"""
test/test_report_service.py

Unit tests for src/services/report_service.py.

All LLM calls go through llm_service.generate_text(), which is mocked here so
these tests run offline without tokens. Each test exercises one specific
behaviour, error path, or formatting rule.

Run with:
    pytest test/test_report_service.py -v
"""

from datetime import datetime  # For timestamp assertions
import re  # Extract "# PART N:" headings in section-pipeline test fakes
from unittest.mock import AsyncMock, MagicMock, patch  # Mocking tools

import pytest  # pytest: test runner

from src.config import Settings  # Provides API key and model configuration
from src.services.audit_models import (
    AuditContext,
    CategoryScore,
    CompetitorGap,
    CompetitorOverview,
    EffortLevel,
    Finding,
    InventorySectionData,
    KeywordOpportunity,
    LocationOpportunity,
    PageEvidence,
    PageReportRow,
    PageType,
    PerformanceEvidence,
    ResearchBundle,
    ResearchClaim,
    ResearchStatus,
    ScoreBreakdown,
    Severity,
    SiteEvidence,
    SiteInventory,
    SitemapEntry,
)
from src.services.extractor_service import (
    AuditEvidence,       # Main evidence dataclass
    ImageInfo,           # Image metadata
    RobotsTxtEvidence,   # robots.txt findings
    SitemapEvidence,     # Sitemap accessibility
)
from src.services.llm_service import LLMProviderError  # Raised on empty/failed LLM responses
from src.services.prompt_loader import PromptContext  # Guidance context
from src.services.report_data_service import (  # Step 15 — well-formed-report test fixtures
    build_inventory_section_data,
    build_on_page_section_data,
    build_technical_section_data,
)
from src.services.report_service import (
    ReportResult,               # Return type
    AssembledReportResult,      # Phase 4 — assemble+validate checkpoint result
    ReportIntegrityError,       # Step 16 — raised when a deterministic block fails validation
    assemble_and_validate_report,  # Phase 4 — assemble+validate checkpoint entry point
    assemble_report_markdown,   # Phase 4 — assembles section dict into one final report
    validate_assembled_report,  # Phase 4 — report-level validation entry point
    _build_retry_user_message,  # Internal helper — retry instruction builder
    _build_section_user_message,  # Internal helper — Phase 4 per-section user message builder
    _build_fallback_section_markdown,  # Internal helper — Step 16 deterministic fallback narrative builder
    _build_user_message,       # Internal helper — evidence formatting
    _deduplicate_table_rows,   # Internal helper — Phase 4/16 duplicate table row removal
    _derive_page_name,         # Internal helper — Step 6 deterministic page name from URL
    _DETERMINISTIC_ONLY_GROUPS,  # Internal constant — Step 9 LLM-bypass group set
    _escape_table_cell,        # Internal helper — Step 6 pipe-escaping for table cells
    _extract_part_templates,   # Internal helper — Phase 4 per-PART template slicing
    _extract_section_body,     # Internal helper — subsection text extraction
    _find_banned_phrases,      # Internal helper — contamination/branding detection
    _find_table_blocks,        # Internal helper — Markdown table detection
    _format_evidence,          # Internal helper — evidence formatting
    _format_section_evidence,  # Internal helper — Phase 4 section-scoped evidence slicing
    _inject_competitor_tables,  # Internal helper — Step 14 forces SECTION 2.1/2.2 tables
    _inject_inventory_tables,  # Internal helper — Step 9 forces PART 1.1/1.2 tables
    _inject_keyword_tables,    # Internal helper — Step 14 forces SECTION 1.1/1.2 tables
    _inject_location_table,    # Internal helper — Step 14 forces SECTION 3.2 table
    _missing_required_report_parts,  # Internal helper — required PART heading detection
    _render_page_inventory_table,  # Internal helper — Step 6 shared table renderer
    _render_research_status_note,  # Internal helper — Step 14 empty-table availability narrative
    _render_technical_and_onpage_section,  # Internal helper — Step 9 deterministic PART 2/3 renderer
    _replace_heading_block,    # Internal helper — Step 9 forces content under a heading
    _split_table_row,          # Internal helper — Markdown table row parsing
    _SECTION_GROUPS,           # Internal constant — Phase 4 section group definitions
    _validate_citation_columns,  # Internal helper — Source/Retrieved validation
    _validate_deterministic_blocks_present,  # Internal helper — Step 15 re-render/verbatim-match validator
    _validate_deterministic_integrity,  # Internal helper — Step 16 raise-worthy subset of deterministic checks
    _validate_inventory_table_coverage,  # Internal helper — Step 15 exactly-once inventory coverage validator
    _validate_no_unconfirmed_http_claims,  # Internal helper — Step 15 unhedged transient-status validator
    _validate_removed_sections_absent,  # Internal helper — Step 15 Section 6-8 absence validator
    _validate_seo_notes_cell_counts,  # Internal helper — Step 15 exactly-3-<li> validator
    generate_report_sections,  # Phase 4 sequential section-generation pipeline
    _find_table_after_heading,  # Internal helper — Step 15 heading-scoped table lookup
    _validate_location_section,  # Internal helper — PART 7 conditional validation
    build_audit_context,       # Public function under test — Phase 4 context builder
    generate_report,           # Public function under test
    render_competitor_gap_table,  # Step 14 — deterministic SECTION 2.2 Keyword Gap Table renderer
    render_competitor_overview_table,  # Step 14 — deterministic SECTION 2.1 renderer
    render_core_pages_table,   # Step 6 — deterministic PART 1.1 Core Pages Table renderer
    render_content_quality_section,  # Step 8 — deterministic PART 3.3 renderer
    render_critical_high_issues_table,  # Step 7 — deterministic PART 2.1 Issues Table renderer
    render_homepage_elements_table,  # Step 8 — deterministic PART 3.1 renderer
    render_indexability_section,  # Step 7 — deterministic PART 2.5 renderer
    render_location_opportunity_table,  # Step 14 — deterministic SECTION 3.2 renderer
    render_long_tail_keywords_table,  # Step 14 — deterministic SECTION 1.2 renderer
    render_pagespeed_section,  # Step 7 — deterministic PART 2.4 renderer
    render_primary_keywords_table,  # Step 14 — deterministic SECTION 1.1 renderer
    render_priority_pages_table,  # Step 8 — deterministic PART 3.2 renderer
    render_robots_txt_section,  # Step 7 — deterministic PART 2.2 renderer
    render_schema_section,     # Step 7 — deterministic PART 2.6 renderer
    render_sitemap_section,    # Step 7 — deterministic PART 2.3 renderer
    render_subpages_table,     # Step 6 — deterministic PART 1.2 Subpages Table renderer
)
from test.fixtures.frozen_audit_context import build_frozen_audit_context  # Step 18/20 — frozen provider-parity fixture


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings() -> Settings:
    """Settings with a fake API key so validation passes."""
    s = Settings()
    s.gemini_api_key = "FAKE_API_KEY_FOR_TESTS"  # Non-empty so the key check passes
    s.gemini_model = "gemini-1.5-flash"           # Use the default model name
    s.llm_provider = "gemini"                     # Pin to Gemini so tests never hit real Perplexity API
    return s


@pytest.fixture
def prompt_context() -> PromptContext:
    """Minimal PromptContext with a URL placeholder."""
    return PromptContext(
        audit_prompt="You are an SEO consultant. Audit {{website_url}}.",
        seo_skill="Priority: Crawlability, Technical, On-Page, Content.",
        master_report_structure="Report sections: Executive Summary, Technical SEO.",
        ai_guidelines="Never invent findings. Use verified evidence only.",
    )


def _make_evidence(
    url: str = "https://example.com",
    title: str | None = "Test Title",
    meta_desc: str | None = "Test description.",
    h1_tags: list[str] | None = None,
    h2_tags: list[str] | None = None,
    internal_links: int = 5,
    external_links: int = 3,
    images: int = 4,
    missing_alt: int = 1,
    empty_alt: int = 0,
    robots_accessible: bool = True,
    sitemap_accessible: bool = True,
    http_status: int = 200,
    is_https: bool = True,
) -> AuditEvidence:
    """Build an AuditEvidence fixture with sensible defaults."""

    robots: RobotsTxtEvidence = RobotsTxtEvidence(
        is_accessible=robots_accessible,
        http_status=200 if robots_accessible else 404,
        disallow_rules=["/admin", "/checkout"],
        allow_rules=[],
        sitemap_urls=["https://example.com/sitemap.xml"],
        blocks_root_path=False,
    )

    sitemaps: list[SitemapEvidence] = [
        SitemapEvidence(
            url="https://example.com/sitemap.xml",
            is_accessible=sitemap_accessible,
            http_status=200 if sitemap_accessible else 404,
            url_count=10 if sitemap_accessible else 0,
        )
    ]

    images_list = [
        ImageInfo(
            src=f"https://example.com/img{i}.jpg",
            alt="" if i < empty_alt else "Alt text",
            has_alt_attribute=(i >= missing_alt),
        )
        for i in range(images)
    ]

    return AuditEvidence(
        base_url=url,
        final_url=url,
        http_status=http_status,
        is_https=is_https,
        page_title=title,
        page_title_length=len(title) if title else 0,
        meta_description=meta_desc,
        meta_description_length=len(meta_desc) if meta_desc else 0,
        canonical_url=f"{url}/",
        page_language="en",
        h1_tags=h1_tags or ["Main Heading"],
        h2_tags=h2_tags or ["Section A", "Section B"],
        internal_links=[f"{url}/page{i}" for i in range(internal_links)],
        external_links=[f"https://ext{i}.com" for i in range(external_links)],
        images=images_list,
        images_missing_alt_count=missing_alt,
        images_empty_alt_count=empty_alt,
        robots_txt=robots,
        sitemaps=sitemaps,
        unverifiable_fields=[
            "Core Web Vitals — requires Lighthouse",
            "Mobile-friendliness — requires browser rendering",
        ],
    )


def _make_gemini_mock(response_text: str = "# SEO Report\n\nTest report.") -> AsyncMock:
    """Create a mock generate_text() dispatcher call that returns the given text."""
    return AsyncMock(return_value=response_text)


# ---------------------------------------------------------------------------
# Tests for generate_report() — success paths
# ---------------------------------------------------------------------------

class TestGenerateReportSuccess:
    """Tests for successful report generation."""

    async def test_returns_report_result(self, settings: Settings, prompt_context: PromptContext) -> None:
        """generate_report() returns a ReportResult on success."""
        evidence = _make_evidence()
        mock_generate_text = _make_gemini_mock("# SEO Report\n\n## Executive Summary\n\nGood site.")

        with patch("src.services.report_service.generate_text", mock_generate_text):
            result = await generate_report("https://example.com", evidence, prompt_context, settings)

        assert isinstance(result, ReportResult)  # Correct return type

    async def test_markdown_report_populated(self, settings: Settings, prompt_context: PromptContext) -> None:
        """markdown_report field contains the LLM response text."""
        expected_markdown = "# SEO Report\n\nThis is the report."
        evidence = _make_evidence()
        mock_generate_text = _make_gemini_mock(expected_markdown)

        with patch("src.services.report_service.generate_text", mock_generate_text):
            result = await generate_report("https://example.com", evidence, prompt_context, settings)

        assert result.markdown_report == expected_markdown  # LLM text stored exactly

    async def test_audit_id_is_unique_uuid(self, settings: Settings, prompt_context: PromptContext) -> None:
        """Each call produces a different, valid UUID4 audit_id."""
        evidence = _make_evidence()
        mock_generate_text = _make_gemini_mock("# Report")

        with patch("src.services.report_service.generate_text", mock_generate_text):
            result1 = await generate_report("https://example.com", evidence, prompt_context, settings)
            result2 = await generate_report("https://example.com", evidence, prompt_context, settings)

        assert result1.audit_id != result2.audit_id  # Each call gets a unique ID
        # Validate UUID format (8-4-4-4-12 hex characters with dashes)
        import re
        uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        assert re.match(uuid_pattern, result1.audit_id)

    async def test_supplied_audit_id_is_reused_verbatim(
        self, settings: Settings, prompt_context: PromptContext,
    ) -> None:
        """A caller-supplied audit_id (e.g. from an already-created job) is used as-is."""
        evidence = _make_evidence()
        mock_generate_text = _make_gemini_mock("# Report")

        with patch("src.services.report_service.generate_text", mock_generate_text):
            result = await generate_report(
                "https://example.com", evidence, prompt_context, settings,
                audit_id="fixed-job-id-123",
            )

        assert result.audit_id == "fixed-job-id-123"  # Pre-generated ID reused, not replaced

    async def test_normalized_url_stored(self, settings: Settings, prompt_context: PromptContext) -> None:
        """normalized_url in the result matches the input URL."""
        url = "https://www.truelinesolution.com"
        evidence = _make_evidence(url=url)
        mock_generate_text = _make_gemini_mock("# Report")

        with patch("src.services.report_service.generate_text", mock_generate_text):
            result = await generate_report(url, evidence, prompt_context, settings)

        assert result.normalized_url == url  # URL preserved exactly

    async def test_created_at_is_datetime(self, settings: Settings, prompt_context: PromptContext) -> None:
        """created_at is a datetime object representing the audit completion time."""
        evidence = _make_evidence()
        mock_generate_text = _make_gemini_mock("# Report")

        with patch("src.services.report_service.generate_text", mock_generate_text):
            result = await generate_report("https://example.com", evidence, prompt_context, settings)

        assert isinstance(result.created_at, datetime)  # Correct type

    async def test_url_substituted_in_prompt(self, settings: Settings, prompt_context: PromptContext) -> None:
        """{{website_url}} placeholder in the audit prompt is replaced with the real URL."""
        target_url = "https://www.specific-website.com"
        evidence = _make_evidence(url=target_url)

        captured_calls: list[str] = []  # Record what is passed to generate_text

        async def capture_call(system_prompt: str, user_message: str, settings: Settings) -> str:
            captured_calls.append(user_message)  # Store the user message
            return "# Report"

        with patch("src.services.report_service.generate_text", side_effect=capture_call):
            await generate_report(target_url, evidence, prompt_context, settings)

        # The URL should appear in the user message passed to the LLM
        assert target_url in captured_calls[0]

    async def test_retries_once_when_report_has_partial_required_parts(
        self, settings: Settings
    ) -> None:
        """A partial PART structure triggers exactly one retry and returns retry output."""
        evidence = _make_evidence()

        # Template that declares 8 required parts — drives the dynamic heading extraction
        template_with_all_parts = (
            "# PART 1: FULL WEBSITE AUDIT\n"
            "# PART 2: TECHNICAL SEO AUDIT\n"
            "# PART 3: ON-PAGE SEO AUDIT\n"
            "# PART 4: COMPLETE KEYWORD STRATEGY\n"
            "# PART 5: COMPETITOR SEO ANALYSIS\n"
            "# PART 6: OFF-PAGE SEO & AUTHORITY BUILDING\n"
            "# PART 7: CONTENT STRATEGY & CONTENT GAP ANALYSIS\n"
            "# PART 8: AI SEARCH & GENERATIVE ENGINE OPTIMIZATION (GEO)\n"
        )
        ctx = PromptContext(
            audit_prompt="You are an SEO consultant. Audit {{website_url}}.",
            seo_skill="Priority: Crawlability.",
            master_report_structure=template_with_all_parts,
            ai_guidelines="Never invent findings.",
        )

        first_response = "# PART 1: FULL WEBSITE AUDIT\n\nOnly part 1 is present."
        second_response = (
            "# PART 1: FULL WEBSITE AUDIT\n"
            "# PART 2: TECHNICAL SEO AUDIT\n"
            "# PART 3: ON-PAGE SEO AUDIT\n"
            "# PART 4: COMPLETE KEYWORD STRATEGY\n"
            "# PART 5: COMPETITOR SEO ANALYSIS\n"
            "# PART 6: OFF-PAGE SEO & AUTHORITY BUILDING\n"
            "# PART 7: CONTENT STRATEGY & CONTENT GAP ANALYSIS\n"
            "# PART 8: AI SEARCH & GENERATIVE ENGINE OPTIMIZATION (GEO)\n"
        )

        call_index = 0

        async def generate_side_effect(system_prompt: str, user_message: str, settings: Settings) -> str:
            nonlocal call_index
            call_index += 1
            return first_response if call_index == 1 else second_response

        with patch("src.services.report_service.generate_text", side_effect=generate_side_effect) as mock_generate:
            result = await generate_report("https://example.com", evidence, ctx, settings)

        assert mock_generate.call_count == 2
        assert result.markdown_report == second_response

    async def test_retries_once_when_report_contains_banned_phrases(
        self, settings: Settings, prompt_context: PromptContext
    ) -> None:
        """A contaminated first response triggers exactly one retry with clean output."""
        evidence = _make_evidence()

        first_response = "# Report\n\nGenerated using Perplexity. Convert to Google Docs to share."
        second_response = "# Report\n\nClean report with no contamination."

        call_index = 0

        async def generate_side_effect(system_prompt: str, user_message: str, settings: Settings) -> str:
            nonlocal call_index
            call_index += 1
            return first_response if call_index == 1 else second_response

        with patch("src.services.report_service.generate_text", side_effect=generate_side_effect) as mock_generate:
            result = await generate_report(
                "https://example.com", evidence, prompt_context, settings
            )

        assert mock_generate.call_count == 2
        assert result.markdown_report == second_response


