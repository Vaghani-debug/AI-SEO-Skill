"""
test/test_pdf_service.py

Unit tests for src/services/pdf_service.py.

All tests are fully offline and deterministic — ReportLab renders in-memory,
no network or LLM calls are involved. Tests assert on the structural
properties of the rendered PDF bytes and on the internal Markdown parsing
helpers, rather than on exact pixel/layout output.

Run with:
    pytest test/test_pdf_service.py -v
"""

import pytest  # Test runner

from src.services.pdf_service import (
    _compute_column_widths,    # Internal helper — content-aware table column sizing
    _is_table_separator,       # Internal helper — table separator row detection
    _markdown_line_to_xml,     # Internal helper — inline Markdown/HTML to ReportLab mini-HTML
    _plain_text_length,        # Internal helper — column-width content measurement
    _render_markdown_blocks,   # Internal helper — full block-level Markdown parsing
    _split_table_row,          # Internal helper — Markdown table row parsing
    render_report_pdf,         # Public function under test
)


# ---------------------------------------------------------------------------
# render_report_pdf() — public interface
# ---------------------------------------------------------------------------

class TestRenderReportPdf:
    """Tests for the top-level PDF rendering function."""

    def test_returns_valid_pdf_bytes(self) -> None:
        """The rendered output starts with the PDF file signature."""
        pdf_bytes = render_report_pdf(
            markdown_report="# Report\n\nSome content.",
            url="https://example.com",
            audit_id="test-audit-001",
        )
        assert pdf_bytes.startswith(b"%PDF-")

    def test_returns_non_empty_bytes(self) -> None:
        """A minimal report still renders a non-trivial PDF."""
        pdf_bytes = render_report_pdf(
            markdown_report="# Report\n\nSome content.",
            url="https://example.com",
            audit_id="test-audit-002",
        )
        assert len(pdf_bytes) > 500  # A real PDF, not a truncated/empty stub

    def test_handles_full_report_with_headings_tables_and_lists(self) -> None:
        """A realistic multi-section report with tables and lists renders without error."""
        markdown_report = (
            "# https://example.com \u2014 SEO Audit Report\n\n"
            "# PART 1: Full Website Audit\n\n"
            "## 1.1 Core Pages\n\n"
            "| #Index | Page Name | Title Tag | Recommendation |\n"
            "|--------|-----------|-----------|----------------|\n"
            "| 1 | Home | Example \\| Home | <ul><li>Improve title</li><li>Add schema</li></ul> |\n"
            "| 2 | About | About Us | Standard text without HTML |\n\n"
            "## 1.2 Notes\n\n"
            "- First finding\n"
            "- Second finding with **bold** and *italic* text\n\n"
            "1. Ordered step one\n"
            "2. Ordered step two\n\n"
            "---\n\n"
            "Some closing paragraph with a [link](https://example.com/page).\n"
        )
        pdf_bytes = render_report_pdf(markdown_report, "https://example.com", "test-audit-003")
        assert pdf_bytes.startswith(b"%PDF-")

    def test_handles_empty_report_body(self) -> None:
        """An empty Markdown report still renders a valid PDF (title block only)."""
        pdf_bytes = render_report_pdf(markdown_report="", url="https://example.com", audit_id="test-audit-004")
        assert pdf_bytes.startswith(b"%PDF-")


# ---------------------------------------------------------------------------
# _markdown_line_to_xml() — inline formatting
# ---------------------------------------------------------------------------

class TestMarkdownLineToXml:
    """Tests for the inline Markdown/HTML-to-ReportLab-XML conversion helper."""

    def test_converts_bold(self) -> None:
        assert _markdown_line_to_xml("**bold**") == "<b>bold</b>"

    def test_converts_italic(self) -> None:
        assert _markdown_line_to_xml("*italic*") == "<i>italic</i>"

    def test_converts_inline_code(self) -> None:
        assert _markdown_line_to_xml("`code`") == '<font face="Courier">code</font>'

    def test_converts_link(self) -> None:
        result = _markdown_line_to_xml("[click here](https://example.com)")
        assert '<link href="https://example.com"' in result
        assert "click here</link>" in result

    def test_escapes_raw_ampersand(self) -> None:
        """A literal '&' not already part of an HTML entity is escaped for XML safety."""
        assert _markdown_line_to_xml("Tom & Jerry") == "Tom &amp; Jerry"

    def test_does_not_double_escape_html_entities(self) -> None:
        """An already-escaped entity like '&amp;' is not turned into '&amp;amp;'."""
        assert _markdown_line_to_xml("Pages &amp; URLs") == "Pages &amp; URLs"

    def test_expands_embedded_html_list(self) -> None:
        """Embedded <ul><li> HTML (common in table cells) becomes bullet lines."""
        result = _markdown_line_to_xml("<ul><li>First item</li><li>Second item</li></ul>")
        assert result == "\u2022 First item<br/>\u2022 Second item"


