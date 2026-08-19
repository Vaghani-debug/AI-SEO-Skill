"""
test/fixtures/frozen_audit_context.py

A single, frozen, anonymized AuditContext fixture (Step 18 of the
"Evidence-First New Report Pipeline" plan) used by regression and
provider-parity tests that need one realistic, representative audit
rather than many small hand-built ones.

Everything below is invented/anonymized synthetic data captured in the
shape real evidence would take - no real business, no live crawl output,
no live provider/API response, and no prose copied from a historical
generated report. The base domain uses the IANA-reserved ".test" TLD
(RFC 2606) specifically so it can never resolve to a real site.

Coverage, per Step 18's literal requirements:
- Multiple core pages and multiple subpages (varied PageType values).
- One transient non-200 observation (a single, unconfirmed 503 - see
  fetch_service.is_transient_status_code()/attempt_count semantics).
- Accessible robots.txt and sitemap evidence.
- Available PageSpeed (field) performance evidence.
- Mixed on-page issues (missing meta description, missing canonical,
  duplicate H1s, thin content) spread across distinct findings/pages.
- Accepted (citation-verified) keyword, competitor, and location research.
- Explicit research-failure states (authority_opportunities/brand_presence)
  distinct from a genuine no-results outcome.

This module intentionally contains no test assertions of its own; it only
builds data. Call build_frozen_audit_context() from tests that need it.
"""

from datetime import datetime

from src.services.audit_models import (
    AuditContext,
    CategoryScore,
    CompetitorGap,
    CompetitorOverview,
    EffortLevel,
    Finding,
    KeywordOpportunity,
    LocationOpportunity,
    PageEvidence,
    PageType,
    PerformanceEvidence,
    ResearchBundle,
    ResearchStatus,
    RobotsTxtEvidence,
    ScoreBreakdown,
    Severity,
    SitemapEntry,
    SitemapEvidence,
    SiteEvidence,
    SiteInventory,
)

BASE_URL = "https://sample-bakery-co.test"

_RETRIEVED_DATE = "2026-08-01"
_SOURCE_URL = "https://research-source.test/article"
_SOURCE_TITLE = "Anonymized research source"


def _page(
    path: str,
    page_type: PageType,
    *,
    http_status: int = 200,
    page_title: str | None = "Anonymized Page Title",
    meta_description: str | None = "Anonymized meta description of representative length.",
    canonical_url: str | None = None,
    h1_tags: list[str] | None = None,
    word_count: int = 500,
    attempt_count: int = 1,
    schema_types: list[str] | None = None,
) -> PageEvidence:
    url = f"{BASE_URL}{path}" if path else f"{BASE_URL}/"
    return PageEvidence(
        url=url,
        page_type=page_type,
        http_status=http_status,
        is_https=True,
        used_playwright_fallback=False,
        page_title=page_title,
        meta_description=meta_description,
        canonical_url=canonical_url if canonical_url is not None else url,
        page_language="en",
        h1_tags=h1_tags if h1_tags is not None else ["Anonymized Heading"],
        word_count=word_count,
        schema_types=schema_types if schema_types is not None else [],
        attempt_count=attempt_count,
    )


def _keyword(keyword: str, search_intent: str, *, estimated_volume: str | None, target_page: str | None) -> KeywordOpportunity:
    return KeywordOpportunity(
        keyword=keyword, search_intent=search_intent, source_url=_SOURCE_URL,
        source_title=_SOURCE_TITLE, retrieved_date=_RETRIEVED_DATE,
        estimated_volume=estimated_volume, target_page=target_page,
    )