# ---------------------------------------------------------------------------
# Tests for _find_banned_phrases()
# ---------------------------------------------------------------------------

class TestFindBannedPhrases:
    """Tests for the contamination/branding-leak detector."""

    def test_returns_empty_list_for_clean_report(self) -> None:
        """A clean report yields no banned phrases."""
        assert _find_banned_phrases("# PART 1: EXECUTIVE SUMMARY\n\nClean content.") == []

    def test_detects_perplexity_self_attribution(self) -> None:
        """Perplexity self-attribution is flagged regardless of case."""
        assert "generated by perplexity" in _find_banned_phrases("Generated by Perplexity AI.")

    def test_detects_google_docs_instruction(self) -> None:
        """'Convert to Google Docs' workflow leakage is flagged."""
        assert "google docs" in _find_banned_phrases("Please convert to Google Docs.")

    def test_detects_multiple_phrases(self) -> None:
        """All contamination phrases present are returned, not just the first."""
        found = _find_banned_phrases(
            "This report was generated by ChatGPT. Please convert to Google Docs and use Comet browser.",
        )
        assert "generated by chatgpt" in found
        assert "google docs" in found
        assert "comet browser" in found

    def test_allows_legitimate_geo_platform_mentions(self) -> None:
        """Regression: PART 9.2 legitimately names ChatGPT/Perplexity/Gemini as AI search targets, not contamination."""
        heading = "## 9.2 AI Search / GEO Visibility (ChatGPT, Perplexity, Gemini)\n\nOptimize for AI answer engines."
        assert _find_banned_phrases(heading) == []


# ---------------------------------------------------------------------------
# Tests for _build_retry_user_message()
# ---------------------------------------------------------------------------

class TestBuildRetryUserMessage:
    """Tests for the combined missing-parts/contamination retry instruction builder."""

    def test_includes_missing_parts_instruction(self) -> None:
        msg = _build_retry_user_message("ORIGINAL", ["# PART 2: TECHNICAL SEO AUDIT"])
        assert "# PART 2: TECHNICAL SEO AUDIT" in msg
        assert "missing required report parts" in msg

    def test_includes_banned_phrase_instruction(self) -> None:
        msg = _build_retry_user_message("ORIGINAL", [], ["perplexity"])
        assert "perplexity" in msg
        assert "forbidden branding" in msg

    def test_includes_both_instructions_when_both_present(self) -> None:
        msg = _build_retry_user_message("ORIGINAL", ["# PART 1: X"], ["chatgpt"])
        assert "# PART 1: X" in msg
        assert "chatgpt" in msg

    def test_preserves_original_message(self) -> None:
        msg = _build_retry_user_message("ORIGINAL_CONTENT", ["# PART 1: X"])
        assert "ORIGINAL_CONTENT" in msg

    def test_includes_location_issue_instruction(self) -> None:
        msg = _build_retry_user_message(
            "ORIGINAL", [], None, ["PART 7 sections 7.2 and 7.3 are both completed"]
        )
        assert "PART 7 sections 7.2 and 7.3 are both completed" in msg
        assert "conditional-section rule" in msg

    def test_includes_citation_issue_instruction(self) -> None:
        msg = _build_retry_user_message(
            "ORIGINAL", [], None, None, ["Row 1 of a Source/Retrieved table is missing a citation value"]
        )
        assert "Row 1 of a Source/Retrieved table is missing a citation value" in msg
        assert "Source or Retrieved value" in msg


# ---------------------------------------------------------------------------
# Tests for _extract_section_body()
# ---------------------------------------------------------------------------

class TestExtractSectionBody:
    """Tests for the subsection text extraction helper."""

    def test_extracts_body_up_to_next_heading(self) -> None:
        report = (
            "## 7.2 Local Location Opportunities\n"
            "Some content here.\n"
            "## 7.3 Audience & Market Expansion\n"
            "Other content.\n"
        )
        body = _extract_section_body(report, "## 7.2")
        assert body == "Some content here."

    def test_extracts_body_to_end_of_document(self) -> None:
        report = "## 7.3 Audience & Market Expansion\nFinal content.\n"
        body = _extract_section_body(report, "## 7.3")
        assert body == "Final content."

    def test_returns_none_when_heading_absent(self) -> None:
        assert _extract_section_body("# PART 1: X\nBody", "## 7.2") is None


# ---------------------------------------------------------------------------
# Tests for _validate_location_section()
# ---------------------------------------------------------------------------

class TestValidateLocationSection:
    """Tests for the SECTION 3 conditional local-vs-market-expansion rule."""

    def test_no_issues_when_only_32_completed(self) -> None:
        report = (
            "## 3.2 Local Location Opportunities\n"
            "Bangalore, Chennai, Hyderabad are strong candidates.\n"
            "## 3.3 Audience & Market Expansion Opportunities\n"
            "Not applicable — business does not target specific locations.\n"
            "---\n"
        )
        assert _validate_location_section(report) == []

    def test_no_issues_when_only_33_completed(self) -> None:
        report = (
            "## 3.2 Local Location Opportunities\n"
            "Not applicable — business is not location-based.\n"
            "## 3.3 Audience & Market Expansion Opportunities\n"
            "SaaS teams and enterprise buyers are the primary expansion audience.\n"
            "---\n"
        )
        assert _validate_location_section(report) == []

    def test_issue_when_both_marked_not_applicable(self) -> None:
        report = (
            "## 3.2 Local Location Opportunities\n"
            "Not applicable — business is not location-based.\n"
            "## 3.3 Audience & Market Expansion Opportunities\n"
            "Not applicable — business does not target specific locations.\n"
            "---\n"
        )
        issues = _validate_location_section(report)
        assert len(issues) == 1
        assert "both marked not applicable" in issues[0]

    def test_issue_when_both_completed(self) -> None:
        report = (
            "## 3.2 Local Location Opportunities\n"
            "Bangalore, Chennai are strong candidates.\n"
            "## 3.3 Audience & Market Expansion Opportunities\n"
            "Enterprise buyers are a strong expansion audience.\n"
            "---\n"
        )
        issues = _validate_location_section(report)
        assert len(issues) == 1
        assert "both completed" in issues[0]

    def test_no_issues_when_section_3_headings_absent(self) -> None:
        """Templates without SECTION 3 (or missing headings) are not flagged here."""
        assert _validate_location_section("# PART 1: FULL WEBSITE AUDIT\nContent.") == []


# ---------------------------------------------------------------------------
# Tests for _find_table_blocks() and _split_table_row()
# ---------------------------------------------------------------------------

class TestFindTableBlocks:
    """Tests for the Markdown table detection helper."""

    def test_finds_single_table(self) -> None:
        report = (
            "# PART 5: KEYWORD OPPORTUNITY STRATEGY\n\n"
            "| Keyword | Source | Retrieved |\n"
            "|---------|--------|-----------|\n"
            "| seo tips | Industry knowledge | 2026-08-04 |\n"
        )
        tables = _find_table_blocks(report)
        assert len(tables) == 1
        assert len(tables[0]) == 3  # header + separator + one data row

    def test_ignores_non_table_lines(self) -> None:
        assert _find_table_blocks("# PART 1: X\nJust a paragraph.\n") == []


class TestSplitTableRow:
    """Tests for the table row cell parser."""

    def test_splits_and_trims_cells(self) -> None:
        assert _split_table_row("| a | b | c |") == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Tests for _validate_citation_columns()
# ---------------------------------------------------------------------------

class TestValidateCitationColumns:
    """Tests for the Source/Retrieved citation-emptiness validator."""

    def test_no_issues_when_all_rows_cited(self) -> None:
        report = (
            "| Keyword | Source | Retrieved |\n"
            "|---------|--------|-----------|\n"
            "| seo tips | Industry knowledge | 2026-08-04 |\n"
        )
        assert _validate_citation_columns(report) == []

    def test_flags_missing_source_cell(self) -> None:
        report = (
            "| Keyword | Source | Retrieved |\n"
            "|---------|--------|-----------|\n"
            "| seo tips |  | 2026-08-04 |\n"
        )
        issues = _validate_citation_columns(report)
        assert len(issues) == 1
        assert "missing a citation value" in issues[0]

    def test_flags_missing_retrieved_cell(self) -> None:
        report = (
            "| Keyword | Source | Retrieved |\n"
            "|---------|--------|-----------|\n"
            "| seo tips | Industry knowledge |  |\n"
        )
        assert len(_validate_citation_columns(report)) == 1

    def test_ignores_tables_without_citation_columns(self) -> None:
        report = (
            "| #Index | URL | Page Name | SEO Notes |\n"
            "|--------|-----|-----------|-----------|\n"
            "| 1 | https://example.com | Home |  |\n"
        )
        assert _validate_citation_columns(report) == []

    def test_excludes_a_table_under_a_named_heading(self) -> None:
        report = (
            "### Primary Keywords Table\n\n"
            "| Keyword | Source | Retrieved |\n"
            "|---------|--------|-----------|\n"
            "| seo tips |  |  |\n"
        )
        assert _validate_citation_columns(report, exclude_table_headings=frozenset({"Primary Keywords Table"})) == []

    def test_excluding_one_heading_still_flags_uncited_rows_elsewhere(self) -> None:
        report = (
            "### Primary Keywords Table\n\n"
            "| Keyword | Source | Retrieved |\n"
            "|---------|--------|-----------|\n"
            "| seo tips |  |  |\n\n"
            "### Some Other Table\n\n"
            "| Keyword | Source | Retrieved |\n"
            "|---------|--------|-----------|\n"
            "| more tips |  |  |\n"
        )
        issues = _validate_citation_columns(report, exclude_table_headings=frozenset({"Primary Keywords Table"}))
        assert len(issues) == 1


# ---------------------------------------------------------------------------
# Tests for generate_report() — error paths
# ---------------------------------------------------------------------------

