"""
test/test_audit_models.py

Unit tests for src/services/audit_models.py.

These are plain dataclass/enum contracts, so tests focus on construction,
default values, and that the fixed vocabularies (PageType, Severity,
EffortLevel) match the values relied on elsewhere in the codebase
(seo_audit.prompt.md severity levels, SCORING_ENGINE.md categories).

Run with:
    pytest test/test_audit_models.py -v
"""

from src.services.audit_models import (
    AuditContext,
    CategoryScore,
    EffortLevel,
    Finding,
    PageEvidence,
    PageType,
    ResearchBundle,
    ResearchClaim,
    ScoreBreakdown,
    SiteEvidence,
    SiteInventory,
    SitemapEntry,
    Severity,
)


class TestPageType:
    """Tests for the PageType classification enum."""

    def test_has_expected_members(self) -> None:
        assert {member.value for member in PageType} == {
            "core",
            "service_product",
            "blog_article",
            "location",
            "category",
            "utility",
        }


class TestSeverity:
    """Tests for the Severity enum, which must match seo_audit.prompt.md."""

    def test_matches_prompt_severity_levels(self) -> None:
        assert [member.value for member in Severity] == [
            "Critical",
            "High",
            "Medium",
            "Low",
            "Informational",
        ]


class TestEffortLevel:
    """Tests for the EffortLevel enum."""

    def test_has_three_levels(self) -> None:
        assert [member.value for member in EffortLevel] == ["Low", "Medium", "High"]


class TestSiteInventory:
    """Tests for SitemapEntry and SiteInventory construction/defaults."""

    def test_defaults_are_empty(self) -> None:
        inventory = SiteInventory(base_url="https://example.com")
        assert inventory.entries == []
        assert inventory.total_url_count == 0
        assert inventory.sampled_urls == []

    def test_stores_sitemap_entries(self) -> None:
        entry = SitemapEntry(
            url="https://example.com/page-1",
            source_sitemap="https://example.com/sitemap.xml",
            lastmod="2026-01-01",
        )
        inventory = SiteInventory(
            base_url="https://example.com",
            entries=[entry],
            total_url_count=1,
            sampled_urls=[entry.url],
        )
        assert inventory.entries[0].lastmod == "2026-01-01"
        assert inventory.sampled_urls == ["https://example.com/page-1"]


class TestPageEvidence:
    """Tests for PageEvidence construction and field defaults."""

    def _make_page(self, **overrides) -> PageEvidence:
        defaults = dict(
            url="https://example.com/",
            page_type=PageType.CORE,
            http_status=200,
            is_https=True,
            used_playwright_fallback=False,
            page_title="Example",
            meta_description="An example page.",
            canonical_url="https://example.com/",
            page_language="en",
        )
        defaults.update(overrides)
        return PageEvidence(**defaults)

    def test_list_fields_default_to_empty(self) -> None:
        page = self._make_page()
        assert page.h1_tags == []
        assert page.h2_tags == []
        assert page.schema_types == []
        assert page.internal_links == []
        assert page.external_links == []
        assert page.images == []
        assert page.redirect_chain == []
        assert page.word_count == 0

    def test_stores_playwright_fallback_flag(self) -> None:
        page = self._make_page(used_playwright_fallback=True, page_type=PageType.BLOG_ARTICLE)
        assert page.used_playwright_fallback is True
        assert page.page_type == PageType.BLOG_ARTICLE


class TestSiteEvidence:
    """Tests for the top-level SiteEvidence container."""

    def test_defaults_are_empty_and_homepage_required(self) -> None:
        homepage = PageEvidence(
            url="https://example.com/",
            page_type=PageType.CORE,
            http_status=200,
            is_https=True,
            used_playwright_fallback=False,
            page_title="Example",
            meta_description="An example page.",
            canonical_url="https://example.com/",
            page_language="en",
        )
        evidence = SiteEvidence(
            base_url="https://example.com",
            final_url="https://example.com/",
            homepage=homepage,
        )
        assert evidence.sampled_pages == []
        assert evidence.inventory is None
        assert evidence.robots_txt is None
        assert evidence.sitemaps == []
        assert evidence.unverifiable_fields == []
        assert evidence.homepage.page_type == PageType.CORE


class TestFindingAndScoreBreakdown:
    """Tests for Finding, CategoryScore, and ScoreBreakdown construction."""

    def test_finding_defaults(self) -> None:
        finding = Finding(
            category="Technical SEO",
            title="Robots.txt blocks the entire site",
            severity=Severity.CRITICAL,
            description="Disallow: / found for the * user-agent.",
            business_impact="Search engines cannot crawl any page.",
            recommendation="Remove the blanket Disallow rule.",
            effort=EffortLevel.LOW,
        )
        assert finding.evidence_urls == []
        assert finding.score_deduction == 0.0

    def test_score_breakdown_aggregates_category_scores(self) -> None:
        category = CategoryScore(category="Technical SEO", weight_percent=40.0, score=85.0)
        breakdown = ScoreBreakdown(overall_score=78.5, category_scores=[category])
        assert breakdown.category_scores[0].category == "Technical SEO"
        assert breakdown.findings == []


class TestResearchClaim:
    """Tests for ResearchClaim construction and default provenance flags."""

    def test_defaults_indicate_an_estimate(self) -> None:
        claim = ResearchClaim(
            claim="Estimated monthly search volume",
            value="1,000-10,000/mo",
            source_url="https://example-research.com/report",
            source_title="Example Research Report",
            retrieved_date="2026-08-04",
        )
        assert claim.is_estimate is True
        assert claim.confidence == "Estimate"


class TestResearchBundle:
    """Tests for the ResearchBundle container that groups research by category."""

    def test_defaults_are_all_empty(self) -> None:
        bundle = ResearchBundle()
        assert bundle.keyword_opportunities == []
        assert bundle.competitors == []
        assert bundle.competitor_analysis == []
        assert bundle.authority_opportunities == []
        assert bundle.local_demand == []

    def test_stores_claims_per_category(self) -> None:
        claim = ResearchClaim(
            claim="Organic competitor", value="Joe's Bakery",
            source_url="https://example.com", source_title="Example",
            retrieved_date="2026-08-04",
        )
        bundle = ResearchBundle(competitors=[claim])
        assert bundle.competitors == [claim]
        assert bundle.keyword_opportunities == []


class TestAuditContext:
    """Tests for the immutable AuditContext bundle passed through the report pipeline."""

    def test_construction_and_immutability(self) -> None:
        from datetime import datetime, timezone

        homepage = PageEvidence(
            url="https://example.com/",
            page_type=PageType.CORE,
            http_status=200,
            is_https=True,
            used_playwright_fallback=False,
            page_title="Example",
            meta_description="An example page.",
            canonical_url="https://example.com/",
            page_language="en",
        )
        site_evidence = SiteEvidence(base_url="https://example.com", final_url="https://example.com/", homepage=homepage)
        score_breakdown = ScoreBreakdown(overall_score=90.0, category_scores=[])

        context = AuditContext(
            audit_id="abc-123",
            normalized_url="https://example.com",
            site_evidence=site_evidence,
            score_breakdown=score_breakdown,
            research=ResearchBundle(),
            is_local_business=False,
            city_or_region=None,
            created_at=datetime.now(timezone.utc),
        )

        assert context.audit_id == "abc-123"
        assert context.score_breakdown.overall_score == 90.0

        try:
            context.audit_id = "changed"  # type: ignore[misc]
            assert False, "AuditContext should be frozen"
        except AttributeError:
            pass
