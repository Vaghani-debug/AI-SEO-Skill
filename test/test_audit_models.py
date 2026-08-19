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
    AuditJob,
    AuditJobStatus,
    CategoryScore,
    CompetitorGap,
    CompetitorGapResult,
    CompetitorOverview,
    CompetitorResearchResult,
    EffortLevel,
    Finding,
    InventorySectionData,
    KeywordOpportunity,
    KeywordResearchResult,
    LocationOpportunity,
    LocationResearchResult,
    OnPageSectionData,
    PageEvidence,
    PageReportRow,
    PageType,
    ResearchBundle,
    ResearchClaim,
    ResearchResult,
    ResearchStatus,
    ScoreBreakdown,
    SiteEvidence,
    SiteInventory,
    SitemapEntry,
    Severity,
    TechnicalSectionData,
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


class TestKeywordOpportunity:
    """Tests for KeywordOpportunity construction and non-fabricated optional fields."""

    def test_estimated_volume_and_target_page_default_to_none(self) -> None:
        opportunity = KeywordOpportunity(
            keyword="sourdough bread austin",
            search_intent="commercial",
            source_url="https://example-research.com/report",
            source_title="Example Research Report",
            retrieved_date="2026-08-04",
        )
        assert opportunity.estimated_volume is None
        assert opportunity.target_page is None

    def test_stores_sourced_estimate_and_target_page(self) -> None:
        opportunity = KeywordOpportunity(
            keyword="sourdough bread austin",
            search_intent="commercial",
            source_url="https://example-research.com/report",
            source_title="Example Research Report",
            retrieved_date="2026-08-04",
            estimated_volume="1,000-10,000/mo",
            target_page="/bread",
        )
        assert opportunity.estimated_volume == "1,000-10,000/mo"
        assert opportunity.target_page == "/bread"


class TestKeywordResearchResult:
    """Tests for KeywordResearchResult, the typed-keyword counterpart to ResearchResult."""

    def test_defaults_are_empty_opportunities_and_no_error(self) -> None:
        result = KeywordResearchResult(status=ResearchStatus.NO_RESULTS)
        assert result.opportunities == []
        assert result.error is None

    def test_stores_opportunities_and_status(self) -> None:
        opportunity = KeywordOpportunity(
            keyword="sourdough bread austin", search_intent="commercial",
            source_url="https://example.com", source_title="Example", retrieved_date="2026-08-04",
        )
        result = KeywordResearchResult(status=ResearchStatus.SUCCESS, opportunities=[opportunity])
        assert result.opportunities == [opportunity]
        assert result.status == ResearchStatus.SUCCESS


class TestResearchBundle:
    """Tests for the ResearchBundle container that groups research by category."""

    def test_defaults_are_all_empty(self) -> None:
        bundle = ResearchBundle()
        assert bundle.primary_keywords == []
        assert bundle.long_tail_keywords == []
        assert bundle.competitors == []
        assert bundle.competitor_analysis == []
        assert bundle.authority_opportunities == []
        assert bundle.local_demand == []

    def test_stores_claims_per_category(self) -> None:
        competitor = CompetitorOverview(
            competitor_name="Joe's Bakery", website="https://joesbakery.com", focus="Wholesale bread",
            source_url="https://example.com", source_title="Example", retrieved_date="2026-08-04",
        )
        bundle = ResearchBundle(competitors=[competitor])
        assert bundle.competitors == [competitor]
        assert bundle.primary_keywords == []
        assert bundle.long_tail_keywords == []


class TestCompetitorOverview:
    """Tests for CompetitorOverview construction and non-fabricated optional fields."""

    def test_estimated_authority_defaults_to_none(self) -> None:
        competitor = CompetitorOverview(
            competitor_name="Joe's Bakery", website="https://joesbakery.com", focus="Wholesale bread",
            source_url="https://example.com", source_title="Example", retrieved_date="2026-08-04",
        )
        assert competitor.estimated_authority is None

    def test_stores_sourced_authority(self) -> None:
        competitor = CompetitorOverview(
            competitor_name="Joe's Bakery", website="https://joesbakery.com", focus="Wholesale bread",
            source_url="https://example.com", source_title="Example", retrieved_date="2026-08-04",
            estimated_authority="High",
        )
        assert competitor.estimated_authority == "High"


