"""
test/test_report_data_service.py

Unit tests for src/services/report_data_service.py — the deterministic
projection layer that builds InventorySectionData/TechnicalSectionData/
OnPageSectionData from one AuditContext. Purely deterministic/string-free
transformations, so no LLM/network mocking is needed.

Run with:
    pytest test/test_report_data_service.py -v
"""

from datetime import datetime, timezone

from src.services.audit_models import (
    AuditContext,
    EffortLevel,
    Finding,
    PageEvidence,
    PageType,
    PerformanceEvidence,
    ResearchBundle,
    RobotsTxtEvidence,
    ScoreBreakdown,
    Severity,
    SiteEvidence,
    SiteInventory,
    SitemapEntry,
    SitemapEvidence,
)
from src.services.report_data_service import (
    build_inventory_section_data,
    build_on_page_section_data,
    build_technical_section_data,
)


def _make_page(**overrides) -> PageEvidence:
    defaults = dict(
        url="https://example.com/",
        page_type=PageType.CORE,
        http_status=200,
        is_https=True,
        used_playwright_fallback=False,
        page_title="Example Bakery",
        meta_description="Fresh sourdough bread baked daily.",
        canonical_url="https://example.com/",
        page_language="en",
    )
    defaults.update(overrides)
    return PageEvidence(**defaults)