class TestGenerateReportErrors:
    """Tests for error conditions in report generation."""

    async def test_missing_api_key_raises_value_error(self, prompt_context: PromptContext) -> None:
        """ValueError is raised when GEMINI_API_KEY is not configured."""
        s = Settings()
        s.gemini_api_key = ""       # Empty key — not configured
        s.llm_provider = "gemini"   # Ensure Gemini path is taken regardless of .env
        evidence = _make_evidence()

        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            await generate_report("https://example.com", evidence, prompt_context, s)

    async def test_llm_call_failure_propagates(
        self, settings: Settings, prompt_context: PromptContext
    ) -> None:
        """A provider call failure (wrapped as LLMProviderError by the adapter) propagates unchanged."""
        evidence = _make_evidence()
        broken = AsyncMock(side_effect=LLMProviderError("Gemini call failed: connection refused"))

        with patch("src.services.report_service.generate_text", broken):
            with pytest.raises(LLMProviderError, match="Gemini call failed"):
                await generate_report("https://example.com", evidence, prompt_context, settings)

    async def test_empty_llm_response_raises_provider_error(
        self, settings: Settings, prompt_context: PromptContext
    ) -> None:
        """LLMProviderError is raised when the LLM returns an empty text response."""
        evidence = _make_evidence()
        broken = AsyncMock(side_effect=LLMProviderError("The gemini LLM returned an empty response."))

        with patch("src.services.report_service.generate_text", broken):
            with pytest.raises(LLMProviderError, match="empty response"):
                await generate_report("https://example.com", evidence, prompt_context, settings)


# ---------------------------------------------------------------------------
# Tests for _format_evidence()
# ---------------------------------------------------------------------------

class TestFormatEvidence:
    """Tests for the evidence-to-text formatting helper."""

    def test_includes_url(self) -> None:
        """The formatted evidence block contains the audited URL."""
        evidence = _make_evidence(url="https://example.com")
        text = _format_evidence("https://example.com", evidence)
        assert "https://example.com" in text

    def test_includes_http_status(self) -> None:
        """The HTTP status code is present in the evidence text."""
        evidence = _make_evidence(http_status=200)
        text = _format_evidence("https://example.com", evidence)
        assert "200" in text

    def test_missing_title_flagged(self) -> None:
        """A missing <title> is explicitly noted in the evidence text."""
        evidence = _make_evidence(title=None)
        text = _format_evidence("https://example.com", evidence)
        assert "Missing" in text  # The word "Missing" flags this for the LLM

    def test_title_present_included(self) -> None:
        """A page title is included verbatim in the evidence text."""
        evidence = _make_evidence(title="Best IT Company in Surat")
        text = _format_evidence("https://example.com", evidence)
        assert "Best IT Company in Surat" in text

    def test_missing_meta_description_flagged(self) -> None:
        """A missing meta description is explicitly noted."""
        evidence = _make_evidence(meta_desc=None)
        text = _format_evidence("https://example.com", evidence)
        assert "Missing" in text

    def test_h1_count_included(self) -> None:
        """The number of H1 tags is included in the evidence text."""
        evidence = _make_evidence(h1_tags=["Title One", "Title Two"])
        text = _format_evidence("https://example.com", evidence)
        assert "H1 Tags Found: 2" in text

    def test_images_missing_alt_count_included(self) -> None:
        """The count of images missing ALT attributes is included."""
        evidence = _make_evidence(missing_alt=3)
        text = _format_evidence("https://example.com", evidence)
        assert "3" in text  # Missing alt count is present

    def test_robots_inaccessible_flagged(self) -> None:
        """An inaccessible robots.txt is noted with its HTTP status."""
        evidence = _make_evidence(robots_accessible=False)
        text = _format_evidence("https://example.com", evidence)
        assert "Not accessible" in text

    def test_sitemap_inaccessible_flagged(self) -> None:
        """An inaccessible sitemap is noted with its HTTP status."""
        evidence = _make_evidence(sitemap_accessible=False)
        text = _format_evidence("https://example.com", evidence)
        assert "Not accessible" in text

    def test_unverifiable_fields_included(self) -> None:
        """The unverifiable fields list is present in the evidence text."""
        evidence = _make_evidence()
        text = _format_evidence("https://example.com", evidence)
        # At least one unverifiable field should appear
        assert "Core Web Vitals" in text

    def test_could_not_be_verified_instruction_included(self) -> None:
        """The 'Could not be verified' instruction appears in the evidence."""
        evidence = _make_evidence()
        text = _format_evidence("https://example.com", evidence)
        assert "Could not be verified" in text


# ---------------------------------------------------------------------------
# Tests for _build_user_message()
# ---------------------------------------------------------------------------

class TestBuildUserMessage:
    """Tests for the user message builder."""

    def test_includes_master_report_template_when_provided(self) -> None:
        """The full master template is sent in the user message for verbatim completion."""
        template = "# PART 1: TEMPLATE_MARKER"
        msg = _build_user_message(
            "https://example.com",
            "EVIDENCE",
            master_report_structure=template,
        )

        assert template in msg
        assert "TEMPLATE TO FILL" in msg
        assert "VERIFIED EVIDENCE" in msg

    def test_contains_url(self) -> None:
        """The user message includes the website URL."""
        msg = _build_user_message("https://example.com", "EVIDENCE")
        assert "https://example.com" in msg

    def test_contains_evidence(self) -> None:
        """The user message includes the formatted evidence text."""
        msg = _build_user_message("https://example.com", "EVIDENCE_TEXT_HERE")
        assert "EVIDENCE_TEXT_HERE" in msg

    def test_instructs_no_invention(self) -> None:
        """The user message explicitly instructs the LLM not to invent findings."""
        msg = _build_user_message("https://example.com", "EVIDENCE")
        msg_lower = msg.lower()
        assert "do not invent" in msg_lower or "verified evidence" in msg_lower

    def test_instructs_unverifiable_phrase(self) -> None:
        """The user message instructs the LLM to use the standard unverifiable phrase."""
        msg = _build_user_message("https://example.com", "EVIDENCE")
        assert "Could not be verified in this audit" in msg


# ---------------------------------------------------------------------------
# build_audit_context() — Phase 4 pipeline groundwork
# ---------------------------------------------------------------------------

def _make_page_evidence(**overrides) -> PageEvidence:
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


def _make_site_evidence(**overrides) -> SiteEvidence:
    defaults = dict(
        base_url="https://example.com",
        final_url="https://example.com/",
        homepage=_make_page_evidence(),
    )
    defaults.update(overrides)
    return SiteEvidence(**defaults)


class TestBuildAuditContext:

    async def test_assembles_context_with_score_and_research(self, settings: Settings) -> None:
        site_evidence = _make_site_evidence()
        with patch("src.services.report_service.research_site", AsyncMock(return_value=MagicMock())) as mock_research:
            context = await build_audit_context("https://example.com", site_evidence, settings)

        assert isinstance(context, AuditContext)
        assert context.normalized_url == "https://example.com"
        assert context.site_evidence is site_evidence
        assert isinstance(context.score_breakdown.overall_score, float)
        assert context.audit_id  # non-empty unique id
        mock_research.assert_awaited_once()

    async def test_local_business_detected_and_passed_to_research(self, settings: Settings) -> None:
        location_page = _make_page_evidence(
            url="https://example.com/locations/austin",
            page_type=PageType.LOCATION,
            h1_tags=["Serving Austin, TX"],
        )
        site_evidence = _make_site_evidence(sampled_pages=[location_page])

        with patch("src.services.report_service.research_site", AsyncMock(return_value=MagicMock())) as mock_research:
            context = await build_audit_context("https://example.com", site_evidence, settings)

        assert context.is_local_business is True
        assert context.city_or_region == "Serving Austin, TX"
        mock_research.assert_awaited_once_with(
            "https://example.com", "Example Bakery. Fresh sourdough bread baked daily.",
            settings, True, "Serving Austin, TX",
        )

    async def test_non_local_business_passes_false_and_none(self, settings: Settings) -> None:
        site_evidence = _make_site_evidence()
        with patch("src.services.report_service.research_site", AsyncMock(return_value=MagicMock())) as mock_research:
            context = await build_audit_context("https://example.com", site_evidence, settings)

        assert context.is_local_business is False
        assert context.city_or_region is None
        mock_research.assert_awaited_once_with(
            "https://example.com", "Example Bakery. Fresh sourdough bread baked daily.",
            settings, False, None,
        )

    async def test_supplied_audit_id_is_reused_verbatim(self, settings: Settings) -> None:
        """A caller-supplied audit_id (e.g. from an already-created job) is used as-is."""
        site_evidence = _make_site_evidence()
        with patch("src.services.report_service.research_site", AsyncMock(return_value=MagicMock())):
            context = await build_audit_context(
                "https://example.com", site_evidence, settings, audit_id="fixed-job-id-456",
            )

        assert context.audit_id == "fixed-job-id-456"  # Pre-generated ID reused, not replaced


# ---------------------------------------------------------------------------
# _SECTION_GROUPS / _format_section_evidence() — Phase 4 pipeline
# ---------------------------------------------------------------------------

def _make_context(**overrides) -> AuditContext:
    defaults = dict(
        audit_id="test-audit-id",
        normalized_url="https://example.com",
        site_evidence=_make_site_evidence(),
        score_breakdown=ScoreBreakdown(overall_score=82.5, category_scores=[
            CategoryScore(category="Technical SEO", weight_percent=40.0, score=90.0),
            CategoryScore(category="On-Page SEO", weight_percent=25.0, score=80.0),
        ]),
        research=ResearchBundle(),
        is_local_business=False,
        city_or_region=None,
        created_at=datetime.now(),
    )
    defaults.update(overrides)
    return AuditContext(**defaults)