class TestCompetitorResearchResult:
    """Tests for CompetitorResearchResult, the typed-competitor counterpart to ResearchResult."""

    def test_defaults_are_empty_competitors_and_no_error(self) -> None:
        result = CompetitorResearchResult(status=ResearchStatus.NO_RESULTS)
        assert result.competitors == []
        assert result.error is None

    def test_stores_competitors_and_status(self) -> None:
        competitor = CompetitorOverview(
            competitor_name="Joe's Bakery", website="https://joesbakery.com", focus="Wholesale bread",
            source_url="https://example.com", source_title="Example", retrieved_date="2026-08-04",
        )
        result = CompetitorResearchResult(status=ResearchStatus.SUCCESS, competitors=[competitor])
        assert result.competitors == [competitor]
        assert result.status == ResearchStatus.SUCCESS


class TestCompetitorGapResult:
    """Tests for CompetitorGapResult, the typed-competitor-gap counterpart to ResearchResult."""

    def test_defaults_are_empty_gaps_and_no_error(self) -> None:
        result = CompetitorGapResult(status=ResearchStatus.NO_RESULTS)
        assert result.gaps == []
        assert result.error is None

    def test_stores_gaps_and_status(self) -> None:
        gap = CompetitorGap(
            keyword="artisan bread austin", competitor_position="Joe's Bakery ranks #2",
            your_gap="No dedicated landing page",
            source_url="https://example.com", source_title="Example", retrieved_date="2026-08-04",
        )
        result = CompetitorGapResult(status=ResearchStatus.SUCCESS, gaps=[gap])
        assert result.gaps == [gap]
        assert result.status == ResearchStatus.SUCCESS


class TestLocationOpportunity:
    """Tests for LocationOpportunity construction and non-fabricated optional fields."""

    def test_estimated_volume_defaults_to_none(self) -> None:
        opportunity = LocationOpportunity(
            city_or_region="Austin, TX", primary_keyword="bakery near me", priority="High",
            source_url="https://example.com", source_title="Example", retrieved_date="2026-08-04",
        )
        assert opportunity.estimated_volume is None

    def test_stores_sourced_estimate(self) -> None:
        opportunity = LocationOpportunity(
            city_or_region="Austin, TX", primary_keyword="bakery near me", priority="High",
            source_url="https://example.com", source_title="Example", retrieved_date="2026-08-04",
            estimated_volume="1,000-10,000/mo",
        )
        assert opportunity.estimated_volume == "1,000-10,000/mo"


class TestLocationResearchResult:
    """Tests for LocationResearchResult, the typed-location counterpart to ResearchResult."""

    def test_defaults_are_empty_opportunities_and_no_error(self) -> None:
        result = LocationResearchResult(status=ResearchStatus.NO_RESULTS)
        assert result.opportunities == []
        assert result.error is None

    def test_stores_opportunities_and_status(self) -> None:
        opportunity = LocationOpportunity(
            city_or_region="Austin, TX", primary_keyword="bakery near me", priority="High",
            source_url="https://example.com", source_title="Example", retrieved_date="2026-08-04",
        )
        result = LocationResearchResult(status=ResearchStatus.SUCCESS, opportunities=[opportunity])
        assert result.opportunities == [opportunity]
        assert result.status == ResearchStatus.SUCCESS

    def test_insufficient_location_evidence_is_a_distinct_deterministic_status(self) -> None:
        result = LocationResearchResult(
            status=ResearchStatus.INSUFFICIENT_LOCATION_EVIDENCE,
            error="Business appears local/service-area, but no service region could be determined from crawl evidence.",
        )
        assert result.opportunities == []
        assert result.error is not None