def _make_finding(category: str, **overrides) -> Finding:
    defaults = dict(
        category=category,
        title="Example finding",
        severity=Severity.MEDIUM,
        description="Something was found.",
        business_impact="Some business impact.",
        recommendation="Do something about it.",
        effort=EffortLevel.LOW,
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _make_context(**overrides) -> AuditContext:
    defaults = dict(
        audit_id="test-audit-id",
        normalized_url="https://example.com",
        site_evidence=SiteEvidence(
            base_url="https://example.com", final_url="https://example.com/", homepage=_make_page(),
        ),
        score_breakdown=ScoreBreakdown(overall_score=90.0),
        research=ResearchBundle(),
        is_local_business=False,
        city_or_region=None,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return AuditContext(**defaults)


class TestBuildInventorySectionData:

    def test_homepage_is_always_a_core_page(self) -> None:
        data = build_inventory_section_data(_make_context())
        assert len(data.core_pages) == 1
        assert data.core_pages[0].url == "https://example.com/"
        assert data.core_pages[0].was_crawled is True
        assert data.subpages == []

    def test_non_core_sampled_page_becomes_a_subpage(self) -> None:
        blog_page = _make_page(url="https://example.com/blog/recipe", page_type=PageType.BLOG_ARTICLE)
        about_page = _make_page(url="https://example.com/about", page_type=PageType.CORE)
        site_evidence = SiteEvidence(
            base_url="https://example.com", final_url="https://example.com/",
            homepage=_make_page(), sampled_pages=[blog_page, about_page],
        )
        data = build_inventory_section_data(_make_context(site_evidence=site_evidence))

        assert {page.url for page in data.core_pages} == {"https://example.com/", "https://example.com/about"}
        assert {page.url for page in data.subpages} == {"https://example.com/blog/recipe"}

    def test_uncrawled_sitemap_entries_carry_no_content_facts(self) -> None:
        inventory = SiteInventory(
            base_url="https://example.com",
            entries=[
                SitemapEntry(
                    url="https://example.com/", source_sitemap="https://example.com/sitemap.xml",
                    page_type=PageType.CORE,
                ),
                SitemapEntry(
                    url="https://example.com/uncrawled-page", source_sitemap="https://example.com/sitemap.xml",
                    lastmod="2026-01-01", page_type=PageType.UTILITY,
                ),
            ],
            total_url_count=2,
        )
        site_evidence = SiteEvidence(
            base_url="https://example.com", final_url="https://example.com/",
            homepage=_make_page(), inventory=inventory,
        )
        data = build_inventory_section_data(_make_context(site_evidence=site_evidence))

        assert len(data.sitemap_only_pages) == 1
        uncrawled = data.sitemap_only_pages[0]
        assert uncrawled.url == "https://example.com/uncrawled-page"
        assert uncrawled.was_crawled is False
        assert uncrawled.http_status is None
        assert uncrawled.page_title is None
        assert uncrawled.source_sitemap == "https://example.com/sitemap.xml"
        assert uncrawled.sitemap_lastmod == "2026-01-01"

    def test_counts_reflect_full_inventory_and_actual_crawl(self) -> None:
        inventory = SiteInventory(base_url="https://example.com", entries=[], total_url_count=42)
        site_evidence = SiteEvidence(
            base_url="https://example.com", final_url="https://example.com/",
            homepage=_make_page(), sampled_pages=[_make_page(url="https://example.com/about")],
            inventory=inventory,
        )
        data = build_inventory_section_data(_make_context(site_evidence=site_evidence))

        assert data.total_discovered == 42
        assert data.total_analyzed == 2

    def test_missing_inventory_falls_back_to_crawled_count(self) -> None:
        data = build_inventory_section_data(_make_context())
        assert data.total_discovered == 1  # Only the homepage was crawled, no inventory available
        assert data.total_analyzed == 1
        assert data.sitemap_only_pages == []


class TestBuildTechnicalSectionData:

    def test_only_technical_seo_findings_are_included(self) -> None:
        findings = [
            _make_finding("Technical SEO", title="No HTTPS"),
            _make_finding("On-Page SEO", title="Missing title"),
        ]
        context = _make_context(score_breakdown=ScoreBreakdown(overall_score=70.0, findings=findings))
        data = build_technical_section_data(context)

        assert [f.title for f in data.findings] == ["No HTTPS"]

    def test_passes_through_robots_sitemap_and_performance_evidence(self) -> None:
        robots = RobotsTxtEvidence(
            is_accessible=True, http_status=200, disallow_rules=[], allow_rules=[],
            sitemap_urls=["https://example.com/sitemap.xml"], blocks_root_path=False,
        )
        sitemap = SitemapEvidence(url="https://example.com/sitemap.xml", is_accessible=True, http_status=200, url_count=5)
        performance = PerformanceEvidence(is_available=True, data_source="field", performance_score=90.0)
        site_evidence = SiteEvidence(
            base_url="https://example.com", final_url="https://example.com/", homepage=_make_page(),
            robots_txt=robots, sitemaps=[sitemap], performance=performance,
        )
        data = build_technical_section_data(_make_context(site_evidence=site_evidence))

        assert data.robots_txt is robots
        assert data.sitemaps == [sitemap]
        assert data.performance is performance

    def test_detected_schema_types_are_deduplicated_in_first_seen_order(self) -> None:
        homepage = _make_page(schema_types=["Organization", "LocalBusiness"])
        sampled = _make_page(url="https://example.com/about", schema_types=["LocalBusiness", "Article"])
        site_evidence = SiteEvidence(
            base_url="https://example.com", final_url="https://example.com/",
            homepage=homepage, sampled_pages=[sampled],
        )
        data = build_technical_section_data(_make_context(site_evidence=site_evidence))

        assert data.detected_schema_types == ["Organization", "LocalBusiness", "Article"]

    def test_pages_include_homepage_and_every_sampled_page(self) -> None:
        sampled = _make_page(url="https://example.com/about", page_type=PageType.CORE)
        site_evidence = SiteEvidence(
            base_url="https://example.com", final_url="https://example.com/",
            homepage=_make_page(), sampled_pages=[sampled],
        )
        data = build_technical_section_data(_make_context(site_evidence=site_evidence))

        assert {page.url for page in data.pages} == {"https://example.com/", "https://example.com/about"}
        assert all(page.was_crawled for page in data.pages)


class TestBuildOnPageSectionData:

    def test_homepage_is_projected_with_verified_fields(self) -> None:
        data = build_on_page_section_data(_make_context())
        assert data.homepage.url == "https://example.com/"
        assert data.homepage.page_title == "Example Bakery"
        assert data.homepage.was_crawled is True

    def test_priority_pages_exclude_the_homepage(self) -> None:
        sampled = _make_page(url="https://example.com/services", page_type=PageType.SERVICE_PRODUCT)
        site_evidence = SiteEvidence(
            base_url="https://example.com", final_url="https://example.com/",
            homepage=_make_page(), sampled_pages=[sampled],
        )
        data = build_on_page_section_data(_make_context(site_evidence=site_evidence))

        assert [page.url for page in data.priority_pages] == ["https://example.com/services"]

    def test_findings_are_split_by_category(self) -> None:
        findings = [
            _make_finding("On-Page SEO", title="Missing meta description"),
            _make_finding("Content Quality", title="Thin content"),
            _make_finding("Technical SEO", title="No HTTPS"),
        ]
        context = _make_context(score_breakdown=ScoreBreakdown(overall_score=70.0, findings=findings))
        data = build_on_page_section_data(context)

        assert [f.title for f in data.on_page_findings] == ["Missing meta description"]
        assert [f.title for f in data.content_findings] == ["Thin content"]
