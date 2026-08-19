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

Performance (10% weight) is scored from real Core Web Vitals data collected
from Google PageSpeed Insights (src/services/pagespeed_service.py) when
available. If PSI data could not be collected for a site, the category
scores 100 rather than penalizing sites for missing evidence
(SCORING_ENGINE.md Principle 5: Fairness). Only LCP/CLS/INP are checked
since those are the only Performance metrics with real measured data;
Page Size/Caching/Compression/Render-Blocking Resources remain unmeasured
in this MVP and are not scored.

Public interface:
    analyze_site(evidence: SiteEvidence) -> ScoreBreakdown
    build_page_seo_notes(page: PageReportRow) -> list[str]
    build_homepage_element_rows(homepage: PageReportRow) -> list[tuple[str, str, str, str]]
    build_priority_page_row(page: PageReportRow) -> tuple[str, str, str, str, str]
"""

import logging

from src.services.audit_models import (
    CategoryScore,
    EffortLevel,
    Finding,
    PageEvidence,
    PageReportRow,
    ScoreBreakdown,
    Severity,
    SiteEvidence,
)
from src.services.fetch_service import is_transient_status_code

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

# Core Web Vitals thresholds per Google's published guidance (web.dev/articles/cwv)
_LCP_POOR_MS = 4000.0
_LCP_NEEDS_IMPROVEMENT_MS = 2500.0
_CLS_POOR = 0.25
_CLS_NEEDS_IMPROVEMENT = 0.1
_INP_POOR_MS = 500.0
_INP_NEEDS_IMPROVEMENT_MS = 200.0


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
        *_check_performance(evidence),
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
        findings.append(_homepage_status_finding(homepage))

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

    findings.extend(_check_sampled_page_http_status(evidence.sampled_pages))

    return findings


def _is_unconfirmed_transient_status(page: PageEvidence) -> bool:
    """
    True when a non-200 status is retry-eligible (429/5xx) but was observed only
    once (attempt_count <= 1) and so was never confirmed by a retry. A status
    that isn't retry-eligible at all (e.g. a real 404) is stable regardless of
    attempt count and is never treated as unconfirmed here. Note: used_playwright_fallback
    is deliberately not considered — the browser fallback only ever runs after a
    successful fetch (to render JS-shell pages), so it says nothing about whether
    a non-200 HTTP status is stable and must not be treated as such.
    """
    return is_transient_status_code(page.http_status) and page.attempt_count <= 1


def _homepage_status_finding(homepage: PageEvidence) -> Finding:
    """
    Build the homepage non-200 status Finding, worded by evidence confidence: a
    confirmed failure (retried and still failing, or a status a retry can't fix)
    is a real, urgent finding; a transient-eligible status seen only once is a
    neutral, unconfirmed note rather than an imperative claim.
    """
    if _is_unconfirmed_transient_status(homepage):
        return Finding(
            category=_CATEGORY_TECHNICAL,
            title=f"Homepage returned HTTP {homepage.http_status} on a single, unconfirmed observation",
            severity=Severity.MEDIUM,
            description=(
                f"The homepage responded with status code {homepage.http_status} once; this was not "
                "confirmed by a retry, so it may have been a transient server issue rather than a "
                "persistent problem."
            ),
            business_impact="If this status recurs, search engines may fail to index the homepage and visitors may see an error instead of the site.",
            recommendation="Re-check the homepage's HTTP status; investigate server logs only if it recurs.",
            effort=EffortLevel.LOW,
            evidence_urls=[homepage.url],
            score_deduction=10.0,
        )
    return Finding(
        category=_CATEGORY_TECHNICAL,
        title=f"Homepage returned HTTP {homepage.http_status} instead of 200",
        severity=Severity.CRITICAL,
        description=f"The homepage responded with status code {homepage.http_status}.",
        business_impact="Search engines may fail to index the homepage, and visitors following links may see an error instead of the site.",
        recommendation="Investigate server logs and fix whatever is causing the non-200 response on the homepage.",
        effort=EffortLevel.HIGH,
        evidence_urls=[homepage.url],
        score_deduction=30.0,
    )


def _check_sampled_page_http_status(sampled_pages: list[PageEvidence]) -> list[Finding]:
    """
    Flag sampled pages with a non-200 HTTP status, split by evidence confidence:
    a confirmed failure (retried and still failing, or a status a retry can't
    fix) becomes a real finding; a transient-eligible status observed only once
    becomes a neutral, unconfirmed note and must not claim urgency.
    """
    non_200_pages = [page for page in sampled_pages if page.http_status != 200]
    if not non_200_pages:
        return []

    confirmed = [page for page in non_200_pages if not _is_unconfirmed_transient_status(page)]
    unconfirmed = [page for page in non_200_pages if _is_unconfirmed_transient_status(page)]
    total = len(sampled_pages)

    findings: list[Finding] = []
    if confirmed:
        findings.append(Finding(
            category=_CATEGORY_TECHNICAL,
            title=f"{len(confirmed)} sampled page(s) returned a confirmed non-200 HTTP status",
            severity=Severity.HIGH,
            description="These pages returned a non-200 status that persisted across retries, or a status a retry cannot fix (e.g. 404).",
            business_impact="Search engines may fail to index these pages, and visitors following links may see an error instead of content.",
            recommendation="Investigate server logs, or fix or remove the broken links, for each affected page.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=_capped([page.url for page in confirmed]),
            score_deduction=_proportional_deduction(len(confirmed), total, 20.0),
        ))
    if unconfirmed:
        findings.append(Finding(
            category=_CATEGORY_TECHNICAL,
            title=f"{len(unconfirmed)} sampled page(s) returned a non-200 status on a single, unconfirmed observation",
            severity=Severity.LOW,
            description="These pages returned a retry-eligible status (e.g. 429/5xx) only once; it was not confirmed by a retry, so it may have been a transient server blip.",
            business_impact="If this recurs consistently, it could affect indexing and user experience.",
            recommendation="Re-check these pages; investigate server logs only if the status recurs.",
            effort=EffortLevel.LOW,
            evidence_urls=_capped([page.url for page in unconfirmed]),
            score_deduction=_proportional_deduction(len(unconfirmed), total, 5.0),
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
# Performance checks (10%)
# ---------------------------------------------------------------------------


def _check_performance(evidence: SiteEvidence) -> list[Finding]:
    performance = evidence.performance
    if performance is None or not performance.is_available:
        return []  # No real PageSpeed Insights data collected - never penalize for missing evidence

    findings: list[Finding] = []
    homepage_url = evidence.homepage.url
    source = performance.data_source

    lcp = performance.largest_contentful_paint_ms
    if lcp is not None and lcp > _LCP_POOR_MS:
        findings.append(Finding(
            category=_CATEGORY_PERFORMANCE,
            title="Largest Contentful Paint (LCP) is poor",
            severity=Severity.HIGH,
            description=f"LCP measured {lcp / 1000:.1f}s, above the 4.0s 'poor' threshold ({source} data).",
            business_impact="Slow-loading main content frustrates visitors and is a confirmed Google ranking signal; poor LCP correlates with higher bounce rates.",
            recommendation="Optimize the largest above-the-fold element: compress/resize hero images, preload critical resources, and reduce server response time.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=[homepage_url],
            score_deduction=35.0,
        ))
    elif lcp is not None and lcp > _LCP_NEEDS_IMPROVEMENT_MS:
        findings.append(Finding(
            category=_CATEGORY_PERFORMANCE,
            title="Largest Contentful Paint (LCP) needs improvement",
            severity=Severity.MEDIUM,
            description=f"LCP measured {lcp / 1000:.1f}s, between the 2.5s 'good' and 4.0s 'poor' thresholds ({source} data).",
            business_impact="Borderline load times can still cost conversions on slower connections or devices.",
            recommendation="Optimize the largest above-the-fold element: compress/resize hero images and reduce render-blocking resources.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=[homepage_url],
            score_deduction=15.0,
        ))

    cls = performance.cumulative_layout_shift
    if cls is not None and cls > _CLS_POOR:
        findings.append(Finding(
            category=_CATEGORY_PERFORMANCE,
            title="Cumulative Layout Shift (CLS) is poor",
            severity=Severity.HIGH,
            description=f"CLS measured {cls:.2f}, above the 0.25 'poor' threshold ({source} data).",
            business_impact="Visible layout shifts cause mis-clicks and a jarring experience, directly hurting Google's page experience signals.",
            recommendation="Reserve space for images/ads/embeds with explicit width and height, and avoid injecting content above existing content.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=[homepage_url],
            score_deduction=30.0,
        ))
    elif cls is not None and cls > _CLS_NEEDS_IMPROVEMENT:
        findings.append(Finding(
            category=_CATEGORY_PERFORMANCE,
            title="Cumulative Layout Shift (CLS) needs improvement",
            severity=Severity.MEDIUM,
            description=f"CLS measured {cls:.2f}, between the 0.1 'good' and 0.25 'poor' thresholds ({source} data).",
            business_impact="Noticeable layout shifts can still degrade the perceived quality of the page.",
            recommendation="Reserve space for images/ads/embeds with explicit width and height attributes.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=[homepage_url],
            score_deduction=12.0,
        ))

    inp = performance.interaction_to_next_paint_ms
    if inp is not None and inp > _INP_POOR_MS:
        findings.append(Finding(
            category=_CATEGORY_PERFORMANCE,
            title="Interaction to Next Paint (INP) is poor",
            severity=Severity.HIGH,
            description=f"INP measured {inp:.0f}ms, above the 500ms 'poor' threshold ({source} data).",
            business_impact="Sluggish response to clicks/taps frustrates visitors and is a Core Web Vital used in Google's page experience ranking signal.",
            recommendation="Break up long JavaScript tasks, defer non-critical scripts, and minimize main-thread work triggered by user interactions.",
            effort=EffortLevel.HIGH,
            evidence_urls=[homepage_url],
            score_deduction=25.0,
        ))
    elif inp is not None and inp > _INP_NEEDS_IMPROVEMENT_MS:
        findings.append(Finding(
            category=_CATEGORY_PERFORMANCE,
            title="Interaction to Next Paint (INP) needs improvement",
            severity=Severity.MEDIUM,
            description=f"INP measured {inp:.0f}ms, between the 200ms 'good' and 500ms 'poor' thresholds ({source} data).",
            business_impact="Slightly delayed responsiveness can still be noticeable to visitors on interactive pages.",
            recommendation="Break up long JavaScript tasks and defer non-critical scripts to improve interaction responsiveness.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=[homepage_url],
            score_deduction=10.0,
        ))

    return findings


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
    findings: list[Finding] = []
    homepage = evidence.homepage

    if not homepage.is_https:
        findings.append(Finding(
            category=_CATEGORY_SECURITY,
            title="Site is not served over HTTPS",
            severity=Severity.CRITICAL,
            description="The homepage was reached over an insecure http:// connection.",
            business_impact="Unencrypted traffic can be intercepted or modified, and browsers actively warn visitors that the site is 'Not Secure'.",
            recommendation="Install an SSL/TLS certificate and enforce HTTPS site-wide.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=[homepage.url],
            score_deduction=100.0,
        ))

    headers = evidence.security_headers
    if headers is None:
        return findings  # No response headers were captured for the homepage - nothing further to check

    if homepage.is_https and not headers.has_hsts:
        findings.append(Finding(
            category=_CATEGORY_SECURITY,
            title="Missing Strict-Transport-Security (HSTS) header",
            severity=Severity.MEDIUM,
            description="The homepage response did not include a Strict-Transport-Security header.",
            business_impact="Without HSTS, browsers may still attempt an insecure http:// connection first, leaving an opening for downgrade or man-in-the-middle attacks.",
            recommendation="Add a Strict-Transport-Security header (e.g. 'max-age=31536000; includeSubDomains') to all HTTPS responses.",
            effort=EffortLevel.LOW,
            evidence_urls=[homepage.url],
            score_deduction=15.0,
        ))

    if not headers.has_content_security_policy:
        findings.append(Finding(
            category=_CATEGORY_SECURITY,
            title="Missing Content-Security-Policy header",
            severity=Severity.MEDIUM,
            description="The homepage response did not include a Content-Security-Policy header.",
            business_impact="Without a CSP, the site has no browser-enforced defense against injected scripts if an XSS vulnerability is ever introduced.",
            recommendation="Define a Content-Security-Policy header that restricts script, style, and frame sources to trusted origins.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=[homepage.url],
            score_deduction=10.0,
        ))

    if not headers.has_x_content_type_options:
        findings.append(Finding(
            category=_CATEGORY_SECURITY,
            title="Missing X-Content-Type-Options header",
            severity=Severity.LOW,
            description="The homepage response did not include an X-Content-Type-Options header.",
            business_impact="Without 'nosniff', some browsers may MIME-sniff responses and execute content in unexpected ways.",
            recommendation="Add an 'X-Content-Type-Options: nosniff' header to all responses.",
            effort=EffortLevel.LOW,
            evidence_urls=[homepage.url],
            score_deduction=8.0,
        ))

    if not headers.has_x_frame_options:
        findings.append(Finding(
            category=_CATEGORY_SECURITY,
            title="Missing X-Frame-Options header",
            severity=Severity.MEDIUM,
            description="The homepage response did not include an X-Frame-Options header.",
            business_impact="Without clickjacking protection, the site could be embedded in a malicious iframe to trick visitors into unintended actions.",
            recommendation="Add an 'X-Frame-Options: DENY' or 'SAMEORIGIN' header (or an equivalent CSP frame-ancestors directive).",
            effort=EffortLevel.LOW,
            evidence_urls=[homepage.url],
            score_deduction=12.0,
        ))

    return findings


# ---------------------------------------------------------------------------
# Per-page SEO notes (deterministic report rendering — src/services/report_service.py)
# ---------------------------------------------------------------------------

_SEO_NOTE_COUNT = 3
# MASTER_REPORT_STRUCTURE.md's SEO Notes cells require exactly three bullets per page.


def build_page_seo_notes(page: PageReportRow) -> list[str]:
    """
    Build exactly three deterministic, evidence-backed SEO notes for one
    crawled page. Each of 8 checks — confirmed HTTP/indexability, title,
    description, H1, canonical, content depth, internal links, applicable
    schema — reports whether it found a real issue. Real issues are
    surfaced first, in that priority order, up to three; if fewer than
    three issues exist, the remaining slots are filled (in the same
    priority order) with a confirmation note from the checks that passed,
    so a healthy page still receives three real, evidence-backed notes
    instead of an invented one.

    Only call this for crawled rows (page.was_crawled); sitemap-only rows
    have no verified per-page fields and must never receive page-specific
    SEO notes (MASTER_REPORT_STRUCTURE.md PART 1).
    """
    checks: list[tuple[bool, str]] = [
        _http_indexability_check(page),
        _title_check(page),
        _description_check(page),
        _h1_check(page),
        _canonical_check(page),
        _content_depth_check(page),
        _internal_links_check(page),
        _schema_check(page),
    ]
    issue_notes = [note for is_issue, note in checks if is_issue]
    if len(issue_notes) >= _SEO_NOTE_COUNT:
        return issue_notes[:_SEO_NOTE_COUNT]
    healthy_notes = [note for is_issue, note in checks if not is_issue]
    return (issue_notes + healthy_notes)[:_SEO_NOTE_COUNT]


def _http_indexability_check(page: PageReportRow) -> tuple[bool, str]:
    status = page.http_status
    if status is not None and status != 200:
        if is_transient_status_code(status) and page.attempt_count <= 1:
            return True, f"Returned HTTP {status} on a single, unconfirmed observation — recheck if this recurs."
        return True, f"Returned HTTP {status} instead of 200 (confirmed) — this blocks indexing until fixed."
    if page.meta_robots and "noindex" in page.meta_robots.lower():
        return True, "Has a noindex directive blocking this page from search results."
    return False, "Returns HTTP 200 and is not blocked from indexing."


def _title_check(page: PageReportRow) -> tuple[bool, str]:
    title = page.page_title
    if not title:
        return True, "Missing a <title> tag — add a unique, descriptive title."
    length = len(title)
    if not (_MIN_TITLE_LENGTH <= length <= _MAX_TITLE_LENGTH):
        return True, f"Title tag is {length} characters, outside the recommended {_MIN_TITLE_LENGTH}-{_MAX_TITLE_LENGTH} range."
    return False, f"Title tag length ({length} characters) is within the recommended range."


def _description_check(page: PageReportRow) -> tuple[bool, str]:
    description = page.meta_description
    if not description:
        return True, "Missing a meta description — add a unique, compelling summary."
    length = len(description)
    if not (_MIN_META_DESCRIPTION_LENGTH <= length <= _MAX_META_DESCRIPTION_LENGTH):
        return True, (
            f"Meta description is {length} characters, outside the recommended "
            f"{_MIN_META_DESCRIPTION_LENGTH}-{_MAX_META_DESCRIPTION_LENGTH} range."
        )
    return False, f"Meta description length ({length} characters) is within the recommended range."


def _h1_check(page: PageReportRow) -> tuple[bool, str]:
    count = len(page.h1_tags)
    if count == 0:
        return True, "Missing an H1 heading."
    if count > 1:
        return True, f"Has {count} H1 headings — use exactly one clear H1."
    return False, "Has exactly one clear H1 heading."


def _canonical_check(page: PageReportRow) -> tuple[bool, str]:
    if not page.canonical_url:
        return True, "Missing a canonical tag."
    return False, "Has a canonical tag."


def _content_depth_check(page: PageReportRow) -> tuple[bool, str]:
    word_count = page.word_count or 0
    if word_count < _THIN_CONTENT_WORD_COUNT:
        return True, f"Only {word_count} words of visible content — expand for topical depth."
    return False, f"{word_count} words of visible content meets the recommended depth."


def _internal_links_check(page: PageReportRow) -> tuple[bool, str]:
    count = len(page.internal_links)
    if count == 0:
        return True, "No internal links found on this page."
    return False, f"Has {count} internal link(s) to other pages on the site."


def _schema_check(page: PageReportRow) -> tuple[bool, str]:
    if not page.schema_types:
        return True, "No structured data (schema.org) detected on this page."
    return False, f"Uses {', '.join(page.schema_types)} structured data."


# ---------------------------------------------------------------------------
# Homepage/priority-page element rows (deterministic report rendering — PART 3)
# ---------------------------------------------------------------------------


def _title_element(page: PageReportRow) -> tuple[str, str, str]:
    """Return (current, issue, recommendation) for a page's title tag."""
    title = page.page_title
    if not title:
        return "Missing", "Missing", "Add a unique, descriptive title tag (10-60 characters)."
    length = len(title)
    if not (_MIN_TITLE_LENGTH <= length <= _MAX_TITLE_LENGTH):
        return (
            title,
            f"{length} characters (outside {_MIN_TITLE_LENGTH}-{_MAX_TITLE_LENGTH})",
            f"Rewrite the title to fall within {_MIN_TITLE_LENGTH}-{_MAX_TITLE_LENGTH} characters.",
        )
    return title, "None", "No change needed."


def _description_element(page: PageReportRow) -> tuple[str, str, str]:
    """Return (current, issue, recommendation) for a page's meta description."""
    description = page.meta_description
    if not description:
        return "Missing", "Missing", "Add a unique, compelling meta description (50-160 characters)."
    length = len(description)
    if not (_MIN_META_DESCRIPTION_LENGTH <= length <= _MAX_META_DESCRIPTION_LENGTH):
        return (
            description,
            f"{length} characters (outside {_MIN_META_DESCRIPTION_LENGTH}-{_MAX_META_DESCRIPTION_LENGTH})",
            f"Rewrite the meta description to fall within {_MIN_META_DESCRIPTION_LENGTH}-{_MAX_META_DESCRIPTION_LENGTH} characters.",
        )
    return description, "None", "No change needed."


def _h1_element(page: PageReportRow) -> tuple[str, str, str]:
    """Return (current, issue, recommendation) for a page's H1 heading(s)."""
    count = len(page.h1_tags)
    if count == 0:
        return "Missing", "Missing", "Add exactly one clear H1 heading."
    current = "; ".join(page.h1_tags)
    if count > 1:
        return current, f"{count} H1 headings found", "Use exactly one clear H1 heading."
    return current, "None", "No change needed."


def _canonical_element(page: PageReportRow) -> tuple[str, str, str]:
    """Return (current, issue, recommendation) for a page's canonical tag."""
    if not page.canonical_url:
        return "Missing", "Missing", "Add a self-referencing canonical tag."
    return page.canonical_url, "None", "No change needed."


def build_homepage_element_rows(homepage: PageReportRow) -> list[tuple[str, str, str, str]]:
    """
    Build (element, current, issue, recommendation) rows for PART 3.1's
    Homepage Elements Table, using only verified fields from `homepage`.
    """
    elements: list[tuple[str, tuple[str, str, str]]] = [
        ("Title Tag", _title_element(homepage)),
        ("Meta Description", _description_element(homepage)),
        ("H1 Heading", _h1_element(homepage)),
        ("Canonical Tag", _canonical_element(homepage)),
    ]
    return [(element, current, issue, recommendation) for element, (current, issue, recommendation) in elements]


def build_priority_page_row(page: PageReportRow) -> tuple[str, str, str, str, str]:
    """
    Build (url, title_issue, description_issue, heading_issue, recommendation)
    for one row of PART 3.2's Priority Pages Table. `recommendation` combines
    every non-"None" element recommendation for this page, or states that no
    action is needed when no element issue was found.
    """
    _, title_issue, title_recommendation = _title_element(page)
    _, description_issue, description_recommendation = _description_element(page)
    _, heading_issue, heading_recommendation = _h1_element(page)

    recommendations = [
        recommendation
        for issue, recommendation in (
            (title_issue, title_recommendation),
            (description_issue, description_recommendation),
            (heading_issue, heading_recommendation),
        )
        if issue != "None"
    ]
    recommendation = " ".join(recommendations) if recommendations else "No immediate action needed."
    return page.url, title_issue, description_issue, heading_issue, recommendation
