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
from unittest.mock import AsyncMock, patch  # Mocking tools

import pytest  # pytest: test runner

from src.config import Settings  # Provides API key and model configuration
from src.services.extractor_service import (
    AuditEvidence,       # Main evidence dataclass
    ImageInfo,           # Image metadata
    RobotsTxtEvidence,   # robots.txt findings
    SitemapEvidence,     # Sitemap accessibility
)
from src.services.llm_service import LLMProviderError  # Raised on empty/failed LLM responses
from src.services.prompt_loader import PromptContext  # Guidance context
from src.services.report_service import (
    ReportResult,               # Return type
    _build_retry_user_message,  # Internal helper — retry instruction builder
    _build_user_message,        # Internal helper — evidence formatting
    _extract_section_body,      # Internal helper — subsection text extraction
    _find_banned_phrases,       # Internal helper — contamination/branding detection
    _find_table_blocks,         # Internal helper — Markdown table detection
    _format_evidence,           # Internal helper — evidence formatting
    _split_table_row,           # Internal helper — Markdown table row parsing
    _validate_citation_columns,  # Internal helper — Source/Retrieved validation
    _validate_location_section,  # Internal helper — SECTION 3 conditional validation
    generate_report,             # Public function under test
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
