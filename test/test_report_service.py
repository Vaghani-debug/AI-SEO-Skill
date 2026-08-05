"""
test/test_report_service.py

Unit tests for src/services/report_service.py.

All Gemini API calls are mocked so these tests run offline without tokens.
Each test exercises one specific behaviour, error path, or formatting rule.

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
    EffortLevel,
    Finding,
    PageEvidence,
    PageType,
    PerformanceEvidence,
    ResearchBundle,
    ResearchClaim,
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
from src.services.prompt_loader import PromptContext  # Guidance context
from src.services.report_service import (
    ReportResult,               # Return type
    AssembledReportResult,      # Phase 4 — assemble+validate checkpoint result
    assemble_and_validate_report,  # Phase 4 — assemble+validate checkpoint entry point
    assemble_report_markdown,   # Phase 4 — assembles section dict into one final report
    validate_assembled_report,  # Phase 4 — report-level validation entry point
    _build_retry_user_message,  # Internal helper — retry instruction builder
    _build_section_user_message,  # Internal helper — Phase 4 per-section user message builder
    _build_user_message,       # Internal helper — evidence formatting
    _deduplicate_table_rows,   # Internal helper — Phase 4/16 duplicate table row removal
    _extract_part_templates,   # Internal helper — Phase 4 per-PART template slicing
    _extract_section_body,     # Internal helper — subsection text extraction
    _find_banned_phrases,      # Internal helper — contamination/branding detection
    _find_table_blocks,        # Internal helper — Markdown table detection
    _format_evidence,          # Internal helper — evidence formatting
    _format_section_evidence,  # Internal helper — Phase 4 section-scoped evidence slicing
    _split_table_row,          # Internal helper — Markdown table row parsing
    _SECTION_GROUPS,           # Internal constant — Phase 4 section group definitions
    _validate_citation_columns,  # Internal helper — Source/Retrieved validation
    build_source_register,     # Phase 4 — deterministic PART 11.3 Source Register Table builder
    generate_report_sections,  # Phase 4 sequential section-generation pipeline
    _validate_location_section,  # Internal helper — PART 7 conditional validation
    build_audit_context,       # Public function under test — Phase 4 context builder
    generate_report,           # Public function under test
)


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


def _make_gemini_mock(response_text: str = "# SEO Report\n\nTest report.") -> MagicMock:
    """Create a mock Gemini model that returns the given text."""
    mock_response = MagicMock()  # Mock response object
    mock_response.text = response_text  # The LLM output text

    mock_model = MagicMock()  # Mock GenerativeModel instance
    mock_model.generate_content.return_value = mock_response  # Sync call returns response

    return mock_model


# ---------------------------------------------------------------------------
# Tests for generate_report() — success paths
# ---------------------------------------------------------------------------

class TestGenerateReportSuccess:
    """Tests for successful report generation."""

    async def test_returns_report_result(self, settings: Settings, prompt_context: PromptContext) -> None:
        """generate_report() returns a ReportResult on success."""
        evidence = _make_evidence()
        mock_model = _make_gemini_mock("# SEO Report\n\n## Executive Summary\n\nGood site.")

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model  # Inject mock model

            result = await generate_report("https://example.com", evidence, prompt_context, settings)

        assert isinstance(result, ReportResult)  # Correct return type

    async def test_markdown_report_populated(self, settings: Settings, prompt_context: PromptContext) -> None:
        """markdown_report field contains the LLM response text."""
        expected_markdown = "# SEO Report\n\nThis is the report."
        evidence = _make_evidence()
        mock_model = _make_gemini_mock(expected_markdown)

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            result = await generate_report("https://example.com", evidence, prompt_context, settings)

        assert result.markdown_report == expected_markdown  # LLM text stored exactly

    async def test_audit_id_is_unique_uuid(self, settings: Settings, prompt_context: PromptContext) -> None:
        """Each call produces a different, valid UUID4 audit_id."""
        evidence = _make_evidence()
        mock_model = _make_gemini_mock("# Report")

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

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
        mock_model = _make_gemini_mock("# Report")

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            result = await generate_report(
                "https://example.com", evidence, prompt_context, settings,
                audit_id="fixed-job-id-123",
            )

        assert result.audit_id == "fixed-job-id-123"  # Pre-generated ID reused, not replaced

    async def test_normalized_url_stored(self, settings: Settings, prompt_context: PromptContext) -> None:
        """normalized_url in the result matches the input URL."""
        url = "https://www.truelinesolution.com"
        evidence = _make_evidence(url=url)
        mock_model = _make_gemini_mock("# Report")

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            result = await generate_report(url, evidence, prompt_context, settings)

        assert result.normalized_url == url  # URL preserved exactly

    async def test_created_at_is_datetime(self, settings: Settings, prompt_context: PromptContext) -> None:
        """created_at is a datetime object representing the audit completion time."""
        evidence = _make_evidence()
        mock_model = _make_gemini_mock("# Report")

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            result = await generate_report("https://example.com", evidence, prompt_context, settings)

        assert isinstance(result.created_at, datetime)  # Correct type

    async def test_url_substituted_in_prompt(self, settings: Settings, prompt_context: PromptContext) -> None:
        """{{website_url}} placeholder in the audit prompt is replaced with the real URL."""
        target_url = "https://www.specific-website.com"
        evidence = _make_evidence(url=target_url)
        mock_model = _make_gemini_mock("# Report")

        captured_calls: list[str] = []  # Record what is passed to generate_content

        def capture_call(user_message: str) -> MagicMock:
            captured_calls.append(user_message)  # Store the user message
            r = MagicMock()
            r.text = "# Report"
            return r

        mock_model.generate_content.side_effect = capture_call

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            await generate_report(target_url, evidence, prompt_context, settings)

        # The URL should appear in the user message passed to the LLM
        assert target_url in captured_calls[0]

    async def test_gemini_configured_with_api_key(self, settings: Settings, prompt_context: PromptContext) -> None:
        """genai.configure() is called with the API key from settings."""
        evidence = _make_evidence()
        mock_model = _make_gemini_mock("# Report")

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            await generate_report("https://example.com", evidence, prompt_context, settings)

        mock_genai.configure.assert_called_once_with(api_key=settings.gemini_api_key)
        # Verify the API key is always passed before making a call

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

        def generate_side_effect(_: str) -> MagicMock:
            nonlocal call_index
            call_index += 1
            response = MagicMock()
            response.text = first_response if call_index == 1 else second_response
            return response

        mock_model = MagicMock()
        mock_model.generate_content.side_effect = generate_side_effect

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            result = await generate_report("https://example.com", evidence, ctx, settings)

        assert mock_model.generate_content.call_count == 2
        assert result.markdown_report == second_response

    async def test_retries_once_when_report_contains_banned_phrases(
        self, settings: Settings, prompt_context: PromptContext
    ) -> None:
        """A contaminated first response triggers exactly one retry with clean output."""
        evidence = _make_evidence()

        first_response = "# Report\n\nGenerated using Perplexity. Convert to Google Docs to share."
        second_response = "# Report\n\nClean report with no contamination."

        call_index = 0

        def generate_side_effect(_: str) -> MagicMock:
            nonlocal call_index
            call_index += 1
            response = MagicMock()
            response.text = first_response if call_index == 1 else second_response
            return response

        mock_model = MagicMock()
        mock_model.generate_content.side_effect = generate_side_effect

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            result = await generate_report(
                "https://example.com", evidence, prompt_context, settings
            )

        assert mock_model.generate_content.call_count == 2
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

    async def test_llm_network_error_raises_runtime_error(
        self, settings: Settings, prompt_context: PromptContext
    ) -> None:
        """RuntimeError is raised and wrapped when the Gemini SDK raises an exception."""
        evidence = _make_evidence()

        mock_model = MagicMock()
        mock_model.generate_content.side_effect = Exception("Network error: connection refused")

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            with pytest.raises(RuntimeError, match="LLM report generation failed"):
                await generate_report("https://example.com", evidence, prompt_context, settings)

    async def test_empty_llm_response_raises_runtime_error(
        self, settings: Settings, prompt_context: PromptContext
    ) -> None:
        """RuntimeError is raised when the LLM returns an empty text response."""
        evidence = _make_evidence()
        mock_model = _make_gemini_mock("")  # Empty text — blocked or no content

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            with pytest.raises(RuntimeError, match="empty response"):
                await generate_report("https://example.com", evidence, prompt_context, settings)

    async def test_none_llm_response_raises_runtime_error(
        self, settings: Settings, prompt_context: PromptContext
    ) -> None:
        """RuntimeError is raised when the LLM returns None."""
        evidence = _make_evidence()

        mock_model = MagicMock()
        mock_model.generate_content.return_value = None  # SDK returned None

        with patch("src.services.report_service.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            with pytest.raises(RuntimeError):
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


class TestSectionGroups:

    def test_covers_every_part_and_section_heading_exactly_once(self) -> None:
        all_headings = [heading for _, headings in _SECTION_GROUPS for heading in headings]
        assert sorted(all_headings) == sorted(
            [f"PART {n}" for n in range(1, 4)] + [f"SECTION {n}" for n in range(1, 9)]
        )
        assert len(all_headings) == len(set(all_headings))

    def test_has_five_to_seven_groups(self) -> None:
        assert 5 <= len(_SECTION_GROUPS) <= 7

    def test_executive_summary_is_last(self) -> None:
        assert _SECTION_GROUPS[-1][0] == "executive_summary"


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

    def test_structured_data_and_execution_no_longer_includes_performance_findings(self) -> None:
        findings = [_make_finding("Performance", title="LCP needs improvement")]
        context = _make_context(score_breakdown=ScoreBreakdown(overall_score=70.0, findings=findings))
        text = _format_section_evidence("structured_data_and_execution", context)
        assert "LCP needs improvement" not in text

    def test_keyword_strategy_formats_claims_with_citation(self) -> None:
        research = ResearchBundle(keyword_opportunities=[_make_claim("Keyword", "sourdough bread austin")])
        text = _format_section_evidence("keyword_strategy", _make_context(research=research))
        assert "sourdough bread austin" in text
        assert "Source" in text and "retrieved 2026-08-04" in text

    def test_keyword_strategy_empty_message(self) -> None:
        text = _format_section_evidence("keyword_strategy", _make_context())
        assert "No keyword opportunities were found" in text

    def test_competitor_analysis_includes_both_lists(self) -> None:
        research = ResearchBundle(
            competitors=[_make_claim("Competitor", "Joe's Bakery")],
            competitor_analysis=[_make_claim("Gap", "No online ordering")],
        )
        text = _format_section_evidence("competitor_analysis", _make_context(research=research))
        assert "Joe's Bakery" in text
        assert "No online ordering" in text

    def test_location_section_uses_local_demand_when_local_with_region(self) -> None:
        research = ResearchBundle(local_demand=[_make_claim("Demand", "High")])
        context = _make_context(research=research, is_local_business=True, city_or_region="Austin, TX")
        text = _format_section_evidence("location_or_market_expansion", context)
        assert "Local/service-area business" in text
        assert "Austin, TX" in text
        assert "High" in text

    def test_location_section_uses_audience_expansion_when_not_local(self) -> None:
        research = ResearchBundle(audience_expansion=[_make_claim("Segment", "Wholesale bakeries")])
        context = _make_context(research=research, is_local_business=False, city_or_region=None)
        text = _format_section_evidence("location_or_market_expansion", context)
        assert "Not local/service-area" in text
        assert "Wholesale bakeries" in text

    def test_structured_data_and_execution_includes_authority_claims(self) -> None:
        research = ResearchBundle(authority_opportunities=[_make_claim("Directory", "Local business directory")])
        text = _format_section_evidence("structured_data_and_execution", _make_context(research=research))
        assert "Local business directory" in text

    def test_structured_data_and_execution_includes_brand_presence_claims(self) -> None:
        research = ResearchBundle(brand_presence=[_make_claim("Brand Presence", "Listed on Yelp")])
        text = _format_section_evidence("structured_data_and_execution", _make_context(research=research))
        assert "Listed on Yelp" in text

    def test_structured_data_and_execution_reports_no_brand_presence_message(self) -> None:
        text = _format_section_evidence("structured_data_and_execution", _make_context())
        assert "No existing brand presence signals were found" in text

    def test_executive_summary_includes_score_and_top_priority_findings(self) -> None:
        findings = [
            _make_finding("Technical SEO", severity=Severity.CRITICAL, title="Robots.txt blocks site"),
            _make_finding("On-Page SEO", severity=Severity.LOW, title="Minor issue"),
        ]
        score_breakdown = ScoreBreakdown(
            overall_score=55.0,
            category_scores=[CategoryScore(category="Technical SEO", weight_percent=40.0, score=10.0)],
            findings=findings,
        )
        text = _format_section_evidence("executive_summary", _make_context(score_breakdown=score_breakdown))
        assert "Overall score: 55.0/100" in text
        assert "Robots.txt blocks site" in text
        assert "Minor issue" not in text  # Low severity — excluded from the top-priority slice

    def test_executive_summary_no_priority_findings_message(self) -> None:
        text = _format_section_evidence("executive_summary", _make_context())
        assert "No Critical or High severity findings" in text

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
                "# SECTION 5: OFF-PAGE SEO & GEO STRATEGY\n\nBody.\n\n"
                "# SECTION 6: PRIORITIZED EXECUTION PLAN & KPIS\n\nBody.\n\n"
                "# SECTION 7: EXECUTIVE SUMMARY\n\nBody.\n\n"
                "# SECTION 8: METHODOLOGY, LIMITATIONS & SOURCES\n\nBody.\n"
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

        with patch("src.services.report_service._call_llm", side_effect=fake_call_llm):
            sections = await generate_report_sections(context, full_prompt_context, settings)

        assert list(sections.keys()) == [name for name, _ in _SECTION_GROUPS]
        assert "Generated content." in sections["site_inventory"]
        assert list(sections.keys())[-1] == "executive_summary"

    async def test_retries_once_when_required_heading_missing(
        self, settings: Settings, full_prompt_context: PromptContext,
    ) -> None:
        context = _make_context()
        technical_call_count = {"n": 0}

        async def fake_call_llm(system_prompt: str, user_message: str, settings: Settings) -> str:
            headings = re.findall(r"# (?:PART|SECTION) \d+:[^\n]*", user_message)
            if any(h.startswith("# PART 3:") for h in headings):
                technical_call_count["n"] += 1
                if technical_call_count["n"] == 1:
                    return "# PART 2: TECHNICAL SEO AUDIT\n\nMissing the PART 3 heading on purpose."
                return "# PART 2: TECHNICAL SEO AUDIT\n\nFixed on retry.\n\n# PART 3: ON-PAGE & CONTENT AUDIT\n\nFixed too."
            if any(h.startswith("# SECTION 3:") for h in headings):
                return "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n## 3.2\nBody.\n## 3.3\nNot applicable.\n"
            return "\n\n".join(f"{h}\n\nOK." for h in headings)

        with patch("src.services.report_service._call_llm", side_effect=fake_call_llm) as mock_call:
            sections = await generate_report_sections(context, full_prompt_context, settings)

        assert technical_call_count["n"] == 2  # First call missed a heading, second call (retry) fixed it
        assert "Fixed on retry" in sections["technical_and_onpage"]
        assert mock_call.await_count == len(_SECTION_GROUPS) + 1  # One extra call for the single retry

    async def test_keeps_best_effort_output_when_retry_still_fails(
        self, settings: Settings, full_prompt_context: PromptContext,
    ) -> None:
        context = _make_context()

        async def always_broken(system_prompt: str, user_message: str, settings: Settings) -> str:
            return "No headings here at all."

        with patch("src.services.report_service._call_llm", side_effect=always_broken):
            sections = await generate_report_sections(context, full_prompt_context, settings)

        # Every group is still present in the result, even though validation never passed.
        assert set(sections.keys()) == {name for name, _ in _SECTION_GROUPS}
        assert all(text == "No headings here at all." for text in sections.values())


# ---------------------------------------------------------------------------
# assemble_report_markdown() — Phase 4 final assembly
# ---------------------------------------------------------------------------

class TestAssembleReportMarkdown:

    def test_orders_groups_by_declaration_order_and_splices_executive_summary_before_methodology(self) -> None:
        # Insertion order mirrors _SECTION_GROUPS' generation order (executive_summary last), but
        # the assembled output must read in declaration order with SECTION 7 spliced in immediately
        # before SECTION 8 (Methodology), which is generated earlier as part of the same batch.
        sections = {
            "site_inventory": "# PART 1: FULL WEBSITE AUDIT\n\nInventory body.",
            "technical_and_onpage": "# PART 2: TECHNICAL SEO AUDIT\n\nTechnical body.",
            "keyword_strategy": "# SECTION 1: KEYWORD OPPORTUNITY STRATEGY\n\nKeyword body.",
            "competitor_analysis": "# SECTION 2: COMPETITOR ANALYSIS\n\nCompetitor body.",
            "location_or_market_expansion": "# SECTION 3: LOCATION STRATEGY\n\nLocation body.",
            "structured_data_and_execution": (
                "# SECTION 4: STRUCTURED DATA\n\nStructured data body.\n\n"
                "# SECTION 8: METHODOLOGY\n\nMethodology body."
            ),
            "executive_summary": "# SECTION 7: EXECUTIVE SUMMARY\n\nSummary body.",
        }
        markdown = assemble_report_markdown(sections)

        assert markdown.index("PART 1") < markdown.index("PART 2")
        assert markdown.index("PART 2") < markdown.index("SECTION 1")
        assert markdown.index("SECTION 1") < markdown.index("SECTION 2")
        assert markdown.index("SECTION 2") < markdown.index("SECTION 3")
        assert markdown.index("SECTION 3") < markdown.index("SECTION 4")
        assert markdown.index("SECTION 4") < markdown.index("SECTION 7")
        assert markdown.index("SECTION 7") < markdown.index("SECTION 8")

    def test_joins_all_section_bodies(self) -> None:
        sections = {
            "executive_summary": "Summary text.",
            "site_inventory": "Inventory text.",
        }
        markdown = assemble_report_markdown(sections)
        assert "Summary text." in markdown
        assert "Inventory text." in markdown

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


# ---------------------------------------------------------------------------
# build_source_register() — Phase 4/16 deterministic PART 11.3 table
# ---------------------------------------------------------------------------

class TestBuildSourceRegister:

    def test_returns_placeholder_when_no_claims(self) -> None:
        context = _make_context(research=ResearchBundle())
        register = build_source_register(context)
        assert "No externally researched claims" in register

    def test_includes_every_unique_claim_across_categories(self) -> None:
        context = _make_context(research=ResearchBundle(
            keyword_opportunities=[_make_claim("Keyword demand")],
            competitors=[_make_claim("Competitor found")],
            local_demand=[_make_claim("Local demand signal")],
        ))
        register = build_source_register(context)
        assert "Keyword demand" in register
        assert "Competitor found" in register
        assert "Local demand signal" in register

    def test_deduplicates_identical_claim_source_pairs(self) -> None:
        duplicate = _make_claim("Repeated claim")
        context = _make_context(research=ResearchBundle(
            keyword_opportunities=[duplicate],
            competitors=[duplicate],
        ))
        register = build_source_register(context)
        assert register.count("Repeated claim") == 1

    def test_matches_master_structure_column_format(self) -> None:
        context = _make_context(research=ResearchBundle(keyword_opportunities=[_make_claim()]))
        register = build_source_register(context)
        assert register.startswith("| # | Claim | Source URL | Retrieved |")

    def test_row_contains_source_url_and_retrieved_date(self) -> None:
        claim = _make_claim("Claim text")
        context = _make_context(research=ResearchBundle(keyword_opportunities=[claim]))
        register = build_source_register(context)
        assert claim.source_url in register
        assert claim.retrieved_date in register


# ---------------------------------------------------------------------------
# validate_assembled_report() — Phase 4 report-level validation
# ---------------------------------------------------------------------------

class TestValidateAssembledReport:

    _TEMPLATE = "# PART 1: EXECUTIVE SUMMARY\n\n# PART 2: FULL WEBSITE AUDIT\n"

    def test_well_formed_report_has_no_issues(self) -> None:
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nOverall score: 82.5/100.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nTechnical SEO: 90.0. On-Page SEO: 80.0.\n"
        )
        assert validate_assembled_report(report, self._TEMPLATE, _make_context()) == []

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
        context = _make_context(research=ResearchBundle(keyword_opportunities=[_make_claim()]))
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

    def test_flags_missing_overall_score(self) -> None:
        context = _make_context(score_breakdown=ScoreBreakdown(overall_score=82.5, category_scores=[]))
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nThe site is doing fine.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, context)
        assert any("overall score 82.5" in issue for issue in issues)

    def test_flags_missing_category_score(self) -> None:
        context = _make_context(score_breakdown=ScoreBreakdown(
            overall_score=82.5,
            category_scores=[CategoryScore(category="Technical SEO", weight_percent=40.0, score=90.0)],
        ))
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nOverall score: 82.5/100.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nNo category figures mentioned here.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, context)
        assert any("Technical SEO score 90.0" in issue for issue in issues)

    def test_accepts_rounded_score_text(self) -> None:
        context = _make_context(score_breakdown=ScoreBreakdown(overall_score=82.5, category_scores=[]))
        report = (
            "# PART 1: EXECUTIVE SUMMARY\n\nOverall score: 82 out of 100.\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, context)
        assert not any("overall score" in issue.lower() for issue in issues)

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

    def test_flags_executive_summary_over_400_words(self) -> None:
        report = (
            "# SECTION 7: EXECUTIVE SUMMARY\n\n" + ("word " * 401) + "\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert any("400-word maximum" in issue for issue in issues)

    def test_allows_executive_summary_at_400_words(self) -> None:
        report = (
            "# SECTION 7: EXECUTIVE SUMMARY\n\n" + ("word " * 400) + "\n\n"
            "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n"
        )
        issues = validate_assembled_report(report, self._TEMPLATE, _make_context())
        assert not any("word maximum" in issue for issue in issues)

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
            "executive_summary": "# PART 1: EXECUTIVE SUMMARY\n\nOverall score: 82.5/100.\n",
            "site_inventory": "# PART 2: FULL WEBSITE AUDIT\n\nBody.\n",
        }
        result = assemble_and_validate_report(sections, self._TEMPLATE, context)

        assert isinstance(result, AssembledReportResult)
        assert result.is_valid is True
        assert result.issues == []
        assert "PART 1" in result.markdown_report
        assert "PART 2" in result.markdown_report

    def test_malformed_sections_produce_invalid_result_with_issues(self) -> None:
        sections = {
            "executive_summary": "# PART 1: EXECUTIVE SUMMARY\n\nSummary.\n",
            # PART 2 heading missing entirely — should be flagged.
        }
        result = assemble_and_validate_report(sections, self._TEMPLATE, _make_context())

        assert result.is_valid is False
        assert any("Missing required PART headings" in issue for issue in result.issues)
        # The Markdown is still returned even though it failed validation.
        assert "PART 1" in result.markdown_report

    def test_replaces_llm_written_source_register_with_deterministic_one(self) -> None:
        context = _make_context(research=ResearchBundle(keyword_opportunities=[_make_claim("Real claim")]))
        sections = {
            "executive_summary": "# PART 1: EXECUTIVE SUMMARY\n\nSummary.\n",
            "structured_data_and_execution": (
                "# PART 11: METHODOLOGY, LIMITATIONS & SOURCES\n\n"
                "## 11.3 Source Register\n\n"
                "### Source Register Table\n\n"
                "| # | Claim | Source URL | Retrieved |\n|---|---|---|---|\n"
                "| 1 | Made up claim | https://not-real.example | 2020-01-01 |\n"
            ),
        }
        result = assemble_and_validate_report(sections, self._TEMPLATE, context)

        assert "Real claim" in result.markdown_report
        assert "Made up claim" not in result.markdown_report
        assert "not-real.example" not in result.markdown_report

    def test_appends_source_register_when_llm_omitted_the_heading(self) -> None:
        context = _make_context(research=ResearchBundle(competitors=[_make_claim("Competitor insight")]))
        sections = {
            "executive_summary": "# PART 1: EXECUTIVE SUMMARY\n\nSummary.\n",
            "structured_data_and_execution": "# PART 11: METHODOLOGY, LIMITATIONS & SOURCES\n\nNo table here.\n",
        }
        result = assemble_and_validate_report(sections, self._TEMPLATE, context)

        assert "### Source Register Table" in result.markdown_report
        assert "Competitor insight" in result.markdown_report

    def test_deduplicates_repeated_action_rows_across_the_assembled_report(self) -> None:
        sections = {
            "executive_summary": "# PART 1: EXECUTIVE SUMMARY\n\nSummary.\n",
            "structured_data_and_execution": (
                "# PART 10: PRIORITIZED EXECUTION PLAN & KPIs\n\n"
                "| Timeframe | Action | Owner | Priority |\n|---|---|---|---|\n"
                "| 30 days | Add meta descriptions | SEO Team | High |\n"
                "| 60 days | Add meta descriptions | SEO Team | High |\n"
            ),
        }
        result = assemble_and_validate_report(sections, self._TEMPLATE, _make_context())

        assert result.markdown_report.count("Add meta descriptions") == 1

