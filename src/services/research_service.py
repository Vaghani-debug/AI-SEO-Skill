"""
src/services/research_service.py

External research boundary: performs bounded, cited live-web-search
research for keyword opportunities, organic competitors, competitor
strengths/gaps, authority/off-page opportunities, and either local demand
signals (for local/service-area sites) or audience/market expansion
opportunities (for everyone else). Local/service-area status is classified
deterministically from crawl evidence - never guessed by the LLM.

Every result is normalized into a ResearchClaim (src/services/audit_models.py)
carrying a resolvable source URL, source title, and retrieval date. A claim
is discarded unless its self-reported source_url is also one of the real
citations the provider's own web search returned - this service must never
let an uncited (or LLM-fabricated) numeric claim reach the report
(docs/SEO_RULES.md: recommendations must be evidence based; never invent
competitors).

This module calls src.services.llm_service.research_with_web_search(), so
it is provider-agnostic (Perplexity/Gemini/OpenAI, selected via
settings.llm_provider) and must be mocked in all tests other than an
explicit live-integration test.

Every research call returns a ResearchResult (src/services/audit_models.py):
claims plus an explicit ResearchStatus (success, no_results, parse_failed,
citation_failed, or provider_failed) rather than collapsing every failure
mode into an indistinguishable empty list - a genuine zero-result search
must remain distinguishable from a broken provider call or an uncited
response so callers (and report_service.py, in a later step) can describe
research availability honestly instead of implying "no opportunity exists."

A response that fails to parse (malformed JSON) or whose claims cite no
real search result gets exactly one targeted correction retry asking the
provider to fix that specific problem before the failure is accepted as
final. Citation matching itself normalizes only cosmetic URL differences
(scheme/host case, default port, trailing slash, fragment, known tracking
parameters) - it never accepts a model-authored URL merely because it
looks plausible.

Public interface:
    classify_local_business(evidence) -> tuple[bool, str | None]
    research_site(site_url, business_summary, settings, is_local_business, city_or_region) -> ResearchBundle
    research_primary_keywords(site_url, business_summary, settings) -> KeywordResearchResult
    research_long_tail_keywords(site_url, business_summary, settings) -> KeywordResearchResult
    research_competitors(site_url, business_summary, settings) -> CompetitorResearchResult
    research_competitor_analysis(site_url, competitor_names, settings) -> CompetitorGapResult
    research_authority_opportunities(site_url, business_summary, settings) -> ResearchResult
    research_brand_presence(site_url, business_summary, settings) -> ResearchResult
    research_local_demand(site_url, business_summary, city_or_region, settings) -> LocationResearchResult
    research_audience_expansion(site_url, business_summary, settings) -> ResearchResult
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.config import Settings
from src.services.audit_models import (
    CompetitorGap,
    CompetitorGapResult,
    CompetitorOverview,
    CompetitorResearchResult,
    KeywordOpportunity,
    KeywordResearchResult,
    LocationOpportunity,
    LocationResearchResult,
    PageType,
    ResearchBundle,
    ResearchClaim,
    ResearchResult,
    ResearchStatus,
    SiteEvidence,
)
from src.services.llm_service import ResearchCitation, research_with_web_search

logger = logging.getLogger(__name__)

_LOCAL_BUSINESS_SCHEMA_TYPES: frozenset[str] = frozenset({
    "LocalBusiness", "Restaurant", "Store", "ProfessionalService",
    "HomeAndConstructionBusiness", "MedicalBusiness", "Attorney", "Dentist",
    "AutoRepair", "BeautySalon", "Plumber", "Electrician", "RealEstateAgent",
    "Hotel", "GroceryStore",
})
# Common schema.org @type values that indicate a physical/service-area business

_MAX_CLAIMS_PER_QUERY = 8
# Bounded search: keep every research call small, focused, and cheap

_JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_JSON_FORMAT_INSTRUCTIONS = (
    "Respond with ONLY a JSON array (no prose, no markdown fences) of objects "
    'with exactly these keys: "claim" (short label), "value" (the estimate '
    'or fact as text), "source_url" (a real URL you found this from), and '
    '"source_title" (the title of that source). '
    "Every object MUST include a real, resolvable source_url - never invent one. "
    f"Return at most {_MAX_CLAIMS_PER_QUERY} objects. If you cannot find "
    "verifiable information with a source, return an empty array []."
)

_KEYWORD_JSON_FORMAT_INSTRUCTIONS = (
    "Respond with ONLY a JSON array (no prose, no markdown fences) of objects "
    'with exactly these keys: "keyword" (the exact keyword phrase), '
    '"search_intent" (one of: informational, commercial, transactional, navigational), '
    '"estimated_volume" (a sourced monthly search volume estimate as text, e.g. '
    '"1,000-10,000/mo", or null if you cannot cite a real source for a number - '
    "never invent or guess a volume), "
    '"target_page" (the most relevant existing page path on the site for this keyword, '
    'or null if none fits), "source_url" (a real URL you found this from), and '
    '"source_title" (the title of that source). '
    "Every object MUST include a real, resolvable source_url - never invent one. "
    f"Return at most {_MAX_CLAIMS_PER_QUERY} objects. If you cannot find "
    "verifiable information with a source, return an empty array []."
)

_COMPETITOR_JSON_FORMAT_INSTRUCTIONS = (
    "Respond with ONLY a JSON array (no prose, no markdown fences) of objects "
    'with exactly these keys: "competitor_name" (the real company name), '
    '"website" (that competitor\'s real website URL - this MUST be a URL you '
    "actually found via web search, never a guessed domain), "
    '"focus" (what this competitor focuses on, relevant to SEO/content/authority), '
    '"estimated_authority" (a sourced authority signal as short text, e.g. "High", '
    '"Domain Authority ~45", or null if you cannot cite a real source for one - '
    "never invent or guess a number), "
    '"source_url" (a real URL confirming this competitor - may be the same as '
    '"website"), and "source_title" (the title of that source). '
    "Every object MUST include a real, resolvable website AND source_url - never invent either. "
    f"Return at most {_MAX_CLAIMS_PER_QUERY} objects. If you cannot find "
    "verifiable competitors with a source, return an empty array []."
)

_COMPETITOR_GAP_JSON_FORMAT_INSTRUCTIONS = (
    "Respond with ONLY a JSON array (no prose, no markdown fences) of objects "
    'with exactly these keys: "keyword" (a relevant search term), '
    '"competitor_position" (which named competitor has an edge here and how), '
    '"your_gap" (what the original site is missing relative to that competitor), '
    '"source_url" (a real URL you found this from), and "source_title" '
    "(the title of that source). "
    "Every object MUST include a real, resolvable source_url - never invent one. "
    f"Return at most {_MAX_CLAIMS_PER_QUERY} objects. If you cannot find "
    "verifiable gaps with a source, return an empty array []."
)

_LOCATION_JSON_FORMAT_INSTRUCTIONS = (
    "Respond with ONLY a JSON array (no prose, no markdown fences) of objects "
    'with exactly these keys: "primary_keyword" (a location-relevant keyword phrase), '
    '"estimated_volume" (a sourced monthly search volume estimate as text, or null '
    "if you cannot cite a real source for a number - never invent or guess a volume), "
    '"priority" (one of: High, Medium, Low), "source_url" (a real URL you found this '
    'from), and "source_title" (the title of that source). '
    "Every object MUST include a real, resolvable source_url - never invent one. "
    f"Return at most {_MAX_CLAIMS_PER_QUERY} objects. If you cannot find "
    "verifiable information with a source, return an empty array []."
)

_TRACKING_QUERY_PARAMS: frozenset[str] = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref_src",
})
# Stripped during citation-URL normalization; these never change which resource a URL identifies

_CORRECTION_RETRY_STATUSES = frozenset({ResearchStatus.PARSE_FAILED, ResearchStatus.CITATION_FAILED})
# Only these are worth one corrective retry; NO_RESULTS is a legitimate answer, not a mistake to fix

_CORRECTION_INSTRUCTIONS = (
    "Your previous response could not be used: {reason} "
    "Return ONLY a JSON array (no prose, no markdown fences). Every object's "
    '"source_url" must be copied exactly from one of your own web search '
    "results - never invent or guess a URL that merely looks plausible."
)


# ---------------------------------------------------------------------------
# Deterministic local-business classification (no LLM, evidence only)
# ---------------------------------------------------------------------------


def classify_local_business(evidence: SiteEvidence) -> tuple[bool, str | None]:
    """
    Decide whether a site is a local/service-area business using only
    verified crawl evidence (schema.org types and location/service-area
    landing pages) - never an AI guess.

    Returns:
        (is_local_business, city_or_region). city_or_region is a
        best-effort free-text service-area signal (a location page's
        first H1 or its title) or None if no usable signal was found.
    """
    pages = [evidence.homepage, *evidence.sampled_pages]

    has_local_schema = any(
        schema_type in _LOCAL_BUSINESS_SCHEMA_TYPES
        for page in pages
        for schema_type in page.schema_types
    )
    location_pages = [page for page in pages if page.page_type == PageType.LOCATION]

    is_local_business = has_local_schema or bool(location_pages)

    city_or_region: str | None = None
    if location_pages:
        first_location_page = location_pages[0]
        city_or_region = (
            first_location_page.h1_tags[0] if first_location_page.h1_tags else first_location_page.page_title
        )

    return is_local_business, city_or_region


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


async def research_site(
    site_url: str,
    business_summary: str,
    settings: Settings,
    is_local_business: bool = False,
    city_or_region: str | None = None,
) -> ResearchBundle:
    """
    Run every bounded research search for one audit and assemble the
    results into a single ResearchBundle, kept separate from verified
    crawl evidence throughout the pipeline.

    Every category's ResearchResult.status is recorded in
    ResearchBundle.research_statuses so a genuine zero-result search stays
    distinguishable from a provider/parse/citation failure, even though
    both leave that category's claim list empty.

    Competitor analysis reuses the accepted (citation-verified) competitor
    names already found by research_competitors() rather than searching
    again, so a gap row can never be derived from an invented competitor.
    Local demand research only runs when is_local_business is True and a
    region was deterministically detected; when the business is local but no
    region could be found, no search runs at all and the status is recorded
    as INSUFFICIENT_LOCATION_EVIDENCE rather than faking a placeholder
    region. Audience/market expansion only replaces local demand for
    genuinely non-local businesses.
    """
    keyword_result, long_tail_result, competitors_result, authority_result, brand_presence_result = await asyncio.gather(
        research_primary_keywords(site_url, business_summary, settings),
        research_long_tail_keywords(site_url, business_summary, settings),
        research_competitors(site_url, business_summary, settings),
        research_authority_opportunities(site_url, business_summary, settings),
        research_brand_presence(site_url, business_summary, settings),
    )

    competitor_names = [competitor.competitor_name for competitor in competitors_result.competitors]
    competitor_analysis_result = await research_competitor_analysis(site_url, competitor_names, settings)

    local_demand_result: LocationResearchResult | None = None
    audience_expansion_result: ResearchResult | None = None
    if is_local_business and city_or_region:
        local_demand_result = await research_local_demand(site_url, business_summary, city_or_region, settings)
    elif is_local_business:
        local_demand_result = LocationResearchResult(
            status=ResearchStatus.INSUFFICIENT_LOCATION_EVIDENCE,
            error="Business appears local/service-area, but no service region could be determined from crawl evidence.",
        )
    else:
        audience_expansion_result = await research_audience_expansion(site_url, business_summary, settings)

    research_statuses: dict[str, ResearchStatus] = {
        "primary_keywords": keyword_result.status,
        "long_tail_keywords": long_tail_result.status,
        "competitors": competitors_result.status,
        "competitor_analysis": competitor_analysis_result.status,
        "authority_opportunities": authority_result.status,
        "brand_presence": brand_presence_result.status,
    }
    if local_demand_result is not None:
        research_statuses["local_demand"] = local_demand_result.status
    if audience_expansion_result is not None:
        research_statuses["audience_expansion"] = audience_expansion_result.status

    return ResearchBundle(
        primary_keywords=keyword_result.opportunities,
        long_tail_keywords=long_tail_result.opportunities,
        competitors=competitors_result.competitors,
        competitor_analysis=competitor_analysis_result.gaps,
        authority_opportunities=authority_result.claims,
        brand_presence=brand_presence_result.claims,
        local_demand=local_demand_result.opportunities if local_demand_result is not None else [],
        audience_expansion=audience_expansion_result.claims if audience_expansion_result is not None else [],
        research_statuses=research_statuses,
    )


# ---------------------------------------------------------------------------
# Public research functions
# ---------------------------------------------------------------------------


async def research_primary_keywords(
    site_url: str, business_summary: str, settings: Settings,
) -> KeywordResearchResult:
    """Bounded search for PRIMARY (broad, high-intent, head-term) keyword opportunities."""
    system_prompt = (
        "You are an SEO researcher. Find realistic PRIMARY (broad, high-intent, "
        "head-term) keyword opportunities for this business - the main search "
        "terms a customer would use to find this type of business or service. "
        + _KEYWORD_JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Website: {site_url}\nBusiness summary: {business_summary}"
    result = await _run_keyword_query(system_prompt, user_message, settings)
    logger.info(
        "research_primary_keywords: status=%s %d opportunit(y/ies) for %s",
        result.status, len(result.opportunities), site_url,
    )
    return result


async def research_long_tail_keywords(
    site_url: str, business_summary: str, settings: Settings,
) -> KeywordResearchResult:
    """Bounded search for LONG-TAIL (specific, longer, lower-competition) keyword opportunities."""
    system_prompt = (
        "You are an SEO researcher. Find realistic LONG-TAIL keyword "
        "opportunities for this business - specific, longer, lower-competition "
        "search phrases (often 4+ words) that a more qualified customer would "
        "search for. " + _KEYWORD_JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Website: {site_url}\nBusiness summary: {business_summary}"
    result = await _run_keyword_query(system_prompt, user_message, settings)
    logger.info(
        "research_long_tail_keywords: status=%s %d opportunit(y/ies) for %s",
        result.status, len(result.opportunities), site_url,
    )
    return result


async def research_competitors(
    site_url: str, business_summary: str, settings: Settings,
) -> CompetitorResearchResult:
    """Bounded search for 3-5 real organic competitors of this business, each with a citation-verified website."""
    system_prompt = (
        "You are an SEO researcher. Identify 3 to 5 real organic search "
        "competitors for this business - actual companies with real "
        "websites, never invented ones. " + _COMPETITOR_JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Website: {site_url}\nBusiness summary: {business_summary}"
    result = await _run_competitor_query(system_prompt, user_message, settings)
    logger.info(
        "research_competitors: status=%s %d competitor(s) for %s", result.status, len(result.competitors), site_url,
    )
    return result


async def research_competitor_analysis(
    site_url: str, competitor_names: list[str], settings: Settings,
) -> CompetitorGapResult:
    """Bounded search for strengths/gaps of already-accepted (citation-verified) competitors only."""
    if not competitor_names:
        return CompetitorGapResult(status=ResearchStatus.NO_RESULTS, error="No competitors were identified to analyze.")

    system_prompt = (
        "You are an SEO researcher. For each named competitor, find one "
        "concrete strength or gap relevant to SEO, content, or authority. "
        + _COMPETITOR_GAP_JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Original site: {site_url}\nCompetitors to analyze: {', '.join(competitor_names)}"
    result = await _run_competitor_gap_query(system_prompt, user_message, settings)
    logger.info(
        "research_competitor_analysis: status=%s %d gap(s) for %d competitor(s)",
        result.status, len(result.gaps), len(competitor_names),
    )
    return result


async def research_authority_opportunities(
    site_url: str, business_summary: str, settings: Settings,
) -> ResearchResult:
    """Bounded search for realistic off-page/authority-building opportunities."""
    system_prompt = (
        "You are an SEO researcher. Find realistic off-page and authority "
        "building opportunities for this business (directories, "
        "publications, partnerships, citation sources) that are genuinely "
        "relevant to its industry. " + _JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Website: {site_url}\nBusiness summary: {business_summary}"
    result = await _run_research_query(system_prompt, user_message, settings)
    logger.info(
        "research_authority_opportunities: status=%s %d claim(s) for %s", result.status, len(result.claims), site_url,
    )
    return result


async def research_brand_presence(
    site_url: str, business_summary: str, settings: Settings,
) -> ResearchResult:
    """
    Bounded search for where this brand is already visible online today
    (business directories, social profiles, press mentions, review sites).

    Covers SEO_RULES.md Section 5's required "Brand Presence" check. Domain
    Authority and Basic Backlink Summary are marked optional in that same
    section and are intentionally not implemented - no free, verified data
    source exists for either, and an LLM guess at a specific number would
    violate this report's "never invent backlinks" rule.
    """
    system_prompt = (
        "You are an SEO researcher. Find real, existing evidence of this "
        "brand's current online presence - business directory listings, "
        "social media profiles, review sites, or press mentions you can "
        "actually verify exist. Do not suggest opportunities to pursue; "
        "only report presence that already exists today. " + _JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Website: {site_url}\nBusiness summary: {business_summary}"
    result = await _run_research_query(system_prompt, user_message, settings)
    logger.info("research_brand_presence: status=%s %d claim(s) for %s", result.status, len(result.claims), site_url)
    return result


async def research_local_demand(
    site_url: str, business_summary: str, city_or_region: str, settings: Settings,
) -> LocationResearchResult:
    """Bounded search for local/service-area demand signals. Only call this for local/service-area businesses."""
    system_prompt = (
        "You are an SEO researcher. Find realistic local search demand "
        "signals for this business in its service area (search interest, "
        "local competition density, or population/market size facts). "
        + _LOCATION_JSON_FORMAT_INSTRUCTIONS
    )
    user_message = (
        f"Website: {site_url}\nBusiness summary: {business_summary}\n"
        f"Service area: {city_or_region}"
    )
    result = await _run_location_query(system_prompt, user_message, city_or_region, settings)
    logger.info(
        "research_local_demand: status=%s %d opportunit(y/ies) for %s in %s",
        result.status, len(result.opportunities), site_url, city_or_region,
    )
    return result


async def research_audience_expansion(
    site_url: str, business_summary: str, settings: Settings,
) -> ResearchResult:
    """Bounded search for new audience segments/markets, for non-local businesses in place of a city strategy."""
    system_prompt = (
        "You are an SEO researcher. Find realistic audience or market "
        "expansion opportunities for this business (adjacent customer "
        "segments, industries, or use cases it could realistically target "
        "next) with any available demand signal you can cite. "
        + _JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Website: {site_url}\nBusiness summary: {business_summary}"
    result = await _run_research_query(system_prompt, user_message, settings)
    logger.info(
        "research_audience_expansion: status=%s %d claim(s) for %s", result.status, len(result.claims), site_url,
    )
    return result


# ---------------------------------------------------------------------------
# Private provider-agnostic research call + normalization
# ---------------------------------------------------------------------------


async def _run_research_query(system_prompt: str, user_message: str, settings: Settings) -> ResearchResult:
    """
    Run one live-web-search research call via llm_service and normalize its
    outcome into a ResearchResult — research is best-effort and must never
    raise/abort an audit, but a provider exception (missing key, network
    error, unusable response) is recorded as PROVIDER_FAILED rather than
    being silently indistinguishable from a genuine zero-result search.
    Retry/backoff for transient provider failures already happens inside
    research_with_web_search(), so any exception seen here is final.

    A PARSE_FAILED or CITATION_FAILED result gets exactly one targeted
    correction retry, telling the provider specifically what was wrong, before
    the failure is accepted as final - never more than one, and the retry's
    own claims are validated by the same citation check as the first attempt.
    """
    try:
        research_response = await research_with_web_search(system_prompt, user_message, settings)
    except Exception as research_error:
        logger.error("Research call failed: %s", research_error)
        return ResearchResult(status=ResearchStatus.PROVIDER_FAILED, error=str(research_error))

    result = _parse_claims(research_response.text, research_response.citations)
    if result.status not in _CORRECTION_RETRY_STATUSES:
        return result

    logger.warning("Research response needs correction (%s); retrying once.", result.status.value)
    correction_message = user_message + "\n\n" + _CORRECTION_INSTRUCTIONS.format(
        reason=result.error or "The response was invalid.",
    )
    try:
        retry_response = await research_with_web_search(system_prompt, correction_message, settings)
    except Exception as research_error:
        logger.error("Research correction retry failed: %s", research_error)
        return result  # the original parse/citation failure is more informative than masking it as PROVIDER_FAILED

    return _parse_claims(retry_response.text, retry_response.citations)


async def _run_keyword_query(system_prompt: str, user_message: str, settings: Settings) -> KeywordResearchResult:
    """
    Typed-keyword counterpart to _run_research_query(): same provider-exception
    handling and one-shot correction retry for PARSE_FAILED/CITATION_FAILED,
    but parses into KeywordOpportunity rows via _parse_keyword_opportunities()
    instead of generic ResearchClaim rows.
    """
    try:
        research_response = await research_with_web_search(system_prompt, user_message, settings)
    except Exception as research_error:
        logger.error("Keyword research call failed: %s", research_error)
        return KeywordResearchResult(status=ResearchStatus.PROVIDER_FAILED, error=str(research_error))

    result = _parse_keyword_opportunities(research_response.text, research_response.citations)
    if result.status not in _CORRECTION_RETRY_STATUSES:
        return result

    logger.warning("Keyword research response needs correction (%s); retrying once.", result.status.value)
    correction_message = user_message + "\n\n" + _CORRECTION_INSTRUCTIONS.format(
        reason=result.error or "The response was invalid.",
    )
    try:
        retry_response = await research_with_web_search(system_prompt, correction_message, settings)
    except Exception as research_error:
        logger.error("Keyword research correction retry failed: %s", research_error)
        return result  # the original parse/citation failure is more informative than masking it as PROVIDER_FAILED

    return _parse_keyword_opportunities(retry_response.text, retry_response.citations)


async def _run_competitor_query(system_prompt: str, user_message: str, settings: Settings) -> CompetitorResearchResult:
    """
    Typed-competitor counterpart to _run_research_query(): same provider-exception
    handling and one-shot correction retry, but parses into CompetitorOverview
    rows via _parse_competitors(), each requiring a citation-verified website.
    """
    try:
        research_response = await research_with_web_search(system_prompt, user_message, settings)
    except Exception as research_error:
        logger.error("Competitor research call failed: %s", research_error)
        return CompetitorResearchResult(status=ResearchStatus.PROVIDER_FAILED, error=str(research_error))

    result = _parse_competitors(research_response.text, research_response.citations)
    if result.status not in _CORRECTION_RETRY_STATUSES:
        return result

    logger.warning("Competitor research response needs correction (%s); retrying once.", result.status.value)
    correction_message = user_message + "\n\n" + _CORRECTION_INSTRUCTIONS.format(
        reason=result.error or "The response was invalid.",
    )
    try:
        retry_response = await research_with_web_search(system_prompt, correction_message, settings)
    except Exception as research_error:
        logger.error("Competitor research correction retry failed: %s", research_error)
        return result  # the original parse/citation failure is more informative than masking it as PROVIDER_FAILED

    return _parse_competitors(retry_response.text, retry_response.citations)


async def _run_competitor_gap_query(system_prompt: str, user_message: str, settings: Settings) -> CompetitorGapResult:
    """
    Typed-competitor-gap counterpart to _run_research_query(): same
    provider-exception handling and one-shot correction retry, but parses
    into CompetitorGap rows via _parse_competitor_gaps().
    """
    try:
        research_response = await research_with_web_search(system_prompt, user_message, settings)
    except Exception as research_error:
        logger.error("Competitor gap research call failed: %s", research_error)
        return CompetitorGapResult(status=ResearchStatus.PROVIDER_FAILED, error=str(research_error))

    result = _parse_competitor_gaps(research_response.text, research_response.citations)
    if result.status not in _CORRECTION_RETRY_STATUSES:
        return result

    logger.warning("Competitor gap research response needs correction (%s); retrying once.", result.status.value)
    correction_message = user_message + "\n\n" + _CORRECTION_INSTRUCTIONS.format(
        reason=result.error or "The response was invalid.",
    )
    try:
        retry_response = await research_with_web_search(system_prompt, correction_message, settings)
    except Exception as research_error:
        logger.error("Competitor gap research correction retry failed: %s", research_error)
        return result  # the original parse/citation failure is more informative than masking it as PROVIDER_FAILED

    return _parse_competitor_gaps(retry_response.text, retry_response.citations)


async def _run_location_query(
    system_prompt: str, user_message: str, city_or_region: str, settings: Settings,
) -> LocationResearchResult:
    """
    Typed-location counterpart to _run_research_query(): same provider-exception
    handling and one-shot correction retry, but parses into LocationOpportunity
    rows via _parse_location_opportunities(). city_or_region is threaded through
    from the deterministic caller, never taken from the LLM response.
    """
    try:
        research_response = await research_with_web_search(system_prompt, user_message, settings)
    except Exception as research_error:
        logger.error("Location research call failed: %s", research_error)
        return LocationResearchResult(status=ResearchStatus.PROVIDER_FAILED, error=str(research_error))

    result = _parse_location_opportunities(research_response.text, research_response.citations, city_or_region)
    if result.status not in _CORRECTION_RETRY_STATUSES:
        return result

    logger.warning("Location research response needs correction (%s); retrying once.", result.status.value)
    correction_message = user_message + "\n\n" + _CORRECTION_INSTRUCTIONS.format(
        reason=result.error or "The response was invalid.",
    )
    try:
        retry_response = await research_with_web_search(system_prompt, correction_message, settings)
    except Exception as research_error:
        logger.error("Location research correction retry failed: %s", research_error)
        return result  # the original parse/citation failure is more informative than masking it as PROVIDER_FAILED

    return _parse_location_opportunities(retry_response.text, retry_response.citations, city_or_region)


def _normalize_url(url: str) -> str:
    """
    Normalize a URL for citation matching: lowercase scheme/host, drop a
    default port, fragment, and known tracking query parameters, and ignore
    a trailing slash - so a claim's source_url can match a citation URL that
    differs only cosmetically, without weakening the requirement that both
    identify the same resource. Path case is preserved since paths are
    case-sensitive on the server.
    """
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    default_port = {"http": 80, "https": 443}.get(scheme)
    netloc = hostname if parsed.port in (None, default_port) else f"{hostname}:{parsed.port}"

    kept_query_pairs = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_PARAMS
    ]

    return urlunsplit((scheme, netloc, parsed.path.rstrip("/"), urlencode(kept_query_pairs), ""))


def _parse_claims(raw_text: str, citations: list[ResearchCitation]) -> ResearchResult:
    """
    Parse a provider JSON-array response into a ResearchResult, distinguishing
    exactly why zero claims were returned rather than collapsing every case
    into an empty list:
      - NO_RESULTS: the provider legitimately reported nothing (blank text or `[]`).
      - PARSE_FAILED: the response was not valid JSON, or not a JSON list.
      - CITATION_FAILED: valid items existed but none had a source_url matching
        one of the provider's own search citations — self-reported LLM text
        alone is never trusted.
      - SUCCESS: at least one claim was accepted.
    """
    if not raw_text.strip():
        return ResearchResult(status=ResearchStatus.NO_RESULTS, error="Provider returned an empty response.")

    fence_match = _JSON_FENCE_PATTERN.search(raw_text)
    json_text = fence_match.group(1) if fence_match else raw_text

    try:
        items = json.loads(json_text)
    except json.JSONDecodeError as decode_error:
        logger.warning("Could not parse research response as JSON; discarding.")
        return ResearchResult(status=ResearchStatus.PARSE_FAILED, error=f"Invalid JSON response: {decode_error}")

    if not isinstance(items, list):
        logger.warning("Research response was valid JSON but not a list; discarding.")
        return ResearchResult(status=ResearchStatus.PARSE_FAILED, error="Response JSON was not a list.")

    if not items:
        return ResearchResult(status=ResearchStatus.NO_RESULTS)

    # Real, provider-verified sources — every claim's source_url must appear here to be kept
    cited_urls = {_normalize_url(citation.url) for citation in citations}
    if not cited_urls:
        logger.warning("Provider returned no search citations; all claims in this response will be discarded.")

    retrieved_date = datetime.now(timezone.utc).date().isoformat()
    claims: list[ResearchClaim] = []
    discarded_for_citation = 0

    for item in items[:_MAX_CLAIMS_PER_QUERY]:
        if not isinstance(item, dict):
            continue

        source_url = str(item.get("source_url", "")).strip()
        if not source_url.lower().startswith(("http://", "https://")):
            logger.warning("Discarding uncited research claim: %s", item.get("claim"))
            discarded_for_citation += 1
            continue

        if _normalize_url(source_url) not in cited_urls:
            logger.warning("Discarding research claim not backed by a provider search citation: %s", item.get("claim"))
            discarded_for_citation += 1
            continue

        claim_text = str(item.get("claim", "")).strip()
        value_text = str(item.get("value", "")).strip()
        if not claim_text or not value_text:
            continue

        claims.append(ResearchClaim(
            claim=claim_text,
            value=value_text,
            source_url=source_url,
            source_title=str(item.get("source_title", "")).strip() or source_url,
            retrieved_date=retrieved_date,
        ))

    if claims:
        return ResearchResult(status=ResearchStatus.SUCCESS, claims=claims)

    if discarded_for_citation:
        return ResearchResult(
            status=ResearchStatus.CITATION_FAILED,
            error="No claim's source_url matched a provider search citation.",
        )

    # Every item was structurally present but missing required claim/value text.
    return ResearchResult(status=ResearchStatus.PARSE_FAILED, error="Response items were missing required fields.")


def _parse_keyword_opportunities(raw_text: str, citations: list[ResearchCitation]) -> KeywordResearchResult:
    """
    Typed-keyword counterpart to _parse_claims(): same NO_RESULTS/PARSE_FAILED/
    CITATION_FAILED/SUCCESS distinctions and the same mandatory-citation check,
    but produces KeywordOpportunity rows with estimated_volume/target_page left
    as None (never fabricated) whenever the provider did not supply them.
    """
    if not raw_text.strip():
        return KeywordResearchResult(status=ResearchStatus.NO_RESULTS, error="Provider returned an empty response.")

    fence_match = _JSON_FENCE_PATTERN.search(raw_text)
    json_text = fence_match.group(1) if fence_match else raw_text

    try:
        items = json.loads(json_text)
    except json.JSONDecodeError as decode_error:
        logger.warning("Could not parse keyword research response as JSON; discarding.")
        return KeywordResearchResult(status=ResearchStatus.PARSE_FAILED, error=f"Invalid JSON response: {decode_error}")

    if not isinstance(items, list):
        logger.warning("Keyword research response was valid JSON but not a list; discarding.")
        return KeywordResearchResult(status=ResearchStatus.PARSE_FAILED, error="Response JSON was not a list.")

    if not items:
        return KeywordResearchResult(status=ResearchStatus.NO_RESULTS)

    # Real, provider-verified sources — every opportunity's source_url must appear here to be kept
    cited_urls = {_normalize_url(citation.url) for citation in citations}
    if not cited_urls:
        logger.warning("Provider returned no search citations; all keyword opportunities in this response will be discarded.")

    retrieved_date = datetime.now(timezone.utc).date().isoformat()
    opportunities: list[KeywordOpportunity] = []
    discarded_for_citation = 0

    for item in items[:_MAX_CLAIMS_PER_QUERY]:
        if not isinstance(item, dict):
            continue

        source_url = str(item.get("source_url", "")).strip()
        if not source_url.lower().startswith(("http://", "https://")):
            logger.warning("Discarding uncited keyword opportunity: %s", item.get("keyword"))
            discarded_for_citation += 1
            continue

        if _normalize_url(source_url) not in cited_urls:
            logger.warning("Discarding keyword opportunity not backed by a provider search citation: %s", item.get("keyword"))
            discarded_for_citation += 1
            continue

        keyword_text = str(item.get("keyword", "")).strip()
        search_intent_text = str(item.get("search_intent", "")).strip()
        if not keyword_text or not search_intent_text:
            continue

        raw_volume = item.get("estimated_volume")
        estimated_volume = str(raw_volume).strip() if raw_volume not in (None, "") else None

        raw_target_page = item.get("target_page")
        target_page = str(raw_target_page).strip() if raw_target_page not in (None, "") else None

        opportunities.append(KeywordOpportunity(
            keyword=keyword_text,
            search_intent=search_intent_text,
            source_url=source_url,
            source_title=str(item.get("source_title", "")).strip() or source_url,
            retrieved_date=retrieved_date,
            estimated_volume=estimated_volume,
            target_page=target_page,
        ))

    if opportunities:
        return KeywordResearchResult(status=ResearchStatus.SUCCESS, opportunities=opportunities)

    if discarded_for_citation:
        return KeywordResearchResult(
            status=ResearchStatus.CITATION_FAILED,
            error="No opportunity's source_url matched a provider search citation.",
        )

    # Every item was structurally present but missing required keyword/search_intent text.
    return KeywordResearchResult(status=ResearchStatus.PARSE_FAILED, error="Response items were missing required fields.")


def _parse_competitors(raw_text: str, citations: list[ResearchCitation]) -> CompetitorResearchResult:
    """
    Typed-competitor counterpart to _parse_claims(): same NO_RESULTS/PARSE_FAILED/
    CITATION_FAILED/SUCCESS distinctions, but the citation check is enforced on
    the competitor's website field itself - a competitor is only ever accepted
    when its website is a real URL matching one of the provider's own search
    citations, never invented or self-reported text alone.
    """
    if not raw_text.strip():
        return CompetitorResearchResult(status=ResearchStatus.NO_RESULTS, error="Provider returned an empty response.")

    fence_match = _JSON_FENCE_PATTERN.search(raw_text)
    json_text = fence_match.group(1) if fence_match else raw_text

    try:
        items = json.loads(json_text)
    except json.JSONDecodeError as decode_error:
        logger.warning("Could not parse competitor research response as JSON; discarding.")
        return CompetitorResearchResult(status=ResearchStatus.PARSE_FAILED, error=f"Invalid JSON response: {decode_error}")

    if not isinstance(items, list):
        logger.warning("Competitor research response was valid JSON but not a list; discarding.")
        return CompetitorResearchResult(status=ResearchStatus.PARSE_FAILED, error="Response JSON was not a list.")

    if not items:
        return CompetitorResearchResult(status=ResearchStatus.NO_RESULTS)

    # Real, provider-verified sources — every competitor's website must appear here to be kept
    cited_urls = {_normalize_url(citation.url) for citation in citations}
    if not cited_urls:
        logger.warning("Provider returned no search citations; all competitors in this response will be discarded.")

    retrieved_date = datetime.now(timezone.utc).date().isoformat()
    competitors: list[CompetitorOverview] = []
    discarded_for_citation = 0

    for item in items[:_MAX_CLAIMS_PER_QUERY]:
        if not isinstance(item, dict):
            continue

        website = str(item.get("website", "")).strip()
        if not website.lower().startswith(("http://", "https://")):
            logger.warning("Discarding uncited competitor: %s", item.get("competitor_name"))
            discarded_for_citation += 1
            continue

        if _normalize_url(website) not in cited_urls:
            logger.warning(
                "Discarding competitor not backed by a provider search citation: %s", item.get("competitor_name"),
            )
            discarded_for_citation += 1
            continue

        competitor_name = str(item.get("competitor_name", "")).strip()
        focus_text = str(item.get("focus", "")).strip()
        if not competitor_name or not focus_text:
            continue

        raw_authority = item.get("estimated_authority")
        estimated_authority = str(raw_authority).strip() if raw_authority not in (None, "") else None

        source_url = str(item.get("source_url", "")).strip() or website

        competitors.append(CompetitorOverview(
            competitor_name=competitor_name,
            website=website,
            focus=focus_text,
            source_url=source_url,
            source_title=str(item.get("source_title", "")).strip() or source_url,
            retrieved_date=retrieved_date,
            estimated_authority=estimated_authority,
        ))

    if competitors:
        return CompetitorResearchResult(status=ResearchStatus.SUCCESS, competitors=competitors)

    if discarded_for_citation:
        return CompetitorResearchResult(
            status=ResearchStatus.CITATION_FAILED,
            error="No competitor's website matched a provider search citation.",
        )

    # Every item was structurally present but missing required competitor_name/focus text.
    return CompetitorResearchResult(status=ResearchStatus.PARSE_FAILED, error="Response items were missing required fields.")


def _parse_competitor_gaps(raw_text: str, citations: list[ResearchCitation]) -> CompetitorGapResult:
    """
    Typed-competitor-gap counterpart to _parse_claims(): same NO_RESULTS/
    PARSE_FAILED/CITATION_FAILED/SUCCESS distinctions and the same mandatory
    source_url citation check, producing CompetitorGap rows.
    """
    if not raw_text.strip():
        return CompetitorGapResult(status=ResearchStatus.NO_RESULTS, error="Provider returned an empty response.")

    fence_match = _JSON_FENCE_PATTERN.search(raw_text)
    json_text = fence_match.group(1) if fence_match else raw_text

    try:
        items = json.loads(json_text)
    except json.JSONDecodeError as decode_error:
        logger.warning("Could not parse competitor gap research response as JSON; discarding.")
        return CompetitorGapResult(status=ResearchStatus.PARSE_FAILED, error=f"Invalid JSON response: {decode_error}")

    if not isinstance(items, list):
        logger.warning("Competitor gap research response was valid JSON but not a list; discarding.")
        return CompetitorGapResult(status=ResearchStatus.PARSE_FAILED, error="Response JSON was not a list.")

    if not items:
        return CompetitorGapResult(status=ResearchStatus.NO_RESULTS)

    # Real, provider-verified sources — every gap's source_url must appear here to be kept
    cited_urls = {_normalize_url(citation.url) for citation in citations}
    if not cited_urls:
        logger.warning("Provider returned no search citations; all competitor gaps in this response will be discarded.")

    retrieved_date = datetime.now(timezone.utc).date().isoformat()
    gaps: list[CompetitorGap] = []
    discarded_for_citation = 0

    for item in items[:_MAX_CLAIMS_PER_QUERY]:
        if not isinstance(item, dict):
            continue

        source_url = str(item.get("source_url", "")).strip()
        if not source_url.lower().startswith(("http://", "https://")):
            logger.warning("Discarding uncited competitor gap: %s", item.get("keyword"))
            discarded_for_citation += 1
            continue

        if _normalize_url(source_url) not in cited_urls:
            logger.warning("Discarding competitor gap not backed by a provider search citation: %s", item.get("keyword"))
            discarded_for_citation += 1
            continue

        keyword_text = str(item.get("keyword", "")).strip()
        competitor_position_text = str(item.get("competitor_position", "")).strip()
        your_gap_text = str(item.get("your_gap", "")).strip()
        if not keyword_text or not competitor_position_text or not your_gap_text:
            continue

        gaps.append(CompetitorGap(
            keyword=keyword_text,
            competitor_position=competitor_position_text,
            your_gap=your_gap_text,
            source_url=source_url,
            source_title=str(item.get("source_title", "")).strip() or source_url,
            retrieved_date=retrieved_date,
        ))

    if gaps:
        return CompetitorGapResult(status=ResearchStatus.SUCCESS, gaps=gaps)

    if discarded_for_citation:
        return CompetitorGapResult(
            status=ResearchStatus.CITATION_FAILED,
            error="No gap's source_url matched a provider search citation.",
        )

    # Every item was structurally present but missing required keyword/competitor_position/your_gap text.
    return CompetitorGapResult(status=ResearchStatus.PARSE_FAILED, error="Response items were missing required fields.")


def _parse_location_opportunities(
    raw_text: str, citations: list[ResearchCitation], city_or_region: str,
) -> LocationResearchResult:
    """
    Typed-location counterpart to _parse_claims(): same NO_RESULTS/PARSE_FAILED/
    CITATION_FAILED/SUCCESS distinctions and the same mandatory source_url
    citation check. city_or_region is never read from the provider response -
    it is always the deterministic value supplied by the caller, so a location
    row can never carry a region invented by the LLM.
    """
    if not raw_text.strip():
        return LocationResearchResult(status=ResearchStatus.NO_RESULTS, error="Provider returned an empty response.")

    fence_match = _JSON_FENCE_PATTERN.search(raw_text)
    json_text = fence_match.group(1) if fence_match else raw_text

    try:
        items = json.loads(json_text)
    except json.JSONDecodeError as decode_error:
        logger.warning("Could not parse location research response as JSON; discarding.")
        return LocationResearchResult(status=ResearchStatus.PARSE_FAILED, error=f"Invalid JSON response: {decode_error}")

    if not isinstance(items, list):
        logger.warning("Location research response was valid JSON but not a list; discarding.")
        return LocationResearchResult(status=ResearchStatus.PARSE_FAILED, error="Response JSON was not a list.")

    if not items:
        return LocationResearchResult(status=ResearchStatus.NO_RESULTS)

    # Real, provider-verified sources — every opportunity's source_url must appear here to be kept
    cited_urls = {_normalize_url(citation.url) for citation in citations}
    if not cited_urls:
        logger.warning("Provider returned no search citations; all location opportunities in this response will be discarded.")

    retrieved_date = datetime.now(timezone.utc).date().isoformat()
    opportunities: list[LocationOpportunity] = []
    discarded_for_citation = 0

    for item in items[:_MAX_CLAIMS_PER_QUERY]:
        if not isinstance(item, dict):
            continue

        source_url = str(item.get("source_url", "")).strip()
        if not source_url.lower().startswith(("http://", "https://")):
            logger.warning("Discarding uncited location opportunity: %s", item.get("primary_keyword"))
            discarded_for_citation += 1
            continue

        if _normalize_url(source_url) not in cited_urls:
            logger.warning(
                "Discarding location opportunity not backed by a provider search citation: %s", item.get("primary_keyword"),
            )
            discarded_for_citation += 1
            continue

        primary_keyword_text = str(item.get("primary_keyword", "")).strip()
        priority_text = str(item.get("priority", "")).strip()
        if not primary_keyword_text or not priority_text:
            continue

        raw_volume = item.get("estimated_volume")
        estimated_volume = str(raw_volume).strip() if raw_volume not in (None, "") else None

        opportunities.append(LocationOpportunity(
            city_or_region=city_or_region,
            primary_keyword=primary_keyword_text,
            priority=priority_text,
            source_url=source_url,
            source_title=str(item.get("source_title", "")).strip() or source_url,
            retrieved_date=retrieved_date,
            estimated_volume=estimated_volume,
        ))

    if opportunities:
        return LocationResearchResult(status=ResearchStatus.SUCCESS, opportunities=opportunities)

    if discarded_for_citation:
        return LocationResearchResult(
            status=ResearchStatus.CITATION_FAILED,
            error="No opportunity's source_url matched a provider search citation.",
        )

    # Every item was structurally present but missing required primary_keyword/priority text.
    return LocationResearchResult(status=ResearchStatus.PARSE_FAILED, error="Response items were missing required fields.")