# ---------------------------------------------------------------------------
# _split_table_row() / _is_table_separator() — table parsing
# ---------------------------------------------------------------------------

class TestSplitTableRow:
    """Tests for the Markdown table row cell-splitting helper."""

    def test_splits_simple_row(self) -> None:
        assert _split_table_row("| A | B | C |") == ["A", "B", "C"]

    def test_preserves_escaped_pipe_as_literal_character(self) -> None:
        """A '\\|' inside a cell is treated as a literal pipe, not a column separator."""
        assert _split_table_row(r"| Title \| Brand | Value |") == ["Title | Brand", "Value"]

    def test_handles_empty_cells(self) -> None:
        assert _split_table_row("| A | | C |") == ["A", "", "C"]


class TestIsTableSeparator:
    """Tests for Markdown table header-separator row detection."""

    def test_detects_simple_separator(self) -> None:
        assert _is_table_separator("|---|---|---|") is True

    def test_detects_separator_with_alignment_colons(self) -> None:
        assert _is_table_separator("|:---|:---:|---:|") is True

    def test_rejects_a_data_row(self) -> None:
        assert _is_table_separator("| A | B | C |") is False

    def test_rejects_a_non_table_line(self) -> None:
        assert _is_table_separator("Just a sentence.") is False


# ---------------------------------------------------------------------------
# _plain_text_length() / _compute_column_widths() — content-aware column sizing
# ---------------------------------------------------------------------------

class TestPlainTextLength:
    """Tests for the column-width content measurement helper."""

    def test_measures_plain_text(self) -> None:
        assert _plain_text_length("Home") == 4

    def test_strips_markdown_formatting_before_measuring(self) -> None:
        assert _plain_text_length("**Home**") == 4

    def test_uses_longest_line_of_an_embedded_html_list(self) -> None:
        """A multi-item bulleted cell is measured by its longest bullet, not the total."""
        cell = "<ul><li>Short</li><li>A much longer recommendation here</li></ul>"
        assert _plain_text_length(cell) == len("\u2022 A much longer recommendation here")


class TestComputeColumnWidths:
    """Tests for content-proportional table column width distribution."""

    def test_short_index_column_is_narrower_than_long_text_column(self) -> None:
        header_cells = ["#", "Page", "Recommendation"]
        rows = [
            ["1", "Home", "Add descriptive alt text to all product images across the catalog"],
            ["2", "About", "Improve internal linking structure between category and product pages"],
        ]
        widths = _compute_column_widths(header_cells, rows, available_width=500.0)
        assert widths[0] < widths[2]

    def test_widths_sum_to_available_width(self) -> None:
        header_cells = ["#", "Page", "Recommendation"]
        rows = [["1", "Home", "Some recommendation text."], ["2", "About", "Another one."]]
        widths = _compute_column_widths(header_cells, rows, available_width=500.0)
        assert sum(widths) == pytest.approx(500.0)

    def test_no_column_exceeds_the_max_width_fraction(self) -> None:
        header_cells = ["#", "Page", "Title", "Recommendation"]
        rows = [["1", "Home", "Example Title", "x" * 500]]  # Extremely long outlier cell
        widths = _compute_column_widths(header_cells, rows, available_width=500.0)
        assert widths[3] <= 500.0 * 0.45 + 1e-6  # Small epsilon for float rounding

    def test_returns_empty_list_for_zero_columns(self) -> None:
        assert _compute_column_widths([], [], available_width=500.0) == []


# ---------------------------------------------------------------------------
# _render_markdown_blocks() — block-level structure
# ---------------------------------------------------------------------------

class TestRenderMarkdownBlocks:
    """Tests for the block-level Markdown parser producing ReportLab flowables."""

    def test_produces_a_flowable_per_heading_and_paragraph(self) -> None:
        flowables = _render_markdown_blocks("# Heading\n\nParagraph text.", _sample_styles())
        assert len(flowables) == 2

    def test_table_block_produces_a_single_table_flowable(self) -> None:
        markdown_report = "| A | B |\n|---|---|\n| 1 | 2 |\n"
        flowables = _render_markdown_blocks(markdown_report, _sample_styles())
        # A Table flowable plus a trailing Spacer
        assert len(flowables) == 2

    def test_bullet_list_produces_a_single_list_flowable(self) -> None:
        markdown_report = "- item one\n- item two\n"
        flowables = _render_markdown_blocks(markdown_report, _sample_styles())
        assert len(flowables) == 1


def _sample_styles() -> dict:
    """Return real ParagraphStyles via the module's own builder (avoids duplicating style setup)."""
    from src.services.pdf_service import _build_styles

    return _build_styles()
