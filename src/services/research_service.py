"""
src/services/research_service.py

External research boundary: performs bounded, cited Perplexity searches for
keyword opportunities, organic competitors, competitor strengths/gaps,
authority/off-page opportunities, and either local demand signals (for
local/service-area sites) or audience/market expansion opportunities
(for everyone else). Local/service-area status is classified
deterministically from crawl evidence - never guessed by the LLM.

Every result is normalized into a ResearchClaim (src/services/audit_models.py)
carrying a resolvable source URL, source title, and retrieval date. Any
claim Perplexity returns without a real source_url is dropped rather than
guessed at - this service must never let an uncited numeric claim reach the
report (docs/SEO_RULES.md: recommendations must be evidence based; never
invent competitors).

This module makes network calls to the Perplexity API and must be mocked
in all tests other than an explicit live-integration test.

Public interface:
    classify_local_business(evidence) -> tuple[bool, str | None]
    research_site(site_url, business_summary, settings, is_local_business, city_or_region) -> ResearchBundle
    research_keyword_opportunities(site_url, business_summary, settings) -> list[ResearchClaim]
    research_competitors(site_url, business_summary, settings) -> list[ResearchClaim]
    research_competitor_analysis(site_url, competitor_names, settings) -> list[ResearchClaim]
    research_authority_opportunities(site_url, business_summary, settings) -> list[ResearchClaim]
    research_brand_presence(site_url, business_summary, settings) -> list[ResearchClaim]
    research_local_demand(site_url, business_summary, city_or_region, settings) -> list[ResearchClaim]
    research_audience_expansion(site_url, business_summary, settings) -> list[ResearchClaim]
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from src.config import Settings
from src.services.audit_models import PageType, ResearchBundle, ResearchClaim, SiteEvidence

logger = logging.getLogger(__name__)

_LOCAL_BUSINESS_SCHEMA_TYPES: frozenset[str] = frozenset({
    "LocalBusiness", "Restaurant", "Store", "ProfessionalService",
    "HomeAndConstructionBusiness", "MedicalBusiness", "Attorney", "Dentist",
    "AutoRepair", "BeautySalon", "Plumber", "Electrician", "RealEstateAgent",
    "Hotel", "GroceryStore",
})
# Common schema.org @type values that indicate a physical/service-area business

_PERPLEXITY_BASE_URL = "https://api.perplexity.ai"
_MAX_RESEARCH_TOKENS = 4000
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

    Competitor analysis reuses the competitor names already found by
    research_competitors() rather than searching again. Local demand
    research only runs when is_local_business is True and a region was
    provided; otherwise an audience/market expansion search replaces it,
    so non-local sites never get a thin, unfounded city strategy.
    """
    keyword_opportunities, competitors, authority_opportunities, brand_presence = await asyncio.gather(
        research_keyword_opportunities(site_url, business_summary, settings),
        research_competitors(site_url, business_summary, settings),
        research_authority_opportunities(site_url, business_summary, settings),
        research_brand_presence(site_url, business_summary, settings),
    )

    competitor_names = [claim.value for claim in competitors if claim.value]
    competitor_analysis = await research_competitor_analysis(site_url, competitor_names, settings)

    local_demand: list[ResearchClaim] = []
    audience_expansion: list[ResearchClaim] = []
    if is_local_business and city_or_region:
        local_demand = await research_local_demand(site_url, business_summary, city_or_region, settings)
    else:
        audience_expansion = await research_audience_expansion(site_url, business_summary, settings)

    return ResearchBundle(
        keyword_opportunities=keyword_opportunities,
        competitors=competitors,
        competitor_analysis=competitor_analysis,
        authority_opportunities=authority_opportunities,
        brand_presence=brand_presence,
        local_demand=local_demand,
        audience_expansion=audience_expansion,
    )


# ---------------------------------------------------------------------------
# Public research functions
# ---------------------------------------------------------------------------