class TestReportFacingModels:
    """Tests for the evidence projections consumed by deterministic renderers."""

    def _make_page_row(self, **overrides) -> PageReportRow:
        defaults = dict(
            url="https://example.com/",
            page_type=PageType.CORE,
            was_crawled=True,
            http_status=200,
            page_title="Example",
        )
        defaults.update(overrides)
        return PageReportRow(**defaults)

    def test_page_report_row_preserves_crawl_coverage(self) -> None:
        crawled = self._make_page_row(h1_tags=["Example"], word_count=450)
        sitemap_only = self._make_page_row(
            url="https://example.com/not-crawled",
            was_crawled=False,
            http_status=None,
            page_title=None,
            source_sitemap="https://example.com/sitemap.xml",
        )

        assert crawled.was_crawled is True
        assert crawled.h1_tags == ["Example"]
        assert sitemap_only.was_crawled is False
        assert sitemap_only.http_status is None
        assert sitemap_only.page_title is None

    def test_inventory_defaults_do_not_share_lists(self) -> None:
        first = InventorySectionData()
        second = InventorySectionData()

        first.core_pages.append(self._make_page_row())

        assert len(first.core_pages) == 1
        assert second.core_pages == []
        assert first.sitemap_only_pages == []

    def test_technical_section_groups_verified_evidence(self) -> None:
        page = self._make_page_row(schema_types=["Organization"])
        section = TechnicalSectionData(
            detected_schema_types=["Organization"],
            pages=[page],
        )

        assert section.robots_txt is None
        assert section.performance is None
        assert section.detected_schema_types == ["Organization"]
        assert section.pages == [page]

    def test_on_page_section_requires_homepage(self) -> None:
        homepage = self._make_page_row()
        priority_page = self._make_page_row(
            url="https://example.com/services",
            page_type=PageType.SERVICE_PRODUCT,
        )
        section = OnPageSectionData(homepage=homepage, priority_pages=[priority_page])

        assert section.homepage == homepage
        assert section.priority_pages == [priority_page]
        assert section.on_page_findings == []
        assert section.content_findings == []


class TestResearchResult:
    """Tests for explicit research outcomes used by the future projection layer."""

    def test_status_vocabulary_is_fixed(self) -> None:
        assert {status.value for status in ResearchStatus} == {
            "success",
            "no_results",
            "parse_failed",
            "citation_failed",
            "provider_failed",
            "insufficient_location_evidence",
        }

    def test_failure_preserves_reason_without_claims(self) -> None:
        result = ResearchResult(
            status=ResearchStatus.CITATION_FAILED,
            error="Provider citations did not match returned source URLs.",
        )

        assert result.claims == []
        assert result.error is not None

    def test_success_stores_verified_claims(self) -> None:
        claim = ResearchClaim(
            claim="Organic competitor",
            value="Example Competitor",
            source_url="https://competitor.example",
            source_title="Example Competitor",
            retrieved_date="2026-08-19",
        )
        result = ResearchResult(status=ResearchStatus.SUCCESS, claims=[claim])

        assert result.claims == [claim]
        assert result.error is None


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


class TestAuditJobStatus:
    """Tests for the AuditJobStatus lifecycle enum."""

    def test_has_expected_members(self) -> None:
        assert {member.value for member in AuditJobStatus} == {
            "pending",
            "crawling",
            "researching",
            "generating",
            "assembling",
            "rendering_pdf",
            "complete",
            "failed",
        }


class TestAuditJob:
    """Tests for the mutable AuditJob dataclass used for in-process job persistence."""

    def test_defaults_on_creation(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        job = AuditJob(
            audit_id="abc-123",
            normalized_url="https://example.com",
            status=AuditJobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        assert job.markdown_report is None
        assert job.pdf_path is None
        assert job.error is None

    def test_status_and_fields_are_mutable_in_place(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        job = AuditJob(
            audit_id="abc-123",
            normalized_url="https://example.com",
            status=AuditJobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

        job.status = AuditJobStatus.CRAWLING
        job.updated_at = datetime.now(timezone.utc)
        assert job.status == AuditJobStatus.CRAWLING

        job.status = AuditJobStatus.COMPLETE
        job.markdown_report = "# Report"
        job.pdf_path = "/reports/abc-123.pdf"
        assert job.status == AuditJobStatus.COMPLETE
        assert job.markdown_report == "# Report"
        assert job.pdf_path == "/reports/abc-123.pdf"

    def test_failed_job_records_error(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        job = AuditJob(
            audit_id="abc-123",
            normalized_url="https://example.com",
            status=AuditJobStatus.FAILED,
            created_at=now,
            updated_at=now,
            error="Could not crawl the website",
        )
        assert job.status == AuditJobStatus.FAILED
        assert job.error == "Could not crawl the website"
