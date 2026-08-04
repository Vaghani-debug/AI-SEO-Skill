"""
src/services/analysis_service.py

Deterministic SEO analysis and scoring service.

Responsibility: convert verified SiteEvidence into concrete Finding
objects and a weighted ScoreBreakdown, following the category weights
and severity philosophy defined in docs/SCORING_ENGINE.md and the
audit categories defined in docs/SEO_RULES.md.

Every check here is based on measurable evidence already collected by
crawl_service/extractor_service. No LLM call is made here, so no AI
opinion can influence the score - this keeps an audit's score
reproducible for an unchanged website (SCORING_ENGINE.md Principle 1:
Consistency).

Performance (10% weight) has no MVP-measurable checks yet - no
Lighthouse/Core Web Vitals data is collected - so it always scores 100
in this version rather than penalizing sites for missing evidence
(SCORING_ENGINE.md Principle 5: Fairness). This is a documented MVP
limitation, not an oversight, and should be revisited once real
performance metrics are collected.

Public interface:
    analyze_site(evidence: SiteEvidence) -> ScoreBreakdown
"""

import logging

from src.services.audit_models import (
    CategoryScore,
    EffortLevel,
    Finding,
    PageEvidence,
    ScoreBreakdown,
    Severity,
    SiteEvidence,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category names and weights (docs/SCORING_ENGINE.md Section 4)
# ---------------------------------------------------------------------------

_CATEGORY_TECHNICAL = "Technical SEO"
_CATEGORY_ON_PAGE = "On-Page SEO"
_CATEGORY_CONTENT = "Content Quality"
_CATEGORY_PERFORMANCE = "Performance"
_CATEGORY_ACCESSIBILITY = "Accessibility"
_CATEGORY_SECURITY = "Security"

_CATEGORY_WEIGHTS: dict[str, float] = {
    _CATEGORY_TECHNICAL: 40.0,
    _CATEGORY_ON_PAGE: 25.0,
    _CATEGORY_CONTENT: 15.0,
    _CATEGORY_PERFORMANCE: 10.0,
    _CATEGORY_ACCESSIBILITY: 5.0,
    _CATEGORY_SECURITY: 5.0,
}

# On-page thresholds (standard SEO guidance)
_MIN_TITLE_LENGTH = 10
_MAX_TITLE_LENGTH = 60
_MIN_META_DESCRIPTION_LENGTH = 50
_MAX_META_DESCRIPTION_LENGTH = 160
_THIN_CONTENT_WORD_COUNT = 300

_MAX_EVIDENCE_URLS = 10
# Caps evidence_urls per finding so large sites don't bloat the report


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def analyze_site(evidence: SiteEvidence) -> ScoreBreakdown:
    """
    Run all deterministic checks against the homepage and sampled pages
    and return the full weighted score breakdown.
    """
    pages: list[PageEvidence] = [evidence.homepage, *evidence.sampled_pages]

    findings: list[Finding] = [
        *_check_technical_seo(evidence, pages),
        *_check_on_page_seo(pages),
        *_check_content_quality(pages),
        *_check_accessibility(pages),
        *_check_security(evidence),
    ]

    category_scores = _build_category_scores(findings)
    overall_score = round(
        sum(cs.score * cs.weight_percent for cs in category_scores) / 100.0, 1
    )

    logger.info(
        "analysis_service: base_url=%s overall_score=%.1f findings=%d pages_analyzed=%d",
        evidence.base_url, overall_score, len(findings), len(pages),
    )

    return ScoreBreakdown(
        overall_score=overall_score,
        category_scores=category_scores,
        findings=findings,
    )


# ---------------------------------------------------------------------------
# Score aggregation helpers
# ---------------------------------------------------------------------------


def _build_category_scores(findings: list[Finding]) -> list[CategoryScore]:
    scores: list[CategoryScore] = []
    for category, weight in _CATEGORY_WEIGHTS.items():
        deducted = sum(f.score_deduction for f in findings if f.category == category)
        score = max(0.0, round(100.0 - deducted, 1))
        scores.append(CategoryScore(category=category, weight_percent=weight, score=score))
    return scores


def _proportional_deduction(affected: int, total: int, max_deduction: float) -> float:
    """Scale a check's max deduction by how much of the site it affects."""
    if total <= 0:
        return 0.0
    return round(max_deduction * (affected / total), 2)


def _capped(urls: list[str]) -> list[str]:
    return urls[:_MAX_EVIDENCE_URLS]


# ---------------------------------------------------------------------------
# Technical SEO checks (40%)
# ---------------------------------------------------------------------------


def _check_technical_seo(evidence: SiteEvidence, pages: list[PageEvidence]) -> list[Finding]:
    findings: list[Finding] = []
    homepage = evidence.homepage

    if not homepage.is_https:
        findings.append(Finding(
            category=_CATEGORY_TECHNICAL,
            title="Homepage is not served over HTTPS",
            severity=Severity.CRITICAL,
            description="The homepage was reached over an insecure http:// connection.",
            business_impact="Browsers flag the site as 'Not Secure', damaging trust and conversions, and HTTPS is a confirmed Google ranking signal.",
            recommendation="Install an SSL/TLS certificate and permanently redirect all http:// traffic to https://.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=[homepage.url],
            score_deduction=30.0,
        ))

    if homepage.http_status != 200:
        findings.append(Finding(
            category=_CATEGORY_TECHNICAL,
            title=f"Homepage returned HTTP {homepage.http_status} instead of 200",
            severity=Severity.CRITICAL,
            description=f"The homepage responded with status code {homepage.http_status}.",
            business_impact="Search engines may fail to index the homepage, and visitors following links may see an error instead of the site.",
            recommendation="Investigate server logs and fix whatever is causing the non-200 response on the homepage.",
            effort=EffortLevel.HIGH,
            evidence_urls=[homepage.url],
            score_deduction=30.0,
        ))

    robots = evidence.robots_txt
    if robots is None or not robots.is_accessible:
        findings.append(Finding(
            category=_CATEGORY_TECHNICAL,
            title="robots.txt is missing or inaccessible",
            severity=Severity.HIGH,
            description="robots.txt could not be fetched successfully.",
            business_impact="Without a working robots.txt, crawlers fall back to default behaviour and sitemap discovery via robots.txt is lost.",
            recommendation="Publish a robots.txt file at the site root that returns HTTP 200.",
            effort=EffortLevel.LOW,
            evidence_urls=[f"{evidence.base_url}/robots.txt"],
            score_deduction=15.0,
        ))
    elif robots.blocks_root_path:
        findings.append(Finding(
            category=_CATEGORY_TECHNICAL,
            title="robots.txt blocks the entire site from being crawled",
            severity=Severity.CRITICAL,
            description="robots.txt contains a Disallow rule that blocks the root path for all crawlers.",
            business_impact="Search engines cannot crawl or index any page on the site, effectively removing it from search results.",
            recommendation="Remove the blanket Disallow rule blocking the root path from robots.txt.",
            effort=EffortLevel.LOW,
            evidence_urls=[f"{evidence.base_url}/robots.txt"],
            score_deduction=50.0,
        ))

    if not any(sitemap.is_accessible for sitemap in evidence.sitemaps):
        findings.append(Finding(
            category=_CATEGORY_TECHNICAL,
            title="No accessible XML sitemap was found",
            severity=Severity.HIGH,
            description="None of the sitemap URLs checked during this audit returned a successful response.",
            business_impact="Search engines rely on sitemaps to efficiently discover and prioritize pages, especially on larger sites.",
            recommendation="Publish an XML sitemap and reference it from robots.txt.",
            effort=EffortLevel.LOW,
            evidence_urls=[f"{evidence.base_url}/sitemap.xml"],
            score_deduction=15.0,
        ))

    if not homepage.canonical_url:
        findings.append(Finding(
            category=_CATEGORY_TECHNICAL,
            title="Homepage is missing a canonical tag",
            severity=Severity.MEDIUM,
            description="No <link rel='canonical'> tag was found on the homepage.",
            business_impact="Without a canonical tag, search engines must guess the preferred URL, which can split ranking signals across duplicate or parameterized URLs.",
            recommendation="Add a self-referencing <link rel='canonical'> tag to the homepage <head>.",
            effort=EffortLevel.LOW,
            evidence_urls=[homepage.url],
            score_deduction=10.0,
        ))

    if not any(page.schema_types for page in pages):
        findings.append(Finding(
            category=_CATEGORY_TECHNICAL,
            title="No structured data (schema.org) detected on any crawled page",
            severity=Severity.MEDIUM,
            description="No JSON-LD structured data with a recognizable @type was found on the homepage or sampled pages.",
            business_impact="Structured data helps search engines understand page content and can enable rich results that improve click-through rate.",
            recommendation="Add relevant schema.org JSON-LD markup (e.g. Organization, LocalBusiness, Article, Product) to key pages.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=_capped([page.url for page in pages]),
            score_deduction=10.0,
        ))

    if homepage.meta_robots and "noindex" in homepage.meta_robots.lower():
        findings.append(Finding(
            category=_CATEGORY_TECHNICAL,
            title="Homepage has a noindex directive",
            severity=Severity.CRITICAL,
            description=f"The homepage's meta robots tag is '{homepage.meta_robots}', which includes noindex.",
            business_impact="A noindex homepage is removed from search results entirely, eliminating organic visibility.",
            recommendation="Remove 'noindex' from the homepage's <meta name='robots'> tag unless intentionally hiding it from search engines.",
            effort=EffortLevel.LOW,
            evidence_urls=[homepage.url],
            score_deduction=50.0,
        ))

    return findings


# ---------------------------------------------------------------------------
# On-Page SEO checks (25%)
# ---------------------------------------------------------------------------


def _check_on_page_seo(pages: list[PageEvidence]) -> list[Finding]:
    findings: list[Finding] = []
    total = len(pages)
    if total == 0:
        return findings

    missing_title = [p.url for p in pages if not p.page_title]
    if missing_title:
        findings.append(Finding(
            category=_CATEGORY_ON_PAGE,
            title="Pages are missing a <title> tag",
            severity=Severity.HIGH,
            description=f"{len(missing_title)} of {total} crawled page(s) have no page title.",
            business_impact="Missing titles produce poor, auto-generated search result snippets and hurt click-through rate.",
            recommendation=f"Add a unique, descriptive <title> tag ({_MIN_TITLE_LENGTH}-{_MAX_TITLE_LENGTH} characters) to every page.",
            effort=EffortLevel.LOW,
            evidence_urls=_capped(missing_title),
            score_deduction=_proportional_deduction(len(missing_title), total, 25.0),
        ))

    bad_length_title = [
        p.url for p in pages
        if p.page_title and not (_MIN_TITLE_LENGTH <= len(p.page_title) <= _MAX_TITLE_LENGTH)
    ]
    if bad_length_title:
        findings.append(Finding(
            category=_CATEGORY_ON_PAGE,
            title="Page titles are outside the recommended length",
            severity=Severity.MEDIUM,
            description=f"{len(bad_length_title)} of {total} page(s) have a title shorter than {_MIN_TITLE_LENGTH} or longer than {_MAX_TITLE_LENGTH} characters.",
            business_impact="Titles that are too short waste an opportunity to include relevant keywords; titles that are too long get truncated in search results.",
            recommendation=f"Rewrite affected titles to fall between {_MIN_TITLE_LENGTH} and {_MAX_TITLE_LENGTH} characters.",
            effort=EffortLevel.LOW,
            evidence_urls=_capped(bad_length_title),
            score_deduction=_proportional_deduction(len(bad_length_title), total, 10.0),
        ))

    missing_description = [p.url for p in pages if not p.meta_description]
    if missing_description:
        findings.append(Finding(
            category=_CATEGORY_ON_PAGE,
            title="Pages are missing a meta description",
            severity=Severity.HIGH,
            description=f"{len(missing_description)} of {total} page(s) have no meta description.",
            business_impact="Without a meta description, search engines auto-generate a snippet that is often less persuasive, reducing click-through rate.",
            recommendation=f"Add a unique meta description ({_MIN_META_DESCRIPTION_LENGTH}-{_MAX_META_DESCRIPTION_LENGTH} characters) to every page.",
            effort=EffortLevel.LOW,
            evidence_urls=_capped(missing_description),
            score_deduction=_proportional_deduction(len(missing_description), total, 20.0),
        ))

    bad_length_description = [
        p.url for p in pages
        if p.meta_description
        and not (_MIN_META_DESCRIPTION_LENGTH <= len(p.meta_description) <= _MAX_META_DESCRIPTION_LENGTH)
    ]
    if bad_length_description:
        findings.append(Finding(
            category=_CATEGORY_ON_PAGE,
            title="Meta descriptions are outside the recommended length",
            severity=Severity.MEDIUM,
            description=f"{len(bad_length_description)} of {total} page(s) have a meta description shorter than {_MIN_META_DESCRIPTION_LENGTH} or longer than {_MAX_META_DESCRIPTION_LENGTH} characters.",
            business_impact="Descriptions that are too short under-sell the page; descriptions that are too long get truncated in search results.",
            recommendation=f"Rewrite affected meta descriptions to fall between {_MIN_META_DESCRIPTION_LENGTH} and {_MAX_META_DESCRIPTION_LENGTH} characters.",
            effort=EffortLevel.LOW,
            evidence_urls=_capped(bad_length_description),
            score_deduction=_proportional_deduction(len(bad_length_description), total, 8.0),
        ))

    bad_h1 = [p.url for p in pages if len(p.h1_tags) != 1]
    if bad_h1:
        findings.append(Finding(
            category=_CATEGORY_ON_PAGE,
            title="Pages do not have exactly one H1 heading",
            severity=Severity.HIGH,
            description=f"{len(bad_h1)} of {total} page(s) have zero or multiple <h1> elements.",
            business_impact="A missing or duplicated H1 makes the primary topic of the page ambiguous to both users and search engines.",
            recommendation="Ensure every page has exactly one clear, descriptive <h1> heading.",
            effort=EffortLevel.LOW,
            evidence_urls=_capped(bad_h1),
            score_deduction=_proportional_deduction(len(bad_h1), total, 15.0),
        ))

    no_internal_links = [p.url for p in pages if not p.internal_links]
    if no_internal_links:
        findings.append(Finding(
            category=_CATEGORY_ON_PAGE,
            title="Pages have no internal links",
            severity=Severity.LOW,
            description=f"{len(no_internal_links)} of {total} page(s) have no detected internal links.",
            business_impact="Pages with no internal links receive no link equity from the rest of the site and may be harder for users and crawlers to discover related content from.",
            recommendation="Add contextual internal links to related pages, services, or articles.",
            effort=EffortLevel.LOW,
            evidence_urls=_capped(no_internal_links),
            score_deduction=_proportional_deduction(len(no_internal_links), total, 5.0),
        ))

    return findings


# ---------------------------------------------------------------------------
# Content Quality checks (15%)
# ---------------------------------------------------------------------------


def _check_content_quality(pages: list[PageEvidence]) -> list[Finding]:
    total = len(pages)
    if total == 0:
        return []

    thin_pages = [p.url for p in pages if p.word_count < _THIN_CONTENT_WORD_COUNT]
    if not thin_pages:
        return []

    return [Finding(
        category=_CATEGORY_CONTENT,
        title="Pages have thin content",
        severity=Severity.MEDIUM,
        description=f"{len(thin_pages)} of {total} page(s) have fewer than {_THIN_CONTENT_WORD_COUNT} visible words.",
        business_impact="Thin content pages tend to under-perform in search results and may contribute little unique value for the topics they target.",
        recommendation=f"Expand affected pages to at least {_THIN_CONTENT_WORD_COUNT} words of genuinely useful, unique content.",
        effort=EffortLevel.HIGH,
        evidence_urls=_capped(thin_pages),
        score_deduction=_proportional_deduction(len(thin_pages), total, 20.0),
    )]


# ---------------------------------------------------------------------------
# Accessibility checks (5%)
# ---------------------------------------------------------------------------


def _check_accessibility(pages: list[PageEvidence]) -> list[Finding]:
    findings: list[Finding] = []
    total = len(pages)
    if total == 0:
        return findings

    total_images = sum(len(p.images) for p in pages)
    missing_alt_images = sum(1 for p in pages for img in p.images if not img.has_alt_attribute)
    if total_images > 0 and missing_alt_images > 0:
        affected_pages = [p.url for p in pages if any(not img.has_alt_attribute for img in p.images)]
        findings.append(Finding(
            category=_CATEGORY_ACCESSIBILITY,
            title="Images are missing alt attributes",
            severity=Severity.MEDIUM,
            description=f"{missing_alt_images} of {total_images} image(s) across {len(affected_pages)} page(s) have no alt attribute.",
            business_impact="Missing alt text makes images inaccessible to screen reader users and forfeits an opportunity for image search visibility.",
            recommendation='Add a descriptive alt attribute to every meaningful image; use alt="" only for purely decorative images.',
            effort=EffortLevel.LOW,
            evidence_urls=_capped(affected_pages),
            score_deduction=_proportional_deduction(missing_alt_images, total_images, 15.0),
        ))

    missing_lang = [p.url for p in pages if not p.page_language]
    if missing_lang:
        findings.append(Finding(
            category=_CATEGORY_ACCESSIBILITY,
            title="Pages are missing a language attribute",
            severity=Severity.MEDIUM,
            description=f"{len(missing_lang)} of {total} page(s) have no lang attribute on the <html> element.",
            business_impact="Screen readers rely on the lang attribute to select the correct pronunciation and voice for assistive technology users.",
            recommendation='Add a lang attribute (e.g. lang="en") to the <html> element on every page.',
            effort=EffortLevel.LOW,
            evidence_urls=_capped(missing_lang),
            score_deduction=_proportional_deduction(len(missing_lang), total, 10.0),
        ))

    return findings


# ---------------------------------------------------------------------------
# Security checks (5%)
# ---------------------------------------------------------------------------


def _check_security(evidence: SiteEvidence) -> list[Finding]:
    if evidence.homepage.is_https:
        return []

    return [Finding(
        category=_CATEGORY_SECURITY,
        title="Site is not served over HTTPS",
        severity=Severity.CRITICAL,
        description="The homepage was reached over an insecure http:// connection.",
        business_impact="Unencrypted traffic can be intercepted or modified, and browsers actively warn visitors that the site is 'Not Secure'.",
        recommendation="Install an SSL/TLS certificate and enforce HTTPS site-wide.",
        effort=EffortLevel.MEDIUM,
        evidence_urls=[evidence.homepage.url],
        score_deduction=100.0,
    )]
