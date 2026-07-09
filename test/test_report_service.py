"""
test/test_report_service.py

Unit tests for src/services/report_service.py.

All Gemini API calls are mocked so these tests run offline without tokens.
Each test exercises one specific behaviour, error path, or formatting rule.

Run with:
    pytest test/test_report_service.py -v
"""

from datetime import datetime  # For timestamp assertions
from unittest.mock import AsyncMock, MagicMock, patch  # Mocking tools

import pytest  # pytest: test runner

from src.config import Settings  # Provides API key and model configuration
from src.services.extractor_service import (
    AuditEvidence,       # Main evidence dataclass
    ImageInfo,           # Image metadata
    RobotsTxtEvidence,   # robots.txt findings
    SitemapEvidence,     # Sitemap accessibility
)
from src.services.prompt_loader import PromptContext  # Guidance context
from src.services.report_service import (
    ReportResult,          # Return type
    _build_user_message,   # Internal helper — evidence formatting
    _format_evidence,      # Internal helper — evidence formatting
    generate_report,       # Public function under test
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
    return s


@pytest.fixture
def prompt_context() -> PromptContext:
    """Minimal PromptContext with a URL placeholder."""
    return PromptContext(
        audit_prompt="You are an SEO consultant. Audit {{website_url}}.",
        seo_skill="Priority: Crawlability, Technical, On-Page, Content.",
        report_specification="Report sections: Executive Summary, Technical SEO.",
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


# ---------------------------------------------------------------------------
# Tests for generate_report() — error paths
# ---------------------------------------------------------------------------

class TestGenerateReportErrors:
    """Tests for error conditions in report generation."""

    async def test_missing_api_key_raises_value_error(self, prompt_context: PromptContext) -> None:
        """ValueError is raised when GEMINI_API_KEY is not configured."""
        s = Settings()
        s.gemini_api_key = ""  # Empty key — not configured
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