def _make_finding(category: str, severity: Severity = Severity.MEDIUM, **overrides) -> Finding:
    defaults = dict(
        category=category,
        title="Example finding",
        severity=severity,
        description="Something was found.",
        business_impact="Some business impact.",
        recommendation="Do something about it.",
        effort=EffortLevel.LOW,
        evidence_urls=["https://example.com/"],
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _make_claim(claim: str = "Claim", value: str = "Value") -> ResearchClaim:
    return ResearchClaim(
        claim=claim, value=value, source_url="https://example.com/source",
        source_title="Source", retrieved_date="2026-08-04",
    )


def _make_keyword_opportunity(keyword: str = "Keyword", search_intent: str = "commercial") -> KeywordOpportunity:
    return KeywordOpportunity(
        keyword=keyword, search_intent=search_intent, source_url="https://example.com/source",
        source_title="Source", retrieved_date="2026-08-04",
    )


def _make_competitor(competitor_name: str = "Joe's Bakery", website: str = "https://joesbakery.com") -> CompetitorOverview:
    return CompetitorOverview(
        competitor_name=competitor_name, website=website, focus="Wholesale bread",
        source_url="https://example.com/source", source_title="Source", retrieved_date="2026-08-04",
    )


def _make_gap(keyword: str = "Keyword", your_gap: str = "No online ordering") -> CompetitorGap:
    return CompetitorGap(
        keyword=keyword, competitor_position="Ranks #2", your_gap=your_gap,
        source_url="https://example.com/source", source_title="Source", retrieved_date="2026-08-04",
    )


def _make_location_opportunity(city_or_region: str = "Austin, TX") -> LocationOpportunity:
    return LocationOpportunity(
        city_or_region=city_or_region, primary_keyword="bakery near me", priority="High",
        source_url="https://example.com/source", source_title="Source", retrieved_date="2026-08-04",
    )


class TestSectionGroups:

    def test_covers_every_part_and_section_heading_exactly_once(self) -> None:
        all_headings = [heading for _, headings in _SECTION_GROUPS for heading in headings]
        assert sorted(all_headings) == sorted(
            [f"PART {n}" for n in range(1, 4)] + [f"SECTION {n}" for n in range(1, 6)]
        )
        assert len(all_headings) == len(set(all_headings))

    def test_has_six_groups(self) -> None:
        assert len(_SECTION_GROUPS) == 6

    def test_technical_and_onpage_is_the_only_deterministic_only_group(self) -> None:
        assert _DETERMINISTIC_ONLY_GROUPS == frozenset({"technical_and_onpage"})


class TestFormatSectionEvidence:

    def test_site_inventory_lists_sampled_pages(self) -> None:
        text = _format_section_evidence("site_inventory", _make_context())
        assert "https://example.com/" in text
        assert "Pages sampled and analyzed: 1" in text

    def test_technical_and_onpage_separates_categories(self) -> None:
        findings = [
            _make_finding("Technical SEO", title="No HTTPS"),
            _make_finding("On-Page SEO", title="Missing title"),
        ]
        context = _make_context(score_breakdown=ScoreBreakdown(overall_score=70.0, findings=findings))
        text = _format_section_evidence("technical_and_onpage", context)
        assert "No HTTPS" in text
        assert "Missing title" in text

    def test_technical_and_onpage_reports_no_findings_message(self) -> None:
        text = _format_section_evidence("technical_and_onpage", _make_context())
        assert "No Technical SEO findings were recorded" in text
        assert "No On-Page or Content Quality findings were recorded" in text
        assert "No Core Web Vitals / PageSpeed data was collected for this audit." in text
        assert "No Performance findings were recorded" in text

    def test_technical_and_onpage_includes_performance_evidence_when_available(self) -> None:
        site_evidence = _make_site_evidence(performance=PerformanceEvidence(
            is_available=True, data_source="field", performance_score=88.0,
            largest_contentful_paint_ms=2200.0, cumulative_layout_shift=0.05,
            interaction_to_next_paint_ms=150.0, source_url="https://example.com",
        ))
        findings = [_make_finding("Performance", title="LCP needs improvement")]
        context = _make_context(
            site_evidence=site_evidence, score_breakdown=ScoreBreakdown(overall_score=70.0, findings=findings),
        )
        text = _format_section_evidence("technical_and_onpage", context)
        assert "Performance score: 88/100" in text
        assert "Largest Contentful Paint (LCP): 2.2s" in text
        assert "LCP needs improvement" in text

    def test_structured_data_and_off_page_excludes_performance_findings(self) -> None:
        findings = [_make_finding("Performance", title="LCP needs improvement")]
        context = _make_context(score_breakdown=ScoreBreakdown(overall_score=70.0, findings=findings))
        text = _format_section_evidence("structured_data_and_off_page", context)
        assert "LCP needs improvement" not in text

    def test_keyword_strategy_formats_claims_with_citation(self) -> None:
        research = ResearchBundle(primary_keywords=[_make_keyword_opportunity("Keyword", "commercial")])
        text = _format_section_evidence("keyword_strategy", _make_context(research=research))
        assert "Keyword" in text
        assert "Source" in text and "retrieved 2026-08-04" in text

    def test_keyword_strategy_empty_message(self) -> None:
        text = _format_section_evidence("keyword_strategy", _make_context())
        assert "No primary keyword opportunities were found" in text

    def test_competitor_analysis_includes_both_lists(self) -> None:
        research = ResearchBundle(
            competitors=[_make_competitor("Joe's Bakery")],
            competitor_analysis=[_make_gap(your_gap="No online ordering")],
        )
        text = _format_section_evidence("competitor_analysis", _make_context(research=research))
        assert "Joe's Bakery" in text
        assert "No online ordering" in text

    def test_location_section_uses_local_demand_when_local_with_region(self) -> None:
        research = ResearchBundle(local_demand=[_make_location_opportunity("Austin, TX")])
        context = _make_context(research=research, is_local_business=True, city_or_region="Austin, TX")
        text = _format_section_evidence("location_or_market_expansion", context)
        assert "Local/service-area business" in text
        assert "Austin, TX" in text

    def test_location_section_uses_audience_expansion_when_not_local(self) -> None:
        research = ResearchBundle(audience_expansion=[_make_claim("Segment", "Wholesale bakeries")])
        context = _make_context(research=research, is_local_business=False, city_or_region=None)
        text = _format_section_evidence("location_or_market_expansion", context)
        assert "Not local/service-area" in text
        assert "Wholesale bakeries" in text

    def test_location_section_reports_insufficient_location_evidence_when_local_with_no_region(self) -> None:
        """
        A local/service-area business with no detected region must get a distinct,
        honest message - never silently reused non-local "Not local/service-area"
        messaging or a fabricated placeholder region.
        """
        research = ResearchBundle(audience_expansion=[_make_claim("Segment", "Wholesale bakeries")])
        context = _make_context(research=research, is_local_business=True, city_or_region=None)
        text = _format_section_evidence("location_or_market_expansion", context)
        assert "insufficient_location_evidence" in text
        assert "Not local/service-area" not in text
        assert "Wholesale bakeries" not in text


    def test_structured_data_and_off_page_includes_authority_claims(self) -> None:
        research = ResearchBundle(authority_opportunities=[_make_claim("Directory", "Local business directory")])
        text = _format_section_evidence("structured_data_and_off_page", _make_context(research=research))
        assert "Local business directory" in text

    def test_structured_data_and_off_page_includes_brand_presence_claims(self) -> None:
        research = ResearchBundle(brand_presence=[_make_claim("Brand Presence", "Listed on Yelp")])
        text = _format_section_evidence("structured_data_and_off_page", _make_context(research=research))
        assert "Listed on Yelp" in text

    def test_structured_data_and_off_page_reports_no_brand_presence_message(self) -> None:
        text = _format_section_evidence("structured_data_and_off_page", _make_context())
        assert "No existing brand presence signals were found" in text

    def test_unknown_group_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _format_section_evidence("not_a_real_group", _make_context())


# ---------------------------------------------------------------------------
# _extract_part_templates() / _build_section_user_message() — Phase 4 pipeline
# ---------------------------------------------------------------------------

_SAMPLE_MASTER_REPORT_STRUCTURE = (
    "# PART 1: EXECUTIVE SUMMARY\n\nSummary body.\n\n"
    "# PART 2: FULL WEBSITE AUDIT\n\n| URL | Type |\n|---|---|\n\n"
    "# PART 3: TECHNICAL SEO AUDIT\n\nTechnical body.\n\n"
    "# PART 4: ON-PAGE & CONTENT AUDIT\n\nOn-page body.\n\n"
    "# PART 5: KEYWORD OPPORTUNITY STRATEGY\n\nKeyword body.\n"
)


class TestExtractPartTemplates:

    def test_extracts_single_part_body(self) -> None:
        result = _extract_part_templates(_SAMPLE_MASTER_REPORT_STRUCTURE, ("PART 3",))
        assert result.startswith("# PART 3: TECHNICAL SEO AUDIT")
        assert "Technical body." in result
        assert "PART 4" not in result

    def test_extracts_multiple_parts_in_order(self) -> None:
        result = _extract_part_templates(_SAMPLE_MASTER_REPORT_STRUCTURE, ("PART 3", "PART 4"))
        assert result.index("PART 3") < result.index("PART 4")
        assert "Technical body." in result
        assert "On-page body." in result

    def test_missing_part_is_silently_skipped(self) -> None:
        result = _extract_part_templates(_SAMPLE_MASTER_REPORT_STRUCTURE, ("PART 99",))
        assert result == ""


class TestBuildSectionUserMessage:

    def test_includes_only_requested_headings_instruction(self) -> None:
        msg = _build_section_user_message(
            "https://example.com", ("PART 3", "PART 4"), "Some evidence text", _SAMPLE_MASTER_REPORT_STRUCTURE,
        )
        assert "Write ONLY PART 3, PART 4" in msg
        assert "Technical body." in msg
        assert "On-page body." in msg
        assert "Some evidence text" in msg
        assert "https://example.com" in msg

    def test_omits_deterministic_table_note_by_default(self) -> None:
        msg = _build_section_user_message(
            "https://example.com", ("PART 3",), "Some evidence text", _SAMPLE_MASTER_REPORT_STRUCTURE,
        )
        assert "already finalized" not in msg

    def test_includes_deterministic_table_note_when_supplied(self) -> None:
        msg = _build_section_user_message(
            "https://example.com", ("PART 3",), "Some evidence text", _SAMPLE_MASTER_REPORT_STRUCTURE,
            deterministic_table_note="Core Pages Table is already finalized.",
        )
        assert "Core Pages Table is already finalized." in msg


# ---------------------------------------------------------------------------
# _replace_heading_block() / _inject_inventory_tables() — Step 9 table injection
# ---------------------------------------------------------------------------

class TestReplaceHeadingBlock:

    def test_overwrites_existing_content_under_the_heading(self) -> None:
        markdown = "### Core Pages Table\n\nInvented row here.\n\n## 1.2 Subpages\n"
        result = _replace_heading_block(markdown, "Core Pages Table", "| real | table |")
        assert "Invented row here." not in result
        assert "| real | table |" in result
        assert "## 1.2 Subpages" in result  # Content after the next heading is preserved

    def test_appends_the_heading_when_missing(self) -> None:
        markdown = "# PART 1: FULL WEBSITE AUDIT\n\nSome narrative only.\n"
        result = _replace_heading_block(markdown, "Core Pages Table", "| real | table |")
        assert "Some narrative only." in result
        assert "### Core Pages Table" in result
        assert "| real | table |" in result


class TestInjectInventoryTables:

    def test_forces_both_tables_regardless_of_model_output(self) -> None:
        markdown = (
            "# PART 1: FULL WEBSITE AUDIT\n\n"
            "### Core Pages Table\n\nInvented core row.\n\n"
            "### Subpages Table\n\nInvented subpage row.\n"
        )
        result = _inject_inventory_tables(markdown, "| real core |", "| real subpages |")
        assert "Invented core row." not in result
        assert "Invented subpage row." not in result
        assert "| real core |" in result
        assert "| real subpages |" in result


# ---------------------------------------------------------------------------
# _render_technical_and_onpage_section() — Step 9 deterministic PART 2/3 renderer
# ---------------------------------------------------------------------------

class TestRenderTechnicalAndOnpageSection:

    def test_renders_both_part_headings_verbatim(self) -> None:
        section = _render_technical_and_onpage_section(_make_context())
        assert section.startswith("# PART 2: TECHNICAL SEO AUDIT")
        assert "# PART 3: ON-PAGE & CONTENT AUDIT" in section

    def test_includes_every_subsection_heading(self) -> None:
        section = _render_technical_and_onpage_section(_make_context())
        for heading in (
            "## 2.1 Critical & High Priority Issues",
            "## 2.2 Robots.txt Analysis",
            "## 2.3 XML Sitemap Analysis",
            "## 2.4 Core Web Vitals & Page Speed",
            "## 2.5 Indexability & Crawlability",
            "## 2.6 Structured Data Status",
            "## 3.1 Homepage On-Page Review",
            "## 3.2 Priority Pages On-Page Review",
            "## 3.3 Content Quality Assessment",
        ):
            assert heading in section

    def test_reflects_deterministic_findings_from_context(self) -> None:
        context = _make_context(
            score_breakdown=ScoreBreakdown(overall_score=50.0, category_scores=[], findings=[
                _make_finding("Technical SEO", title="No HTTPS", severity=Severity.CRITICAL),
            ]),
        )
        section = _render_technical_and_onpage_section(context)
        assert "No HTTPS" in section


# ---------------------------------------------------------------------------
# generate_report_sections() — Phase 4 sequential section-generation loop
# ---------------------------------------------------------------------------

class TestGenerateReportSections:

    @pytest.fixture
    def full_prompt_context(self) -> PromptContext:
        return PromptContext(
            audit_prompt="Audit {{website_url}}.",
            seo_skill="Priority: Crawlability, Technical, On-Page, Content.",
            master_report_structure=(
                "# PART 1: FULL WEBSITE AUDIT\n\nBody.\n\n"
                "# PART 2: TECHNICAL SEO AUDIT\n\nBody.\n\n"
                "# PART 3: ON-PAGE & CONTENT AUDIT\n\nBody.\n\n"
                "# SECTION 1: KEYWORD OPPORTUNITY STRATEGY\n\nBody.\n\n"
                "# SECTION 2: COMPETITOR ANALYSIS\n\nBody.\n\n"
                "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n## 3.2\nBody.\n## 3.3\nNot applicable.\n\n"
                "# SECTION 4: STRUCTURED DATA RECOMMENDATIONS\n\nBody.\n\n"
                "# SECTION 5: OFF-PAGE SEO & GEO STRATEGY\n\nBody.\n"
            ),
            ai_guidelines="Never invent findings. Use verified evidence only.",
        )

    async def test_generates_one_section_per_group_in_order(
        self, settings: Settings, full_prompt_context: PromptContext,
    ) -> None:
        context = _make_context()

        async def fake_call_llm(system_prompt: str, user_message: str, settings: Settings) -> str:
            headings = re.findall(r"# (?:PART|SECTION) \d+:[^\n]*", user_message)
            if any(h.startswith("# SECTION 3:") for h in headings):
                return "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n## 3.2\nBody.\n## 3.3\nNot applicable.\n"
            return "\n\n".join(f"{h}\n\nGenerated content." for h in headings)

        with patch("src.services.report_service.generate_text", side_effect=fake_call_llm):
            sections = await generate_report_sections(context, full_prompt_context, settings)

        assert list(sections.keys()) == [name for name, _ in _SECTION_GROUPS]
        assert "Generated content." in sections["site_inventory"]
        assert list(sections.keys())[-1] == "structured_data_and_off_page"

    async def test_retries_once_when_required_heading_missing(
        self, settings: Settings, full_prompt_context: PromptContext,
    ) -> None:
        context = _make_context()
        inventory_call_count = {"n": 0}

        async def fake_call_llm(system_prompt: str, user_message: str, settings: Settings) -> str:
            headings = re.findall(r"# (?:PART|SECTION) \d+:[^\n]*", user_message)
            if any(h.startswith("# PART 1:") for h in headings):
                inventory_call_count["n"] += 1
                if inventory_call_count["n"] == 1:
                    return "Missing the PART 1 heading on purpose."
                return "# PART 1: FULL WEBSITE AUDIT — ALL PAGES & URLs\n\nFixed on retry."
            if any(h.startswith("# SECTION 3:") for h in headings):
                return "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n## 3.2\nBody.\n## 3.3\nNot applicable.\n"
            return "\n\n".join(f"{h}\n\nOK." for h in headings)

        # technical_and_onpage never calls the LLM (deterministic-only group), so it is
        # excluded from the +1 retry-call-count math below.
        with patch("src.services.report_service.generate_text", side_effect=fake_call_llm) as mock_call:
            sections = await generate_report_sections(context, full_prompt_context, settings)

        assert inventory_call_count["n"] == 2  # First call missed a heading, second call (retry) fixed it
        assert "Fixed on retry" in sections["site_inventory"]
        assert mock_call.await_count == (len(_SECTION_GROUPS) - 1) + 1  # -1 for the deterministic-only group, +1 retry

    async def test_substitutes_deterministic_fallback_when_retry_still_fails(
        self, settings: Settings, full_prompt_context: PromptContext,
    ) -> None:
        context = _make_context()

        async def always_broken(system_prompt: str, user_message: str, settings: Settings) -> str:
            return "No headings here at all."

        with patch("src.services.report_service.generate_text", side_effect=always_broken):
            sections = await generate_report_sections(context, full_prompt_context, settings)

        # Every group is still present in the result.
        assert set(sections.keys()) == {name for name, _ in _SECTION_GROUPS}
        # technical_and_onpage never calls the (broken) LLM at all — it is rendered
        # deterministically regardless of how badly the model behaves everywhere else.
        assert "# PART 2: TECHNICAL SEO AUDIT" in sections["technical_and_onpage"]
        assert "# PART 3: ON-PAGE & CONTENT AUDIT" in sections["technical_and_onpage"]

        # Every LLM-generating group that still failed validation after the retry gets the
        # safe deterministic fallback narrative substituted, never the malformed LLM text.
        llm_groups = {name for name, _ in _SECTION_GROUPS} - {"technical_and_onpage"}
        for name in llm_groups:
            assert sections[name] != "No headings here at all."
            assert "No headings here at all." not in sections[name]
            assert "Automated narrative generation for this section did not pass validation" in sections[name]

        assert sections["site_inventory"].startswith("# PART 1:")
        assert "### Core Pages Table" in sections["site_inventory"]
        assert "### Subpages Table" in sections["site_inventory"]
        assert sections["keyword_strategy"].startswith("# SECTION 1:")
        assert "### Primary Keywords Table" in sections["keyword_strategy"]
        assert "### Long-Tail Keywords Table" in sections["keyword_strategy"]
        assert sections["competitor_analysis"].startswith("# SECTION 2:")
        assert "### Competitor Overview Table" in sections["competitor_analysis"]
        assert "### Keyword Gap Table" in sections["competitor_analysis"]
        # location_or_market_expansion's fallback still satisfies the "exactly one of
        # 3.2/3.3 marked not applicable" rule (this default context is not local).
        assert "## 3.2 Local Location Opportunities" in sections["location_or_market_expansion"]
        assert "## 3.3 Audience & Market Expansion" in sections["location_or_market_expansion"]
        assert "Not applicable" in sections["location_or_market_expansion"]
        assert sections["structured_data_and_off_page"].startswith("# SECTION 4:")
        assert "# SECTION 5:" in sections["structured_data_and_off_page"]

    async def test_keyword_and_competitor_tables_always_injected(
        self, settings: Settings, full_prompt_context: PromptContext,
    ) -> None:
        context = _make_context(
            research=ResearchBundle(
                primary_keywords=[_make_keyword_opportunity(keyword="artisan bread austin")],
                competitors=[_make_competitor(competitor_name="Joe's Bakery")],
                research_statuses={"primary_keywords": ResearchStatus.SUCCESS, "competitors": ResearchStatus.SUCCESS},
            ),
        )

        async def fake_call_llm(system_prompt: str, user_message: str, settings: Settings) -> str:
            headings = re.findall(r"# (?:PART|SECTION) \d+:[^\n]*", user_message)
            if any(h.startswith("# SECTION 1:") for h in headings):
                return (
                    "# SECTION 1: KEYWORD OPPORTUNITY STRATEGY\n\n### Primary Keywords Table\n\nModel row.\n\n"
                    "### Long-Tail Keywords Table\n\nModel row.\n"
                )
            if any(h.startswith("# SECTION 2:") for h in headings):
                return (
                    "# SECTION 2: COMPETITOR ANALYSIS\n\n### Competitor Overview Table\n\nModel row.\n\n"
                    "### Keyword Gap Table\n\nModel row.\n"
                )
            if any(h.startswith("# SECTION 3:") for h in headings):
                return (
                    "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n"
                    "## 3.1 Applicability Assessment\n\nNot a local business.\n\n"
                    "## 3.2 Local Location Opportunities\n\nNot applicable.\n\n"
                    "## 3.3 Audience & Market Expansion Opportunities\n\nSome narrative.\n"
                )
            return "\n\n".join(f"{h}\n\nGenerated content." for h in headings)

        with patch("src.services.report_service.generate_text", side_effect=fake_call_llm):
            sections = await generate_report_sections(context, full_prompt_context, settings)

        assert "artisan bread austin" in sections["keyword_strategy"]
        assert "Model row." not in sections["keyword_strategy"]
        assert "Joe's Bakery" in sections["competitor_analysis"]
        assert "Model row." not in sections["competitor_analysis"]
        # Not a local business with a known region — the Location Opportunity Table is
        # never fabricated, so 3.2's model-authored "Not applicable" text is left alone.
        assert "Not applicable" in sections["location_or_market_expansion"]

    async def test_location_table_injected_only_when_local_business_with_known_region(
        self, settings: Settings, full_prompt_context: PromptContext,
    ) -> None:
        context = _make_context(
            is_local_business=True,
            city_or_region="Austin, TX",
            research=ResearchBundle(
                local_demand=[_make_location_opportunity(city_or_region="Austin, TX")],
                research_statuses={"local_demand": ResearchStatus.SUCCESS},
            ),
        )

        async def fake_call_llm(system_prompt: str, user_message: str, settings: Settings) -> str:
            headings = re.findall(r"# (?:PART|SECTION) \d+:[^\n]*", user_message)
            if any(h.startswith("# SECTION 3:") for h in headings):
                return (
                    "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n"
                    "## 3.1 Applicability Assessment\n\nLocal business serving Austin, TX.\n\n"
                    "## 3.2 Local Location Opportunities\n\n### Location Opportunity Table\n\nModel row.\n\n"
                    "## 3.3 Audience & Market Expansion Opportunities\n\nNot applicable.\n"
                )
            return "\n\n".join(f"{h}\n\nGenerated content." for h in headings)

        with patch("src.services.report_service.generate_text", side_effect=fake_call_llm):
            sections = await generate_report_sections(context, full_prompt_context, settings)

        assert "Austin, TX" in sections["location_or_market_expansion"]
        assert "Model row." not in sections["location_or_market_expansion"].split("Location Opportunity Table")[-1]

    async def test_deterministic_table_notes_sent_for_each_llm_group(
        self, settings: Settings, full_prompt_context: PromptContext,
    ) -> None:
        context = _make_context(is_local_business=True, city_or_region="Austin, TX")
        sent_user_messages: dict[str, str] = {}

        async def fake_call_llm(system_prompt: str, user_message: str, settings: Settings) -> str:
            headings = re.findall(r"# (?:PART|SECTION) \d+:[^\n]*", user_message)
            for heading in headings:
                sent_user_messages.setdefault(heading, user_message)
            return "\n\n".join(
                f"{h}\n\nGenerated content.\n\n## 3.2\nNot applicable.\n## 3.3\nBody.\n" for h in headings
            )

        with patch("src.services.report_service.generate_text", side_effect=fake_call_llm):
            await generate_report_sections(context, full_prompt_context, settings)

        site_inventory_msg = next(msg for heading, msg in sent_user_messages.items() if heading.startswith("# PART 1:"))
        keyword_msg = next(msg for heading, msg in sent_user_messages.items() if heading.startswith("# SECTION 1:"))
        competitor_msg = next(msg for heading, msg in sent_user_messages.items() if heading.startswith("# SECTION 2:"))
        location_msg = next(msg for heading, msg in sent_user_messages.items() if heading.startswith("# SECTION 3:"))

        assert "Core Pages Table (1.1) and Subpages Table (1.2)" in site_inventory_msg
        assert "Primary Keywords Table (1.1) and Long-Tail Keywords Table (1.2)" in keyword_msg
        assert "Competitor Overview Table (2.1) and Keyword Gap Table (2.2)" in competitor_msg
        assert "Location Opportunity Table (3.2)" in location_msg

    async def test_own_draft_citation_gap_in_a_deterministic_table_does_not_trigger_a_retry(
        self, settings: Settings, full_prompt_context: PromptContext,
    ) -> None:
        context = _make_context(
            research=ResearchBundle(
                primary_keywords=[_make_keyword_opportunity(keyword="artisan bread austin")],
                research_statuses={"primary_keywords": ResearchStatus.SUCCESS},
            ),
        )
        call_count = {"n": 0}

        async def fake_call_llm(system_prompt: str, user_message: str, settings: Settings) -> str:
            headings = re.findall(r"# (?:PART|SECTION) \d+:[^\n]*", user_message)
            if any(h.startswith("# SECTION 1:") for h in headings):
                call_count["n"] += 1
                return (
                    "# SECTION 1: KEYWORD OPPORTUNITY STRATEGY\n\n"
                    "### Primary Keywords Table\n\n"
                    "| Keyword | Source | Retrieved |\n|---|---|---|\n| bread |  |  |\n\n"
                    "### Long-Tail Keywords Table\n\n"
                    "| Keyword | Source | Retrieved |\n|---|---|---|\n| bread near me |  |  |\n"
                )
            return "\n\n".join(f"{h}\n\nGenerated content." for h in headings)

        with patch("src.services.report_service.generate_text", side_effect=fake_call_llm):
            sections = await generate_report_sections(context, full_prompt_context, settings)

        # Only the initial call — the model's own uncited draft table is never checked,
        # since it is discarded and overwritten by the verified table regardless.
        assert call_count["n"] == 1
        assert "artisan bread austin" in sections["keyword_strategy"]


# ---------------------------------------------------------------------------
# _build_fallback_section_markdown() — Step 16 deterministic fallback narrative
# ---------------------------------------------------------------------------

class TestBuildFallbackSectionMarkdown:

    def test_site_inventory_fallback_has_required_heading_and_no_banned_phrases(self) -> None:
        context = _make_context(score_breakdown=ScoreBreakdown(
            overall_score=70.0, findings=[_make_finding("Technical SEO")],
        ))
        markdown = _build_fallback_section_markdown("site_inventory", ("PART 1",), context)

        assert markdown.startswith("# PART 1:")
        assert not _find_banned_phrases(markdown)
        assert not _missing_required_report_parts(markdown, ("# PART 1:",))
        assert "Example finding" in markdown  # derived from context.score_breakdown.findings

    def test_keyword_strategy_fallback_includes_primary_and_long_tail_opportunities(self) -> None:
        context = _make_context(research=ResearchBundle(
            primary_keywords=[_make_keyword_opportunity(keyword="artisan bread austin")],
        ))
        markdown = _build_fallback_section_markdown("keyword_strategy", ("SECTION 1",), context)

        assert markdown.startswith("# SECTION 1:")
        assert "artisan bread austin" in markdown

    def test_competitor_analysis_fallback_includes_competitors(self) -> None:
        context = _make_context(research=ResearchBundle(
            competitors=[_make_competitor(competitor_name="Joe's Bakery")],
        ))
        markdown = _build_fallback_section_markdown("competitor_analysis", ("SECTION 2",), context)

        assert markdown.startswith("# SECTION 2:")
        assert "Joe's Bakery" in markdown

    def test_location_fallback_marks_exactly_one_of_3_2_3_3_not_applicable_when_non_local(self) -> None:
        context = _make_context(is_local_business=False, city_or_region=None)
        markdown = _build_fallback_section_markdown("location_or_market_expansion", ("SECTION 3",), context)

        assert not _validate_location_section(markdown)

    def test_location_fallback_marks_exactly_one_of_3_2_3_3_not_applicable_when_local_with_region(self) -> None:
        context = _make_context(is_local_business=True, city_or_region="Austin, TX")
        markdown = _build_fallback_section_markdown("location_or_market_expansion", ("SECTION 3",), context)

        assert not _validate_location_section(markdown)
        assert "Austin, TX" in markdown

    def test_location_fallback_marks_exactly_one_of_3_2_3_3_not_applicable_when_local_without_region(self) -> None:
        # Step 13's insufficient_location_evidence case.
        context = _make_context(is_local_business=True, city_or_region=None)
        markdown = _build_fallback_section_markdown("location_or_market_expansion", ("SECTION 3",), context)

        assert not _validate_location_section(markdown)

    def test_structured_data_and_off_page_fallback_has_both_required_headings(self) -> None:
        context = _make_context()
        markdown = _build_fallback_section_markdown(
            "structured_data_and_off_page", ("SECTION 4", "SECTION 5"), context,
        )

        assert markdown.startswith("# SECTION 4:")
        assert "# SECTION 5:" in markdown
        assert not _missing_required_report_parts(markdown, ("# SECTION 4:", "# SECTION 5:"))


# ---------------------------------------------------------------------------
# assemble_report_markdown() — Phase 4 final assembly
# ---------------------------------------------------------------------------

class TestAssembleReportMarkdown:

    def test_orders_groups_by_declaration_order(self) -> None:
        sections = {
            "site_inventory": "# PART 1: FULL WEBSITE AUDIT\n\nInventory body.",
            "technical_and_onpage": "# PART 2: TECHNICAL SEO AUDIT\n\nTechnical body.",
            "keyword_strategy": "# SECTION 1: KEYWORD OPPORTUNITY STRATEGY\n\nKeyword body.",
            "competitor_analysis": "# SECTION 2: COMPETITOR ANALYSIS\n\nCompetitor body.",
            "location_or_market_expansion": "# SECTION 3: LOCATION STRATEGY\n\nLocation body.",
            "structured_data_and_off_page": (
                "# SECTION 4: STRUCTURED DATA\n\nStructured data body.\n\n"
                "# SECTION 5: OFF-PAGE SEO\n\nOff-page body."
            ),
        }
        markdown = assemble_report_markdown(sections)

        assert markdown.index("PART 1") < markdown.index("PART 2")
        assert markdown.index("PART 2") < markdown.index("SECTION 1")
        assert markdown.index("SECTION 1") < markdown.index("SECTION 2")
        assert markdown.index("SECTION 2") < markdown.index("SECTION 3")
        assert markdown.index("SECTION 3") < markdown.index("SECTION 4")
        assert markdown.index("SECTION 4") < markdown.index("SECTION 5")

    def test_joins_all_section_bodies(self) -> None:
        sections = {
            "site_inventory": "Inventory text.",
            "structured_data_and_off_page": "Off-page text.",
        }
        markdown = assemble_report_markdown(sections)
        assert "Inventory text." in markdown
        assert "Off-page text." in markdown

    def test_handles_partial_sections_dict(self) -> None:
        # A subset of groups (e.g. an in-progress/partially-checkpointed run) still assembles cleanly.
        sections = {"technical_and_onpage": "Technical only."}
        assert assemble_report_markdown(sections) == "Technical only."


# ---------------------------------------------------------------------------
# _deduplicate_table_rows() — Phase 4/16 cross-call duplicate recommendation removal
# ---------------------------------------------------------------------------

class TestDeduplicateTableRows:

    def test_drops_exact_duplicate_data_rows(self) -> None:
        markdown = (
            "| Timeframe | Action | Owner | Priority |\n"
            "|---|---|---|---|\n"
            "| 30 days | Add meta descriptions | SEO Team | High |\n"
            "| 60 days | Add meta descriptions | SEO Team | High |\n"
        )
        result = _deduplicate_table_rows(markdown)
        assert result.count("Add meta descriptions") == 1

    def test_deduplication_is_case_insensitive_and_whitespace_tolerant(self) -> None:
        markdown = (
            "| Action | Priority |\n"
            "|---|---|\n"
            "| Fix broken links | High |\n"
            "|  fix broken links  | High |\n"
        )
        result = _deduplicate_table_rows(markdown)
        assert result.count("broken links") == 1

    def test_preserves_distinct_rows(self) -> None:
        markdown = (
            "| Action | Priority |\n"
            "|---|---|\n"
            "| Fix broken links | High |\n"
            "| Compress images | Medium |\n"
        )
        result = _deduplicate_table_rows(markdown)
        assert "Fix broken links" in result
        assert "Compress images" in result

    def test_leaves_non_table_content_untouched(self) -> None:
        markdown = "# PART 10: PRIORITIZED EXECUTION PLAN\n\nSome intro text.\n"
        assert _deduplicate_table_rows(markdown).strip() == markdown.strip()

    def test_preserves_header_and_separator_rows(self) -> None:
        markdown = (
            "| Action | Priority |\n"
            "|---|---|\n"
            "| Fix broken links | High |\n"
        )
        result = _deduplicate_table_rows(markdown)
        assert result.startswith("| Action | Priority |\n|---|---|\n")

    def test_element_column_prevents_wrongly_collapsing_shared_recommendation(self) -> None:
        """Homepage Elements Table: 'Element' differs, so identical 'No change needed.' rows must both survive."""
        markdown = (
            "| Element | Current | Issue | Recommended |\n"
            "|---------|---------|-------|-------------|\n"
            "| Title Tag | Example Bakery | None | No change needed. |\n"
            "| Canonical Tag | https://example.com/ | None | No change needed. |\n"
        )
        result = _deduplicate_table_rows(markdown)
        assert result.count("No change needed.") == 2
        assert "Title Tag" in result
        assert "Canonical Tag" in result

    def test_page_column_prevents_wrongly_collapsing_shared_recommendation(self) -> None:
        """Priority Pages Table: 'Page' differs, so two different pages sharing a recommendation must both survive."""
        markdown = (
            "| Page | Title Tag Issue | Meta Description Issue | Heading Issue | Recommendation |\n"
            "|------|------------------|-------------------------|----------------|-----------------|\n"
            "| https://example.com/a | None | None | None | No immediate action needed. |\n"
            "| https://example.com/b | None | None | None | No immediate action needed. |\n"
        )
        result = _deduplicate_table_rows(markdown)
        assert result.count("No immediate action needed.") == 2


def _render_deterministic_blocks_body(context: AuditContext) -> str:
    """
    Assemble every deterministic block Step 15's validators require, from the
    real renderers (never hand-typed), so a "well-formed" test report stays
    well-formed even as new section-aware validators are added.

    Deliberately avoids _render_technical_and_onpage_section()'s own "## 3.2"/
    "## 3.3" subsection headings, which would collide with
    _validate_location_section()'s "## 3.2"/"## 3.3" prefix match on SECTION 3.
    """
    inventory = build_inventory_section_data(context)
    technical = build_technical_section_data(context)
    on_page = build_on_page_section_data(context)
    research = context.research

    return "\n\n".join([
        "### Core Pages Table\n\n" + render_core_pages_table(inventory),
        "### Subpages Table\n\n" + render_subpages_table(inventory),
        render_critical_high_issues_table(technical.findings),
        render_robots_txt_section(technical.robots_txt),
        render_sitemap_section(technical.sitemaps),
        render_pagespeed_section(technical.performance),
        render_indexability_section(technical.pages, technical.robots_txt),
        render_schema_section(technical.detected_schema_types),
        render_homepage_elements_table(on_page.homepage),
        render_priority_pages_table(on_page.priority_pages),
        render_content_quality_section(on_page.content_findings),
        render_primary_keywords_table(research.primary_keywords, research.research_statuses.get("primary_keywords")),
        render_long_tail_keywords_table(research.long_tail_keywords, research.research_statuses.get("long_tail_keywords")),
        render_competitor_overview_table(research.competitors, research.research_statuses.get("competitors")),
        render_competitor_gap_table(research.competitor_analysis, research.research_statuses.get("competitor_analysis")),
    ])


# ---------------------------------------------------------------------------
# validate_assembled_report() — Phase 4 report-level validation
# ---------------------------------------------------------------------------

class TestValidateAssembledReport:

    _TEMPLATE = "# PART 1: EXECUTIVE SUMMARY\n\n# PART 2: FULL WEBSITE AUDIT\n"

    def test_well_formed_report_has_no_issues(self) -> None:
        context = _make_context()
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nOverall score: 82.5/100.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nTechnical SEO: 90.0. On-Page SEO: 80.0.\n\n"
            + _render_deterministic_blocks_body(context)
        )
        assert validate_assembled_report(report, self._TEMPLATE, context) == []

    def test_flags_missing_required_heading(self) -> None:
        report = "# PART 1: EXECUTIVE SUMMARY\n\nBody only, PART 2 missing.\n"
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("Missing required PART headings" in issue and "PART 2" in issue for issue in issues)

    def test_flags_banned_phrases(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nGenerated with ChatGPT.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("banned contamination phrases" in issue for issue in issues)

    def test_flags_missing_citation_in_source_table(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nBody.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\n"
            "| Claim | Source | Retrieved |\n|---|---|---|\n| Some claim | | 2026-01-01 |\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("missing a citation value" in issue for issue in issues)

    def test_flags_empty_table_cell_outside_citation_tables(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nBody.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\n"
            "| URL | Type |\n|---|---|\n| https://example.com/ | |\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("empty cell" in issue for issue in issues)

    def test_flags_section_3_conditional_violation(self) -> None:
        template = self._TEMPLATE + "# SECTION 3: LOCATION STRATEGY\n"
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nBody.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n\n"
            "# SECTION 3: LOCATION STRATEGY\n\n## 3.2\nCompleted.\n## 3.3\nAlso completed.\n"
        )
        issues = validate_assembled_report(report, template, _make_context())
        assert any("3.2" in issue and "3.3" in issue for issue in issues)

    def test_allows_urls_from_crawl_evidence_and_research(self) -> None:
        context = _make_context(research=ResearchBundle(primary_keywords=[_make_keyword_opportunity()]))
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nBody.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\n"
            "| Claim | Source |\n|---|---|\n"
            "| Homepage | https://example.com/ |\n"
            "| Keyword estimate | https://example.com/source |\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, context)
        assert not any("unverified" in issue.lower() for issue in issues)

    def test_flags_url_not_found_in_evidence_or_research(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nBody.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\n"
            "| Claim | Source |\n|---|---|\n| Made up stat | https://not-a-real-source.com/page |\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("https://not-a-real-source.com/page" in issue for issue in issues)

    def test_allows_url_only_present_in_site_inventory_entries(self) -> None:
        """Regression: a URL known only via SiteInventory.entries (not homepage/sampled_pages) must not be flagged."""
        inventory = SiteInventory(
            base_url="https://example.com",
            entries=[SitemapEntry(url="https://example.com/blog/post-1", source_sitemap="https://example.com/sitemap.xml")],
            total_url_count=1,
            sampled_urls=[],
        )
        context = _make_context(site_evidence=_make_site_evidence(inventory=inventory))
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nBody.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\n"
            "| Claim | Source |\n|---|---|\n| Inventory claim | https://example.com/blog/post-1 |\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, context)
        assert not any("https://example.com/blog/post-1" in issue for issue in issues)

    def test_does_not_require_scores_to_appear_in_report_text(self) -> None:
        """Score-text-presence is not enforced: Sections 6-8 (which held the score summary) were removed."""
        context = _make_context(score_breakdown=ScoreBreakdown(overall_score=82.5, category_scores=[
            CategoryScore(category="Technical SEO", weight_percent=40.0, score=90.0),
        ]))
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nThe site is doing fine.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nNo score figures mentioned here at all.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, context)
        assert not any("score" in issue.lower() for issue in issues)

    def test_flags_invented_core_web_vitals_value(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nYour LCP is 2.4s, which is too slow.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("Core Web Vitals" in issue for issue in issues)

    def test_flags_invented_pagespeed_score(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nYour PageSpeed score is 45.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("PageSpeed/Lighthouse score" in issue for issue in issues)

    def test_allows_core_web_vitals_value_when_performance_evidence_available(self) -> None:
        context = _make_context(site_evidence=_make_site_evidence(
            performance=PerformanceEvidence(is_available=True, data_source="field", largest_contentful_paint_ms=2400.0),
        ))
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nYour LCP is 2.4s, which is too slow.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, context)
        assert not any("Core Web Vitals" in issue for issue in issues)

    def test_still_flags_keyword_ranking_and_backlinks_when_performance_evidence_available(self) -> None:
        context = _make_context(site_evidence=_make_site_evidence(
            performance=PerformanceEvidence(is_available=True, data_source="lab"),
        ))
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nYou rank #3 for 'bakery near me' and have 120 backlinks.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, context)
        assert any("keyword ranking position" in issue for issue in issues)
        assert any("backlink count" in issue for issue in issues)

    def test_flags_invented_keyword_ranking(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nYou rank #3 for 'bakery near me'.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("keyword ranking position" in issue for issue in issues)

    def test_flags_invented_backlink_count(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nThe site has 120 backlinks.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("backlink count" in issue for issue in issues)

    def test_flags_table_row_with_wrong_column_count(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nSummary.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\n"
            "| A | B | C |\n|---|---|---|\n| one | two |\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("has 2 cells, but its header row has 3" in issue for issue in issues)

    def test_allows_table_with_matching_column_counts(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nSummary.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\n"
            "| A | B | C |\n|---|---|---|\n| one | two | three |\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert not any("cells, but its header row has" in issue for issue in issues)


# ---------------------------------------------------------------------------
# assemble_and_validate_report() — Phase 4 assemble+validate checkpoint
# ---------------------------------------------------------------------------

class TestAssembleAndValidateReport:

    _TEMPLATE = "# PART 1: EXECUTIVE SUMMARY\n\n# PART 2: FULL WEBSITE AUDIT\n"

    def test_well_formed_sections_produce_valid_result(self) -> None:
        context = _make_context(score_breakdown=ScoreBreakdown(overall_score=82.5, category_scores=[]))
        sections = {
            "site_inventory": (
                "# PART 1: EXECUTIVE SUMMARY\n\nOverall score: 82.5/100.\n\n"
                "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n\n" + _render_deterministic_blocks_body(context)
            ),
        }
        result = assemble_and_validate_report(sections, self._TEMPLATE, context)

        assert isinstance(result, AssembledReportResult)
        assert result.is_valid is True
        assert result.issues == []
        assert "PART 1" in result.markdown_report
        assert "PART 2" in result.markdown_report

    def test_malformed_sections_produce_invalid_result_with_issues(self) -> None:
        context = _make_context()
        sections = {
            "site_inventory": (
                "# PART 1: EXECUTIVE SUMMARY\n\nSummary.\n\n"
                # PART 2 heading missing entirely — should be flagged.
                + _render_deterministic_blocks_body(context)
            ),
        }
        result = assemble_and_validate_report(sections, self._TEMPLATE, context)

        assert result.is_valid is False
        assert any("Missing required PART headings" in issue for issue in result.issues)
        # The Markdown is still returned even though it failed validation.
        assert "PART 1" in result.markdown_report

    def test_deduplicates_repeated_action_rows_across_the_assembled_report(self) -> None:
        context = _make_context()
        sections = {
            "site_inventory": (
                "# PART 1: FULL WEBSITE AUDIT\n\n"
                "| Timeframe | Action | Owner | Priority |\n|---|---|---|---|\n"
                "| 30 days | Add meta descriptions | SEO Team | High |\n"
                "| 60 days | Add meta descriptions | SEO Team | High |\n\n"
                + _render_deterministic_blocks_body(context)
            ),
        }
        result = assemble_and_validate_report(sections, self._TEMPLATE, context)

        assert result.markdown_report.count("Add meta descriptions") == 1

    def test_raises_report_integrity_error_when_a_deterministic_block_is_missing(self) -> None:
        context = _make_context()
        sections = {
            "site_inventory": (
                "# PART 1: EXECUTIVE SUMMARY\n\nSummary.\n\n# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
                # Every deterministic block (Core Pages Table, etc.) is missing —
                # this can only happen from a pipeline bug, never LLM content.
            ),
        }

        with pytest.raises(ReportIntegrityError):
            assemble_and_validate_report(sections, self._TEMPLATE, context)

    def test_raises_report_integrity_error_when_a_crawled_page_is_missing_from_inventory(self) -> None:
        context = _make_context()
        # Every deterministic block present except the inventory tables are missing a
        # crawled page — this can only happen from a pipeline/injection bug.
        broken_body = _render_deterministic_blocks_body(context).replace(
            "### Core Pages Table\n\n" + render_core_pages_table(build_inventory_section_data(context)),
            "### Core Pages Table\n\n| Page | URL | Title Tag | Meta Description | H1 | Word Count | SEO Notes |\n"
            "|---|---|---|---|---|---|---|\n",
        )
        sections = {
            "site_inventory": (
                "# PART 1: EXECUTIVE SUMMARY\n\nSummary.\n\n# PART 2: FULL WEBSITE AUDIT\n\nBody.\n\n" + broken_body
            ),
        }

        with pytest.raises(ReportIntegrityError):
            assemble_and_validate_report(sections, self._TEMPLATE, context)


# ---------------------------------------------------------------------------
# Deterministic PART 1 inventory table rendering (Step 6)
# ---------------------------------------------------------------------------

def _make_report_row(
    url: str = "https://example.com/services/hair-transplant",
    page_title: str | None = "Hair Transplant Services | Example Clinic",
) -> PageReportRow:
    return PageReportRow(
        url=url,
        page_type=PageType.SERVICE_PRODUCT,
        was_crawled=True,
        http_status=200,
        page_title=page_title,
        meta_description="A meta description of reasonable length for testing purposes here.",
        canonical_url=url,
        h1_tags=["Hair Transplant Services"],
        word_count=400,
        schema_types=["Service"],
        internal_links=["https://example.com/about"],
    )


class TestDerivePageName:

    def test_homepage_path_returns_homepage(self) -> None:
        assert _derive_page_name("https://example.com/") == "Homepage"
        assert _derive_page_name("https://example.com") == "Homepage"

    def test_hyphenated_slug_is_title_cased(self) -> None:
        assert _derive_page_name("https://example.com/services/hair-transplant") == "Hair Transplant"

    def test_underscored_slug_is_title_cased(self) -> None:
        assert _derive_page_name("https://example.com/blog/best_seo_tips") == "Best Seo Tips"


class TestEscapeTableCell:

    def test_pipe_characters_are_escaped(self) -> None:
        assert _escape_table_cell("Title | With Pipe") == "Title \\| With Pipe"

    def test_plain_text_is_unchanged(self) -> None:
        assert _escape_table_cell("Plain Title") == "Plain Title"


class TestRenderPageInventoryTable:

    def test_table_has_required_header_and_separator(self) -> None:
        table = _render_page_inventory_table([_make_report_row()])
        lines = table.splitlines()
        assert lines[0] == "| #Index | Page Name (derived from URL) | URL | Title Tag | SEO Notes |"
        assert lines[1].startswith("|--------|")

    def test_row_uses_verified_title_and_derived_name(self) -> None:
        table = _render_page_inventory_table([_make_report_row()])
        assert "Hair Transplant" in table
        assert "Hair Transplant Services \\| Example Clinic" in table

    def test_missing_title_renders_as_missing_not_invented(self) -> None:
        row = _make_report_row(page_title=None)
        table = _render_page_inventory_table([row])
        assert "| Missing |" in table

    def test_seo_notes_cell_has_exactly_three_bullets(self) -> None:
        table = _render_page_inventory_table([_make_report_row()])
        assert table.count("<li>") == 3
        assert table.count("</li>") == 3
        assert "<ul>" in table and "</ul>" in table

    def test_empty_rows_produce_header_only(self) -> None:
        table = _render_page_inventory_table([])
        assert len(table.splitlines()) == 2


class TestRenderCorePagesAndSubpagesTables:

    def test_render_core_pages_table_uses_only_core_pages(self) -> None:
        inventory = InventorySectionData(
            core_pages=[_make_report_row(url="https://example.com/")],
            subpages=[_make_report_row(url="https://example.com/blog/post-1")],
            sitemap_only_pages=[],
            total_discovered=10,
            total_analyzed=2,
        )
        table = render_core_pages_table(inventory)
        assert "https://example.com/" in table
        assert "post-1" not in table

    def test_render_subpages_table_uses_only_subpages(self) -> None:
        inventory = InventorySectionData(
            core_pages=[_make_report_row(url="https://example.com/")],
            subpages=[_make_report_row(url="https://example.com/blog/post-1")],
            sitemap_only_pages=[],
            total_discovered=10,
            total_analyzed=2,
        )
        table = render_subpages_table(inventory)
        assert "https://example.com/blog/post-1" in table
        assert table.count("| Homepage |") == 0


# ---------------------------------------------------------------------------
# Deterministic PART 2 factual body rendering (Step 7)
# ---------------------------------------------------------------------------

class TestRenderCriticalHighIssuesTable:

    def test_includes_only_critical_and_high_severity_findings(self) -> None:
        findings = [
            _make_finding("Technical SEO", title="Critical issue", severity=Severity.CRITICAL),
            _make_finding("Technical SEO", title="High issue", severity=Severity.HIGH),
            _make_finding("Technical SEO", title="Medium issue", severity=Severity.MEDIUM),
            _make_finding("Technical SEO", title="Low issue", severity=Severity.LOW),
        ]
        table = render_critical_high_issues_table(findings)
        assert "Critical issue" in table
        assert "High issue" in table
        assert "Medium issue" not in table
        assert "Low issue" not in table

    def test_empty_findings_produce_a_clear_no_issues_row(self) -> None:
        table = render_critical_high_issues_table([])
        assert "No Critical or High severity issues were found." in table

    def test_pipe_characters_in_finding_text_are_escaped(self) -> None:
        finding = _make_finding("Technical SEO", severity=Severity.HIGH, title="Title | with pipe")
        table = render_critical_high_issues_table([finding])
        assert "Title \\| with pipe" in table


class TestRenderRobotsTxtSection:

    def test_none_evidence_is_reported_honestly(self) -> None:
        assert "not collected" in render_robots_txt_section(None)

    def test_accessible_robots_with_no_blocking_reports_facts(self) -> None:
        robots = RobotsTxtEvidence(
            is_accessible=True, http_status=200,
            disallow_rules=["/admin"], allow_rules=["/admin/public"],
            sitemap_urls=["https://example.com/sitemap.xml"], blocks_root_path=False,
        )
        section = render_robots_txt_section(robots)
        assert "Accessible: Yes (HTTP 200)" in section
        assert "/admin" in section
        assert "/admin/public" in section
        assert "https://example.com/sitemap.xml" in section
        assert "Blocks the entire site" not in section

    def test_blocking_root_path_is_flagged(self) -> None:
        robots = RobotsTxtEvidence(
            is_accessible=True, http_status=200,
            disallow_rules=["/"], allow_rules=[], sitemap_urls=[], blocks_root_path=True,
        )
        section = render_robots_txt_section(robots)
        assert "Blocks the entire site from crawling" in section
        assert "No Sitemap directive" in section


class TestRenderSitemapSection:

    def test_empty_list_is_reported_honestly(self) -> None:
        assert "No XML sitemap" in render_sitemap_section([])

    def test_accessible_sitemap_reports_url_count(self) -> None:
        sitemaps = [SitemapEvidence(url="https://example.com/sitemap.xml", is_accessible=True, http_status=200, url_count=42)]
        section = render_sitemap_section(sitemaps)
        assert "https://example.com/sitemap.xml" in section
        assert "42 URL(s)" in section
        assert "accessible (HTTP 200)" in section

    def test_inaccessible_sitemap_reports_status_not_count(self) -> None:
        sitemaps = [SitemapEvidence(url="https://example.com/sitemap.xml", is_accessible=False, http_status=404, url_count=0)]
        section = render_sitemap_section(sitemaps)
        assert "not accessible (HTTP 404)" in section


class TestRenderPagespeedSection:

    def test_unavailable_performance_is_reported_honestly(self) -> None:
        assert "not available" in render_pagespeed_section(None)
        assert "not available" in render_pagespeed_section(PerformanceEvidence(is_available=False, data_source=""))

    def test_available_field_data_reports_source_and_metrics(self) -> None:
        performance = PerformanceEvidence(
            is_available=True, data_source="field", source_url="https://example.com/",
            performance_score=88.0, largest_contentful_paint_ms=2500.0,
            cumulative_layout_shift=0.05, interaction_to_next_paint_ms=180.0,
        )
        section = render_pagespeed_section(performance)
        assert "Chrome UX Report" in section
        assert "88/100" in section
        assert "2.5s" in section
        assert "0.05" in section
        assert "180ms" in section

    def test_available_with_no_metrics_states_none_available(self) -> None:
        performance = PerformanceEvidence(is_available=True, data_source="lab", source_url="https://example.com/")
        section = render_pagespeed_section(performance)
        assert "No individual Core Web Vitals metrics were available." in section


class TestRenderIndexabilitySection:

    def test_no_blocking_and_no_noindex_reports_clean_state(self) -> None:
        pages = [_make_report_row(url="https://example.com/")]
        section = render_indexability_section(pages, robots=None)
        assert "Not blocked at the site level" in section
        assert "None of the 1 analyzed page(s)" in section

    def test_blocked_root_path_is_flagged(self) -> None:
        robots = RobotsTxtEvidence(
            is_accessible=True, http_status=200,
            disallow_rules=["/"], allow_rules=[], sitemap_urls=[], blocks_root_path=True,
        )
        section = render_indexability_section([], robots=robots)
        assert "Crawlability: Blocked" in section

    def test_noindex_pages_are_listed(self) -> None:
        pages = [_make_report_row(url="https://example.com/private"), ]
        pages[0].meta_robots = "noindex"
        section = render_indexability_section(pages, robots=None)
        assert "1 of 1 analyzed page(s)" in section
        assert "https://example.com/private" in section


class TestRenderSchemaSection:

    def test_no_schema_is_reported_honestly(self) -> None:
        assert "No structured data" in render_schema_section([])

    def test_detected_schema_types_are_listed(self) -> None:
        section = render_schema_section(["LocalBusiness", "FAQPage"])
        assert "LocalBusiness, FAQPage" in section


# ---------------------------------------------------------------------------
# Deterministic PART 3 on-page table rendering (Step 8)
# ---------------------------------------------------------------------------

class TestRenderHomepageElementsTable:

    def test_table_has_required_header_and_four_element_rows(self) -> None:
        table = render_homepage_elements_table(_make_report_row())
        lines = table.splitlines()
        assert lines[0] == "| Element | Current | Issue | Recommended |"
        assert lines[1].startswith("|---------|")
        assert len(lines) == 6  # header + separator + 4 elements

    def test_missing_title_row_reports_missing_and_a_recommendation(self) -> None:
        row = _make_report_row(page_title=None)
        table = render_homepage_elements_table(row)
        assert "| Title Tag | Missing | Missing | Add a unique, descriptive title tag" in table

    def test_pipe_characters_in_values_are_escaped(self) -> None:
        row = _make_report_row(page_title="Title | With Pipe")
        table = render_homepage_elements_table(row)
        assert "Title \\| With Pipe" in table


class TestRenderPriorityPagesTable:

    def test_table_has_required_header(self) -> None:
        table = render_priority_pages_table([_make_report_row()])
        lines = table.splitlines()
        assert lines[0] == "| Page | Title Tag Issue | Meta Description Issue | Heading Issue | Recommendation |"

    def test_empty_list_produces_an_honest_no_pages_row(self) -> None:
        table = render_priority_pages_table([])
        assert "No priority pages were analyzed." in table

    def test_healthy_page_needs_no_action(self) -> None:
        table = render_priority_pages_table([_make_report_row()])
        assert "No immediate action needed." in table
        assert "| None | None | None |" in table

    def test_page_with_issues_reports_them(self) -> None:
        row = _make_report_row(page_title=None)
        table = render_priority_pages_table([row])
        assert row.url in table
        assert "Missing" in table


class TestRenderContentQualitySection:

    def test_no_findings_reports_an_honest_clean_state(self) -> None:
        section = render_content_quality_section([])
        assert "No content quality issues were found" in section

    def test_findings_report_counts_and_affected_urls(self) -> None:
        finding = _make_finding(
            "Content Quality",
            title="Pages have thin content",
            description="2 of 5 page(s) have fewer than 300 visible words.",
            evidence_urls=["https://example.com/thin-1", "https://example.com/thin-2"],
        )
        section = render_content_quality_section([finding])
        assert "Pages have thin content" in section
        assert "2 of 5 page(s)" in section
        assert "https://example.com/thin-1" in section
        assert "https://example.com/thin-2" in section


# ---------------------------------------------------------------------------
# Deterministic Section 1-3 opportunity table rendering (Step 14 — Phase 4)
# ---------------------------------------------------------------------------

class TestRenderResearchStatusNote:

    def test_no_results_is_a_genuine_empty_result_not_a_failure(self) -> None:
        note = _render_research_status_note(ResearchStatus.NO_RESULTS)
        assert "genuine zero-result search" in note
        assert "not a research failure" in note

    def test_parse_failed_is_an_availability_gap_not_no_opportunity(self) -> None:
        note = _render_research_status_note(ResearchStatus.PARSE_FAILED)
        assert "research-availability gap" in note
        assert "not an absence of market opportunity" in note

    def test_citation_failed_is_an_availability_gap(self) -> None:
        assert "research-availability gap" in _render_research_status_note(ResearchStatus.CITATION_FAILED)

    def test_provider_failed_is_an_availability_gap(self) -> None:
        assert "research-availability gap" in _render_research_status_note(ResearchStatus.PROVIDER_FAILED)

    def test_success_and_none_produce_no_note(self) -> None:
        assert _render_research_status_note(ResearchStatus.SUCCESS) == ""
        assert _render_research_status_note(None) == ""


class TestRenderPrimaryAndLongTailKeywordsTable:

    def test_renders_accepted_rows_with_citation(self) -> None:
        opportunity = _make_keyword_opportunity(keyword="artisan bread austin")
        table = render_primary_keywords_table([opportunity], ResearchStatus.SUCCESS)
        assert "artisan bread austin" in table
        assert "[Source](https://example.com/source)" in table
        assert "2026-08-04" in table

    def test_missing_estimates_are_honest_not_fabricated(self) -> None:
        opportunity = KeywordOpportunity(
            keyword="bread", search_intent="commercial", source_url="https://example.com/source",
            source_title="Source", retrieved_date="2026-08-04",
        )
        table = render_long_tail_keywords_table([opportunity], ResearchStatus.SUCCESS)
        assert "No sourced estimate" in table
        assert "No clear existing page match" in table

    def test_empty_no_results_shows_a_genuine_empty_result_note(self) -> None:
        table = render_primary_keywords_table([], ResearchStatus.NO_RESULTS)
        assert "genuine zero-result search" in table

    def test_empty_failure_status_shows_an_availability_gap_note(self) -> None:
        table = render_long_tail_keywords_table([], ResearchStatus.PROVIDER_FAILED)
        assert "research-availability gap" in table


class TestRenderCompetitorOverviewAndGapTable:

    def test_renders_accepted_competitor_with_citation(self) -> None:
        competitor = _make_competitor(competitor_name="Joe's Bakery", website="https://joesbakery.com")
        table = render_competitor_overview_table([competitor], ResearchStatus.SUCCESS)
        assert "Joe's Bakery" in table
        assert "https://joesbakery.com" in table

    def test_missing_authority_is_honest_not_fabricated(self) -> None:
        competitor = CompetitorOverview(
            competitor_name="Joe's Bakery", website="https://joesbakery.com", focus="Wholesale bread",
            source_url="https://example.com/source", source_title="Source", retrieved_date="2026-08-04",
        )
        table = render_competitor_overview_table([competitor], ResearchStatus.SUCCESS)
        assert "No sourced estimate" in table

    def test_gap_table_renders_accepted_rows(self) -> None:
        gap = _make_gap(keyword="artisan bread austin", your_gap="No dedicated landing page")
        table = render_competitor_gap_table([gap], ResearchStatus.SUCCESS)
        assert "artisan bread austin" in table
        assert "No dedicated landing page" in table

    def test_empty_no_results_shows_a_genuine_empty_result_note(self) -> None:
        assert "genuine zero-result search" in render_competitor_overview_table([], ResearchStatus.NO_RESULTS)

    def test_empty_failure_status_shows_an_availability_gap_note(self) -> None:
        assert "research-availability gap" in render_competitor_gap_table([], ResearchStatus.CITATION_FAILED)


class TestRenderLocationOpportunityTable:

    def test_renders_accepted_rows_with_citation(self) -> None:
        opportunity = _make_location_opportunity(city_or_region="Austin, TX")
        table = render_location_opportunity_table([opportunity], ResearchStatus.SUCCESS)
        assert "Austin, TX" in table
        assert "bakery near me" in table

    def test_empty_no_results_shows_a_genuine_empty_result_note(self) -> None:
        assert "genuine zero-result search" in render_location_opportunity_table([], ResearchStatus.NO_RESULTS)

    def test_empty_failure_status_shows_an_availability_gap_note(self) -> None:
        assert "research-availability gap" in render_location_opportunity_table([], ResearchStatus.PARSE_FAILED)


class TestInjectKeywordAndCompetitorAndLocationTables:

    def test_inject_keyword_tables_forces_both_regardless_of_model_output(self) -> None:
        markdown = (
            "# SECTION 1: KEYWORD OPPORTUNITY STRATEGY\n\n"
            "### Primary Keywords Table\n\nInvented primary row.\n\n"
            "### Long-Tail Keywords Table\n\nInvented long-tail row.\n"
        )
        result = _inject_keyword_tables(markdown, "| real primary |", "| real long-tail |")
        assert "Invented primary row." not in result
        assert "Invented long-tail row." not in result
        assert "| real primary |" in result
        assert "| real long-tail |" in result

    def test_inject_competitor_tables_forces_both_regardless_of_model_output(self) -> None:
        markdown = (
            "# SECTION 2: COMPETITOR ANALYSIS\n\n"
            "### Competitor Overview Table\n\nInvented competitor row.\n\n"
            "### Keyword Gap Table\n\nInvented gap row.\n"
        )
        result = _inject_competitor_tables(markdown, "| real overview |", "| real gap |")
        assert "Invented competitor row." not in result
        assert "Invented gap row." not in result
        assert "| real overview |" in result
        assert "| real gap |" in result

    def test_inject_location_table_forces_content_regardless_of_model_output(self) -> None:
        markdown = "# SECTION 3: LOCATION\n\n### Location Opportunity Table\n\nInvented location row.\n"
        result = _inject_location_table(markdown, "| real location |")
        assert "Invented location row." not in result
        assert "| real location |" in result


# ---------------------------------------------------------------------------
# Section-aware evidence validators (Step 15)
# ---------------------------------------------------------------------------

class TestFindTableAfterHeading:

    def test_returns_empty_list_when_heading_absent(self) -> None:
        assert _find_table_after_heading("# PART 1\n\nNo tables here.\n", "Core Pages Table") == []

    def test_returns_table_rows_found_under_heading(self) -> None:
        markdown = (
            "### Core Pages Table\n\n"
            "| URL | Title Tag |\n|---|---|\n| https://example.com/ | Example |\n"
        )
        tables = _find_table_after_heading(markdown, "Core Pages Table")
        assert len(tables) == 1
        assert tables[0][0] == "| URL | Title Tag |"


class TestValidateInventoryTableCoverage:

    @staticmethod
    def _report(core_table_body: str) -> str:
        return (
            "### Core Pages Table\n\n" + core_table_body +
            "\n\n### Subpages Table\n\n| URL | Title Tag |\n|---|---|\n"
        )

    def test_flags_page_missing_from_both_tables(self) -> None:
        context = _make_context()  # homepage https://example.com/, page_title="Example Bakery"
        report = self._report("| URL | Title Tag |\n|---|---|\n")  # homepage row omitted
        issues = _validate_inventory_table_coverage(report, context)
        assert any("is missing from both the Core Pages Table and Subpages Table" in issue for issue in issues)

    def test_flags_page_appearing_more_than_once(self) -> None:
        context = _make_context()
        row = "| https://example.com/ | Example Bakery |\n"
        report = self._report("| URL | Title Tag |\n|---|---|\n" + row + row)
        issues = _validate_inventory_table_coverage(report, context)
        assert any("appears 2 times" in issue for issue in issues)

    def test_flags_title_tag_mismatch(self) -> None:
        context = _make_context()
        report = self._report("| URL | Title Tag |\n|---|---|\n| https://example.com/ | Wrong Title |\n")
        issues = _validate_inventory_table_coverage(report, context)
        assert any("Title Tag cell for https://example.com/" in issue for issue in issues)

    def test_well_formed_single_occurrence_with_matching_title_has_no_issues(self) -> None:
        context = _make_context()
        report = self._report("| URL | Title Tag |\n|---|---|\n| https://example.com/ | Example Bakery |\n")
        assert _validate_inventory_table_coverage(report, context) == []


class TestValidateSeoNotesCellCounts:

    def test_flags_wrong_li_count(self) -> None:
        report = (
            "### Core Pages Table\n\n"
            "| URL | SEO Notes |\n|---|---|\n"
            "| https://example.com/ | <ul><li>One</li><li>Two</li></ul> |\n"
        )
        issues = _validate_seo_notes_cell_counts(report)
        assert any("has 2 SEO Notes <li> item(s), expected exactly 3" in issue for issue in issues)

    def test_accepts_exactly_three_li_items(self) -> None:
        report = (
            "### Core Pages Table\n\n"
            "| URL | SEO Notes |\n|---|---|\n"
            "| https://example.com/ | <ul><li>One</li><li>Two</li><li>Three</li></ul> |\n"
        )
        assert _validate_seo_notes_cell_counts(report) == []


class TestValidateNoUnconfirmedHttpClaims:

    @staticmethod
    def _context_with_status(http_status: int, attempt_count: int = 1) -> AuditContext:
        page = _make_page_evidence(http_status=http_status, attempt_count=attempt_count)
        return _make_context(site_evidence=_make_site_evidence(homepage=page))

    def test_flags_unhedged_definitive_claim(self) -> None:
        context = self._context_with_status(503)
        report = "The homepage returned HTTP 503, confirming the server is down.\n"
        issues = _validate_no_unconfirmed_http_claims(report, context)
        assert any("HTTP 503" in issue for issue in issues)

    def test_allows_hedged_claim(self) -> None:
        context = self._context_with_status(503)
        report = "The homepage returned an unconfirmed HTTP 503 status, observed once.\n"
        assert _validate_no_unconfirmed_http_claims(report, context) == []

    def test_confirmed_status_is_never_flagged(self) -> None:
        context = self._context_with_status(503, attempt_count=2)
        report = "The homepage returned HTTP 503.\n"
        assert _validate_no_unconfirmed_http_claims(report, context) == []

    def test_non_transient_status_is_never_flagged(self) -> None:
        context = self._context_with_status(200)
        report = "The homepage returned HTTP 200.\n"
        assert _validate_no_unconfirmed_http_claims(report, context) == []


class TestValidateDeterministicBlocksPresent:

    def test_flags_missing_block(self) -> None:
        context = _make_context()
        issues = _validate_deterministic_blocks_present("# Report with nothing in it.\n", context)
        assert any("missing the expected deterministic Core Pages Table content" in issue for issue in issues)

    def test_all_blocks_present_has_no_issues(self) -> None:
        context = _make_context()
        report = _render_deterministic_blocks_body(context)
        assert _validate_deterministic_blocks_present(report, context) == []

    def test_location_table_not_required_for_non_local_business(self) -> None:
        context = _make_context()  # is_local_business=False by default
        report = _render_deterministic_blocks_body(context)
        assert _validate_deterministic_blocks_present(report, context) == []

    def test_location_table_required_for_local_business_with_region(self) -> None:
        context = _make_context()
        report = _render_deterministic_blocks_body(context)  # built without a Location Opportunity Table
        local_context = _make_context(is_local_business=True, city_or_region="Austin, TX")

        issues = _validate_deterministic_blocks_present(report, local_context)
        assert any("missing the expected deterministic Location Opportunity Table content" in issue for issue in issues)


class TestValidateRemovedSectionsAbsent:

    def test_flags_removed_section_heading(self) -> None:
        issues = _validate_removed_sections_absent("# SECTION 6: OLD SECTION\n\nContent.\n")
        assert any("SECTION 6" in issue for issue in issues)

    def test_no_issue_when_all_removed_sections_absent(self) -> None:
        assert _validate_removed_sections_absent("# PART 1: EXECUTIVE SUMMARY\n") == []


# ---------------------------------------------------------------------------
# Provider parity (Step 20) — the same frozen AuditContext, run through
# generate_report_sections()/assemble_and_validate_report() once per
# provider with only the underlying narrative wording varied, must produce
# structurally identical reports. Only narrative sentences may differ.
# ---------------------------------------------------------------------------

_PROVIDER_PARITY_TEMPLATE = PromptContext(
    audit_prompt="Audit {{website_url}}.",
    seo_skill="Priority: Crawlability, Technical, On-Page, Content.",
    master_report_structure=(
        "# PART 1: FULL WEBSITE AUDIT\n\nBody.\n\n"
        "# PART 2: TECHNICAL SEO AUDIT\n\nBody.\n\n"
        "# PART 3: ON-PAGE & CONTENT AUDIT\n\nBody.\n\n"
        "# SECTION 1: KEYWORD OPPORTUNITY STRATEGY\n\nBody.\n\n"
        "# SECTION 2: COMPETITOR ANALYSIS\n\nBody.\n\n"
        "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n"
        "## 3.1 Applicability Assessment\nBody.\n"
        "## 3.2 Local Location Opportunities\nBody.\n"
        "## 3.3 Audience & Market Expansion Opportunities\nNot applicable.\n\n"
        "# SECTION 4: STRUCTURED DATA RECOMMENDATIONS\n\nBody.\n\n"
        "# SECTION 5: OFF-PAGE SEO & GEO STRATEGY\n\nBody.\n"
    ),
    ai_guidelines="Never invent findings. Use verified evidence only.",
)


def _make_provider_narrative_fake(style: str):
    """
    Build a fake generate_text() standing in for one provider's real output: same
    required headings/structure as any other provider (so the pipeline's
    validation/injection behaves identically), but with wording tagged by
    `style` so the test can prove narrative text is genuinely allowed to differ.
    """

    async def fake_generate_text(system_prompt: str, user_message: str, settings: Settings) -> str:
        headings = re.findall(r"# (?:PART|SECTION) \d+:[^\n]*", user_message)
        if any(h.startswith("# SECTION 3:") for h in headings):
            return (
                "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n"
                f"## 3.1 Applicability Assessment\n\n[{style}] Local business serving Austin, TX.\n\n"
                f"## 3.2 Local Location Opportunities\n\n[{style}] Narrative on local expansion.\n\n"
                "## 3.3 Audience & Market Expansion Opportunities\n\nNot applicable.\n"
            )
        return "\n\n".join(f"{h}\n\n[{style}] Generated narrative for this section." for h in headings)

    return fake_generate_text


class TestProviderParity:

    async def test_gemini_openai_perplexity_produce_structurally_identical_reports(
        self, settings: Settings,
    ) -> None:
        context = build_frozen_audit_context()
        assembled: dict[str, AssembledReportResult] = {}

        for provider, style in (("gemini", "Gemini"), ("openai", "OpenAI"), ("perplexity", "Perplexity")):
            settings.llm_provider = provider  # Documents which provider this run represents
            with patch(
                "src.services.report_service.generate_text", side_effect=_make_provider_narrative_fake(style)
            ):
                sections = await generate_report_sections(context, _PROVIDER_PARITY_TEMPLATE, settings)
            assembled[provider] = assemble_and_validate_report(
                sections, _PROVIDER_PARITY_TEMPLATE.master_report_structure, context
            )

        for provider, result in assembled.items():
            assert result.markdown_report, f"{provider} produced an empty report"

        # Every provider must produce exactly the same validation verdict — a genuine
        # per-provider discrepancy would show up as different issues here. (This
        # simplified template's PART 3 "## 3.2"/"## 3.3" On-Page headings share a
        # prefix with SECTION 3's own "## 3.2"/"## 3.3" — a pre-existing,
        # already-documented _validate_location_section() limitation, out of scope
        # for this plan — so the same template-level issue is expected identically
        # across all three providers, not a parity failure.)
        issues = {provider: result.issues for provider, result in assembled.items()}
        assert issues["openai"] == issues["gemini"]
        assert issues["perplexity"] == issues["gemini"]

        reports = {provider: result.markdown_report for provider, result in assembled.items()}

        # Headings (PART/SECTION/subsection/table) and their order must be byte-identical —
        # they are deterministically templated/injected, never provider-authored.
        headings = {
            provider: re.findall(r"^#{1,3} .+$", markdown, re.MULTILINE) for provider, markdown in reports.items()
        }
        assert headings["openai"] == headings["gemini"]
        assert headings["perplexity"] == headings["gemini"]

        # Table blocks — columns, row counts, factual values, and citations — are all
        # force-injected from the same AuditContext, so they must match exactly too.
        tables = {provider: _find_table_blocks(markdown) for provider, markdown in reports.items()}
        assert tables["openai"] == tables["gemini"]
        assert tables["perplexity"] == tables["gemini"]

        # Narrative wording is the one thing allowed to differ between providers —
        # confirms the parity above isn't trivially true from byte-identical mock output.
        assert reports["gemini"] != reports["openai"]
        assert reports["gemini"] != reports["perplexity"]
        assert "[Gemini]" in reports["gemini"]
        assert "[OpenAI]" in reports["openai"]
        assert "[Perplexity]" in reports["perplexity"]







