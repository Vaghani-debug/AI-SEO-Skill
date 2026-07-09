"""
test/test_pdf_service.py

Unit tests for src/services/pdf_service.py.

Tests use real ReportLab rendering with a temporary directory so the PDF
is actually written — this is the most reliable way to verify the output.
All tests use tmp_path (pytest's built-in temporary directory fixture) so
no files are left on disk after the test run.

Run with:
    pytest test/test_pdf_service.py -v
"""

from datetime import datetime, timezone  # For test timestamps
from pathlib import Path  # For path assertions
import pytest  # Test runner

from src.config import Settings  # Provides reports_dir and other settings
from src.services.pdf_service import (
    _build_styles,       # Style builder — tested for completeness
    _escape_xml,         # XML escape helper — tested for correctness
    _inline_to_html,     # Inline HTML serialiser — tested with BeautifulSoup Tags
    _markdown_to_flowables,  # Core Markdown converter — tested with various inputs
    generate_pdf,        # Public function under test
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings with a temporary reports directory for test isolation."""
    s = Settings()
    s.reports_dir = str(tmp_path / "reports")  # Use a fresh temp dir for each test
    return s


@pytest.fixture
def audit_datetime() -> datetime:
    """Fixed audit timestamp for deterministic test assertions."""
    return datetime(2026, 7, 9, 14, 0, 0, tzinfo=timezone.utc)


SAMPLE_MARKDOWN = """
# Executive Summary

This website has a good overall SEO foundation.

## Technical SEO

- HTTPS is properly configured.
- The robots.txt file is accessible.
- **Critical**: The /public/ directory is blocked by robots.txt.

## On-Page SEO

The homepage title is present but could be improved.

### Heading Structure

The H1 tag says: "Elevate Your Business With Innovative IT Solutions."

> This heading is generic and does not contain primary keywords.

## Top 3 Quick Wins

1. Fix robots.txt to allow image crawling.
2. Rewrite the H1 to include the city and service keywords.
3. Add a meta description.

---

## Overall Conclusion

The website is performing well in several areas.
"""


# ---------------------------------------------------------------------------
# Tests for generate_pdf() — file creation
# ---------------------------------------------------------------------------

class TestGeneratePdfFileCreation:
    """Tests that generate_pdf() creates valid files on disk."""

    def test_returns_path_object(self, settings: Settings, audit_datetime: datetime) -> None:
        """generate_pdf() returns a Path object."""
        result = generate_pdf(
            audit_id="test-audit-001",
            normalized_url="https://example.com",
            markdown_report=SAMPLE_MARKDOWN,
            created_at=audit_datetime,
            settings=settings,
        )
        assert isinstance(result, Path)  # Return type is Path

    def test_pdf_file_exists_on_disk(self, settings: Settings, audit_datetime: datetime) -> None:
        """The PDF file exists at the returned path after generation."""
        result = generate_pdf(
            audit_id="test-audit-002",
            normalized_url="https://example.com",
            markdown_report=SAMPLE_MARKDOWN,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.exists()  # File must be on disk

    def test_pdf_file_is_non_empty(self, settings: Settings, audit_datetime: datetime) -> None:
        """The generated PDF file contains data (is not empty)."""
        result = generate_pdf(
            audit_id="test-audit-003",
            normalized_url="https://example.com",
            markdown_report=SAMPLE_MARKDOWN,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.stat().st_size > 0  # File must have content

    def test_pdf_starts_with_pdf_magic_bytes(self, settings: Settings, audit_datetime: datetime) -> None:
        """The generated file begins with the PDF magic bytes %%PDF-."""
        result = generate_pdf(
            audit_id="test-audit-004",
            normalized_url="https://example.com",
            markdown_report=SAMPLE_MARKDOWN,
            created_at=audit_datetime,
            settings=settings,
        )
        header = result.read_bytes()[:5]  # Read the first 5 bytes
        assert header == b"%PDF-"  # Valid PDF signature

    def test_pdf_filename_matches_audit_id(self, settings: Settings, audit_datetime: datetime) -> None:
        """The PDF filename is {audit_id}.pdf."""
        audit_id = "my-audit-xyz-123"
        result = generate_pdf(
            audit_id=audit_id,
            normalized_url="https://example.com",
            markdown_report=SAMPLE_MARKDOWN,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.name == f"{audit_id}.pdf"  # Correct filename

    def test_reports_directory_created_if_missing(self, tmp_path: Path, audit_datetime: datetime) -> None:
        """The reports directory is created automatically if it does not exist."""
        settings = Settings()
        settings.reports_dir = str(tmp_path / "new" / "nested" / "reports")
        # This nested directory does not exist yet

        result = generate_pdf(
            audit_id="test-audit-dir",
            normalized_url="https://example.com",
            markdown_report=SAMPLE_MARKDOWN,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.exists()  # Directory and file were created

    def test_two_audits_produce_different_files(self, settings: Settings, audit_datetime: datetime) -> None:
        """Two calls with different audit IDs produce two separate files."""
        result1 = generate_pdf(
            audit_id="audit-a",
            normalized_url="https://example.com",
            markdown_report=SAMPLE_MARKDOWN,
            created_at=audit_datetime,
            settings=settings,
        )
        result2 = generate_pdf(
            audit_id="audit-b",
            normalized_url="https://example.com",
            markdown_report=SAMPLE_MARKDOWN,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result1 != result2    # Different paths
        assert result1.exists()      # Both files exist
        assert result2.exists()


# ---------------------------------------------------------------------------
# Tests for generate_pdf() — content variants
# ---------------------------------------------------------------------------

class TestGeneratePdfContentVariants:
    """Tests that various Markdown content types are handled without error."""

    def test_empty_markdown_does_not_raise(self, settings: Settings, audit_datetime: datetime) -> None:
        """An empty Markdown string produces a valid PDF without raising."""
        result = generate_pdf(
            audit_id="empty-report",
            normalized_url="https://example.com",
            markdown_report="",
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.exists()
        assert result.read_bytes()[:5] == b"%PDF-"

    def test_only_headings_does_not_raise(self, settings: Settings, audit_datetime: datetime) -> None:
        """Markdown with only headings produces a valid PDF."""
        md = "# H1\n## H2\n### H3"
        result = generate_pdf(
            audit_id="headings-only",
            normalized_url="https://example.com",
            markdown_report=md,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.read_bytes()[:5] == b"%PDF-"

    def test_only_bullet_list_does_not_raise(self, settings: Settings, audit_datetime: datetime) -> None:
        """Markdown with only bullet lists produces a valid PDF."""
        md = "- Item one\n- Item two\n- Item three"
        result = generate_pdf(
            audit_id="bullets-only",
            normalized_url="https://example.com",
            markdown_report=md,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.read_bytes()[:5] == b"%PDF-"

    def test_ordered_list_does_not_raise(self, settings: Settings, audit_datetime: datetime) -> None:
        """Markdown with a numbered list produces a valid PDF."""
        md = "1. First item\n2. Second item\n3. Third item"
        result = generate_pdf(
            audit_id="numbered-list",
            normalized_url="https://example.com",
            markdown_report=md,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.read_bytes()[:5] == b"%PDF-"

    def test_horizontal_rule_does_not_raise(self, settings: Settings, audit_datetime: datetime) -> None:
        """Horizontal rules (---) in Markdown are handled without error."""
        md = "Section A\n\n---\n\nSection B"
        result = generate_pdf(
            audit_id="with-rule",
            normalized_url="https://example.com",
            markdown_report=md,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.read_bytes()[:5] == b"%PDF-"

    def test_bold_and_italic_inline_does_not_raise(self, settings: Settings, audit_datetime: datetime) -> None:
        """Markdown with **bold** and *italic* text is handled without error."""
        md = "This is **bold** and this is *italic* text."
        result = generate_pdf(
            audit_id="bold-italic",
            normalized_url="https://example.com",
            markdown_report=md,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.read_bytes()[:5] == b"%PDF-"

    def test_blockquote_does_not_raise(self, settings: Settings, audit_datetime: datetime) -> None:
        """Markdown blockquotes (> text) are handled without error."""
        md = "> This is a blockquote about SEO findings."
        result = generate_pdf(
            audit_id="blockquote",
            normalized_url="https://example.com",
            markdown_report=md,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.read_bytes()[:5] == b"%PDF-"

    def test_special_characters_in_url_do_not_raise(self, settings: Settings, audit_datetime: datetime) -> None:
        """URLs with special characters (&, =, ?) do not break the PDF renderer."""
        result = generate_pdf(
            audit_id="special-url",
            normalized_url="https://example.com/page?q=seo&lang=en",
            markdown_report="# Report\n\nContent.",
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.read_bytes()[:5] == b"%PDF-"

    def test_markdown_with_table_does_not_raise(self, settings: Settings, audit_datetime: datetime) -> None:
        """Markdown tables are converted to indented rows without raising."""
        md = (
            "| Priority | Recommendation | Difficulty |\n"
            "| --- | --- | --- |\n"
            "| 1 | Fix robots.txt | Low |\n"
            "| 2 | Update H1 | Low |\n"
        )
        result = generate_pdf(
            audit_id="with-table",
            normalized_url="https://example.com",
            markdown_report=md,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.read_bytes()[:5] == b"%PDF-"

    def test_full_sample_report_does_not_raise(self, settings: Settings, audit_datetime: datetime) -> None:
        """The complete SAMPLE_MARKDOWN renders without error."""
        result = generate_pdf(
            audit_id="full-sample",
            normalized_url="https://www.truelinesolution.com",
            markdown_report=SAMPLE_MARKDOWN,
            created_at=audit_datetime,
            settings=settings,
        )
        assert result.read_bytes()[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Tests for _escape_xml()
# ---------------------------------------------------------------------------

class TestEscapeXml:
    """Tests for the XML special-character escape helper."""

    def test_ampersand_escaped(self) -> None:
        """& is replaced with &amp;."""
        assert _escape_xml("a & b") == "a &amp; b"

    def test_less_than_escaped(self) -> None:
        """< is replaced with &lt;."""
        assert _escape_xml("a < b") == "a &lt; b"

    def test_greater_than_escaped(self) -> None:
        """> is replaced with &gt;."""
        assert _escape_xml("a > b") == "a &gt; b"

    def test_multiple_special_chars(self) -> None:
        """Multiple special characters in one string are all escaped."""
        result = _escape_xml("<tag attr='x'> & </tag>")
        assert "&lt;" in result
        assert "&gt;" in result
        assert "&amp;" in result

    def test_plain_text_unchanged(self) -> None:
        """Plain text without special characters is returned unchanged."""
        assert _escape_xml("Hello World") == "Hello World"

    def test_empty_string_unchanged(self) -> None:
        """An empty string returns an empty string."""
        assert _escape_xml("") == ""

    def test_ampersand_escaped_first_no_double_escape(self) -> None:
        """& is not double-escaped (e.g. &amp; must not become &amp;amp;)."""
        result = _escape_xml("Tom & Jerry")
        assert result == "Tom &amp; Jerry"
        assert "amp;amp" not in result  # No double-escape


# ---------------------------------------------------------------------------
# Tests for _build_styles()
# ---------------------------------------------------------------------------

class TestBuildStyles:
    """Tests that all expected style keys are present."""

    def test_all_required_styles_present(self) -> None:
        """_build_styles() returns all required style keys."""
        styles = _build_styles()
        required_keys = ["h1", "h2", "h3", "h4", "body", "bullet", "cover_url", "cover_date"]
        for key in required_keys:
            assert key in styles, f"Missing style key: {key}"

    def test_h1_larger_than_h2(self) -> None:
        """H1 font size is larger than H2."""
        styles = _build_styles()
        assert styles["h1"].fontSize > styles["h2"].fontSize

    def test_h2_larger_than_h3(self) -> None:
        """H2 font size is larger than H3."""
        styles = _build_styles()
        assert styles["h2"].fontSize > styles["h3"].fontSize