def build_frozen_audit_context() -> AuditContext:
    """Return one fully populated, immutable AuditContext for regression/parity tests."""

    homepage = _page(
        "", PageType.CORE,
        page_title="Sample Bakery Co. - Fresh Bread & Cakes",
        meta_description="Anonymized homepage description of representative length.",
        h1_tags=["Sample Bakery Co."],
        word_count=850,
        schema_types=["LocalBusiness"],
    )
    about_page = _page(
        "/about", PageType.CORE,
        page_title="About Sample Bakery Co.",
        h1_tags=["About Us"],
        word_count=620,
    )
    service_page = _page(
        "/services/custom-cakes", PageType.SERVICE_PRODUCT,
        page_title="Custom Cakes",
        meta_description=None,  # Mixed on-page issue: missing meta description
        h1_tags=["Custom Cakes"],
        word_count=540,
    )
    blog_page = _page(
        "/blog/sourdough-tips", PageType.BLOG_ARTICLE,
        page_title="Sourdough Tips",
        h1_tags=["Sourdough Tips"],
        word_count=120,  # Mixed on-page issue: thin content
    )
    location_page = _page(
        "/locations/austin-tx", PageType.LOCATION,
        page_title="Serving Austin, TX",
        canonical_url=None,  # Mixed on-page issue: missing canonical tag
        h1_tags=["Serving Austin, TX"],
        word_count=430,
    )
    category_page = _page(
        "/category/breads", PageType.CATEGORY,
        page_title="Our Breads",
        h1_tags=["Our Breads", "Fresh Baked Breads"],  # Mixed on-page issue: duplicate H1s
        word_count=380,
    )
    unconfirmed_status_page = _page(
        "/support/order-status", PageType.UTILITY,
        http_status=503,  # One transient non-200 observation, never retried
        page_title=None,
        meta_description=None,
        canonical_url=None,
        h1_tags=[],
        word_count=0,
        attempt_count=1,
    )

    inventory = SiteInventory(
        base_url=BASE_URL,
        entries=[
            SitemapEntry(url=homepage.url, source_sitemap=f"{BASE_URL}/sitemap.xml", page_type=PageType.CORE),
            SitemapEntry(url=about_page.url, source_sitemap=f"{BASE_URL}/sitemap.xml", page_type=PageType.CORE),
            SitemapEntry(url=service_page.url, source_sitemap=f"{BASE_URL}/sitemap.xml", page_type=PageType.SERVICE_PRODUCT),
            SitemapEntry(url=blog_page.url, source_sitemap=f"{BASE_URL}/sitemap.xml", page_type=PageType.BLOG_ARTICLE),
            SitemapEntry(url=location_page.url, source_sitemap=f"{BASE_URL}/sitemap.xml", page_type=PageType.LOCATION),
            SitemapEntry(url=category_page.url, source_sitemap=f"{BASE_URL}/sitemap.xml", page_type=PageType.CATEGORY),
            SitemapEntry(url=unconfirmed_status_page.url, source_sitemap=f"{BASE_URL}/sitemap.xml", page_type=PageType.UTILITY),
            # Discovered but not selected for the crawl sample - never given invented content.
            SitemapEntry(url=f"{BASE_URL}/services/wedding-cakes", source_sitemap=f"{BASE_URL}/sitemap.xml", page_type=PageType.SERVICE_PRODUCT),
        ],
        total_url_count=8,
        sampled_urls=[
            homepage.url, about_page.url, service_page.url, blog_page.url,
            location_page.url, category_page.url, unconfirmed_status_page.url,
        ],
    )

    site_evidence = SiteEvidence(
        base_url=BASE_URL,
        final_url=f"{BASE_URL}/",
        homepage=homepage,
        sampled_pages=[about_page, service_page, blog_page, location_page, category_page, unconfirmed_status_page],
        inventory=inventory,
        robots_txt=RobotsTxtEvidence(
            is_accessible=True,
            http_status=200,
            disallow_rules=["/cart/", "/checkout/"],
            allow_rules=["/"],
            sitemap_urls=[f"{BASE_URL}/sitemap.xml"],
            blocks_root_path=False,
        ),
        sitemaps=[
            SitemapEvidence(
                url=f"{BASE_URL}/sitemap.xml",
                is_accessible=True,
                http_status=200,
                url_count=8,
                urls=[entry.url for entry in inventory.entries],
            ),
        ],
        performance=PerformanceEvidence(
            is_available=True,
            data_source="field",
            performance_score=78.0,
            largest_contentful_paint_ms=2400.0,
            cumulative_layout_shift=0.08,
            interaction_to_next_paint_ms=180.0,
            source_url=homepage.url,
        ),
        unverifiable_fields=["security_headers"],
    )

    findings = [
        Finding(
            category="Technical SEO",
            title="Sampled page returned an unconfirmed transient HTTP status",
            severity=Severity.LOW,
            description=(
                "One sampled page returned a 503 status on a single, unconfirmed "
                "observation; it was not retried, so this is not yet a confirmed failure."
            ),
            business_impact="Minor - a single unconfirmed observation may resolve on its own.",
            recommendation="Re-check this page; investigate further only if the status recurs.",
            effort=EffortLevel.LOW,
            evidence_urls=[unconfirmed_status_page.url],
            score_deduction=5.0,
        ),
        Finding(
            category="On-Page SEO",
            title="Missing meta description",
            severity=Severity.MEDIUM,
            description="One sampled page has no meta description.",
            business_impact="Search engines may generate a less compelling auto snippet.",
            recommendation="Add a unique, compelling meta description for this page.",
            effort=EffortLevel.LOW,
            evidence_urls=[service_page.url],
            score_deduction=8.0,
        ),
        Finding(
            category="On-Page SEO",
            title="Missing canonical tag",
            severity=Severity.LOW,
            description="One sampled page has no canonical tag.",
            business_impact="Minor risk of duplicate-content ambiguity.",
            recommendation="Add a self-referencing canonical tag to this page.",
            effort=EffortLevel.LOW,
            evidence_urls=[location_page.url],
            score_deduction=4.0,
        ),
        Finding(
            category="On-Page SEO",
            title="Multiple H1 headings detected",
            severity=Severity.MEDIUM,
            description="One sampled page has two H1 headings instead of exactly one.",
            business_impact="Diluted heading signal for the page's primary topic.",
            recommendation="Consolidate to a single H1 heading per page.",
            effort=EffortLevel.LOW,
            evidence_urls=[category_page.url],
            score_deduction=6.0,
        ),
        Finding(
            category="Content Quality",
            title="Thin content detected",
            severity=Severity.MEDIUM,
            description="One sampled page has a word count well below the thin-content threshold.",
            business_impact="Thin pages are less likely to rank for meaningful queries.",
            recommendation="Expand this page with more substantive, useful content.",
            effort=EffortLevel.MEDIUM,
            evidence_urls=[blog_page.url],
            score_deduction=7.0,
        ),
    ]

    score_breakdown = ScoreBreakdown(
        overall_score=76.5,
        category_scores=[
            CategoryScore(category="Technical SEO", weight_percent=40.0, score=88.0),
            CategoryScore(category="On-Page SEO", weight_percent=25.0, score=68.0),
            CategoryScore(category="Content Quality", weight_percent=15.0, score=75.0),
            CategoryScore(category="Performance", weight_percent=10.0, score=78.0),
            CategoryScore(category="Accessibility", weight_percent=5.0, score=90.0),
            CategoryScore(category="Security", weight_percent=5.0, score=60.0),
        ],
        findings=findings,
    )

    research = ResearchBundle(
        primary_keywords=[
            _keyword("artisan sourdough bread austin", "commercial", estimated_volume="1,000-10,000/mo", target_page="/blog/sourdough-tips"),
            _keyword("custom birthday cakes austin", "transactional", estimated_volume="100-1,000/mo", target_page="/services/custom-cakes"),
        ],
        long_tail_keywords=[
            _keyword("gluten free sourdough bread austin tx", "commercial", estimated_volume=None, target_page="/blog/sourdough-tips"),
        ],
        competitors=[
            CompetitorOverview(
                competitor_name="Anonymized Competitor Bakery", website="https://competitor-bakery.test",
                focus="Wholesale bread and pastries", source_url=_SOURCE_URL,
                source_title=_SOURCE_TITLE, retrieved_date=_RETRIEVED_DATE,
                estimated_authority="Medium",
            ),
        ],
        competitor_analysis=[
            CompetitorGap(
                keyword="custom birthday cakes austin", competitor_position="Ranks in top 3",
                your_gap="No dedicated landing page targeting this keyword.",
                source_url=_SOURCE_URL, source_title=_SOURCE_TITLE, retrieved_date=_RETRIEVED_DATE,
            ),
        ],
        authority_opportunities=[],
        brand_presence=[],
        local_demand=[
            LocationOpportunity(
                city_or_region="Austin, TX", primary_keyword="bakery near me", priority="High",
                source_url=_SOURCE_URL, source_title=_SOURCE_TITLE, retrieved_date=_RETRIEVED_DATE,
                estimated_volume="1,000-10,000/mo",
            ),
        ],
        audience_expansion=[],
        research_statuses={
            "primary_keywords": ResearchStatus.SUCCESS,
            "long_tail_keywords": ResearchStatus.SUCCESS,
            "competitors": ResearchStatus.SUCCESS,
            "competitor_analysis": ResearchStatus.SUCCESS,
            # Explicit research-failure states - never silently treated as genuine no-results.
            "authority_opportunities": ResearchStatus.PROVIDER_FAILED,
            "brand_presence": ResearchStatus.PARSE_FAILED,
            "local_demand": ResearchStatus.SUCCESS,
        },
    )

    return AuditContext(
        audit_id="frozen-fixture-audit-0001",
        normalized_url=BASE_URL,
        site_evidence=site_evidence,
        score_breakdown=score_breakdown,
        research=research,
        is_local_business=True,
        city_or_region="Austin, TX",
        created_at=datetime(2026, 8, 1, 12, 0, 0),
    )