async def research_keyword_opportunities(
    site_url: str, business_summary: str, settings: Settings,
) -> list[ResearchClaim]:
    """Bounded search for realistic keyword/search-demand opportunities for this business."""
    system_prompt = (
        "You are an SEO researcher. Find realistic keyword opportunities "
        "(topics or search terms this business could realistically rank for) "
        "with any available search demand or difficulty signal you can cite. "
        + _JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Website: {site_url}\nBusiness summary: {business_summary}"
    claims = _parse_claims(await _call_perplexity_json(system_prompt, user_message, settings))
    logger.info("research_keyword_opportunities: %d claim(s) for %s", len(claims), site_url)
    return claims


async def research_competitors(
    site_url: str, business_summary: str, settings: Settings,
) -> list[ResearchClaim]:
    """Bounded search for 3-5 real organic competitors of this business."""
    system_prompt = (
        "You are an SEO researcher. Identify 3 to 5 real organic search "
        "competitors for this business - actual companies with real "
        "websites, never invented ones. " + _JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Website: {site_url}\nBusiness summary: {business_summary}"
    claims = _parse_claims(await _call_perplexity_json(system_prompt, user_message, settings))
    logger.info("research_competitors: %d claim(s) for %s", len(claims), site_url)
    return claims


async def research_competitor_analysis(
    site_url: str, competitor_names: list[str], settings: Settings,
) -> list[ResearchClaim]:
    """Bounded search for strengths/gaps of already-identified competitors."""
    if not competitor_names:
        return []

    system_prompt = (
        "You are an SEO researcher. For each named competitor, find one "
        "concrete strength or gap relevant to SEO, content, or authority. "
        + _JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Original site: {site_url}\nCompetitors to analyze: {', '.join(competitor_names)}"
    claims = _parse_claims(await _call_perplexity_json(system_prompt, user_message, settings))
    logger.info("research_competitor_analysis: %d claim(s) for %d competitor(s)", len(claims), len(competitor_names))
    return claims


async def research_authority_opportunities(
    site_url: str, business_summary: str, settings: Settings,
) -> list[ResearchClaim]:
    """Bounded search for realistic off-page/authority-building opportunities."""
    system_prompt = (
        "You are an SEO researcher. Find realistic off-page and authority "
        "building opportunities for this business (directories, "
        "publications, partnerships, citation sources) that are genuinely "
        "relevant to its industry. " + _JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Website: {site_url}\nBusiness summary: {business_summary}"
    claims = _parse_claims(await _call_perplexity_json(system_prompt, user_message, settings))
    logger.info("research_authority_opportunities: %d claim(s) for %s", len(claims), site_url)
    return claims


async def research_brand_presence(
    site_url: str, business_summary: str, settings: Settings,
) -> list[ResearchClaim]:
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
    claims = _parse_claims(await _call_perplexity_json(system_prompt, user_message, settings))
    logger.info("research_brand_presence: %d claim(s) for %s", len(claims), site_url)
    return claims


async def research_local_demand(
    site_url: str, business_summary: str, city_or_region: str, settings: Settings,
) -> list[ResearchClaim]:
    """Bounded search for local/service-area demand signals. Only call this for local/service-area businesses."""
    system_prompt = (
        "You are an SEO researcher. Find realistic local search demand "
        "signals for this business in its service area (search interest, "
        "local competition density, or population/market size facts). "
        + _JSON_FORMAT_INSTRUCTIONS
    )
    user_message = (
        f"Website: {site_url}\nBusiness summary: {business_summary}\n"
        f"Service area: {city_or_region}"
    )
    claims = _parse_claims(await _call_perplexity_json(system_prompt, user_message, settings))
    logger.info("research_local_demand: %d claim(s) for %s in %s", len(claims), site_url, city_or_region)
    return claims


async def research_audience_expansion(
    site_url: str, business_summary: str, settings: Settings,
) -> list[ResearchClaim]:
    """Bounded search for new audience segments/markets, for non-local businesses in place of a city strategy."""
    system_prompt = (
        "You are an SEO researcher. Find realistic audience or market "
        "expansion opportunities for this business (adjacent customer "
        "segments, industries, or use cases it could realistically target "
        "next) with any available demand signal you can cite. "
        + _JSON_FORMAT_INSTRUCTIONS
    )
    user_message = f"Website: {site_url}\nBusiness summary: {business_summary}"
    claims = _parse_claims(await _call_perplexity_json(system_prompt, user_message, settings))
    logger.info("research_audience_expansion: %d claim(s) for %s", len(claims), site_url)
    return claims


# ---------------------------------------------------------------------------
# Private Perplexity call + normalization
# ---------------------------------------------------------------------------


async def _call_perplexity_json(system_prompt: str, user_message: str, settings: Settings) -> str:
    """
    Call Perplexity and return the raw response text, or "" on any failure.

    Transient failures (rate limits, timeouts, connection errors, 5xx) are
    retried with exponential backoff, up to settings.perplexity_retry_attempts.
    A real auth/bad-request error is returned immediately since retrying
    would not change the outcome.
    """
    if not settings.perplexity_api_key:
        logger.warning("PERPLEXITY_API_KEY is not configured; skipping external research call.")
        return ""

    client = AsyncOpenAI(api_key=settings.perplexity_api_key, base_url=_PERPLEXITY_BASE_URL)
    retry_attempts = settings.perplexity_retry_attempts
    backoff_base_seconds = settings.perplexity_retry_backoff_base_seconds

    attempt = 1
    while True:
        try:
            response = await client.chat.completions.create(
                model=settings.perplexity_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=_MAX_RESEARCH_TOKENS,
            )
        except (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError) as transient_error:
            if attempt >= retry_attempts:
                logger.error(
                    "Perplexity research call failed after %d attempt(s): %s", attempt, transient_error,
                )
                return ""
            delay = backoff_base_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Transient Perplexity failure (attempt %d/%d), retrying in %.1fs: %s",
                attempt, retry_attempts, delay, transient_error,
            )
            await asyncio.sleep(delay)
            attempt += 1
            continue
        except Exception as research_error:
            logger.error("Perplexity research call failed: %s", research_error)
            return ""

        if not response or not response.choices or not response.choices[0].message.content:
            logger.warning("Perplexity research call returned an empty response")
            return ""

        return response.choices[0].message.content


def _parse_claims(raw_text: str) -> list[ResearchClaim]:
    """
    Parse a Perplexity JSON-array response into ResearchClaim objects.

    Any item missing a real http(s) source_url, or the whole response
    failing to parse as JSON, is discarded rather than guessed at.
    """
    if not raw_text.strip():
        return []

    fence_match = _JSON_FENCE_PATTERN.search(raw_text)
    json_text = fence_match.group(1) if fence_match else raw_text

    try:
        items = json.loads(json_text)
    except json.JSONDecodeError:
        logger.warning("Could not parse research response as JSON; discarding.")
        return []

    if not isinstance(items, list):
        logger.warning("Research response was valid JSON but not a list; discarding.")
        return []

    retrieved_date = datetime.now(timezone.utc).date().isoformat()
    claims: list[ResearchClaim] = []

    for item in items[:_MAX_CLAIMS_PER_QUERY]:
        if not isinstance(item, dict):
            continue

        source_url = str(item.get("source_url", "")).strip()
        if not source_url.startswith(("http://", "https://")):
            logger.warning("Discarding uncited research claim: %s", item.get("claim"))
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

    return claims
