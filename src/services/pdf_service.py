"""
src/services/pdf_service.py

Deterministic Markdown-to-PDF rendering service.

Responsibility: convert a completed audit's stored Markdown report into a
professional, paginated PDF document using ReportLab. This is the only
module that depends on ReportLab — routes and other services stay
unaware of which rendering library is used.

This module performs no LLM calls and no I/O beyond returning bytes; the
caller (the API route) is responsible for loading the persisted report
and deciding what to do with the rendered PDF.

Public interface
----------------
    render_report_pdf(markdown_report, url, audit_id) -> bytes
"""

import html  # html.unescape/html.escape — safely round-trip HTML entities already present in reports
import io  # io.BytesIO — in-memory buffer ReportLab writes the PDF into
import logging  # Standard logging — records render start/success/failure
import re  # Markdown block/inline parsing (headings, tables, lists, bold/italic/links)

from reportlab.lib import colors  # Named/hex colors for brand-consistent styling
from reportlab.lib.enums import TA_LEFT  # Left-alignment constant for paragraph styles
from reportlab.lib.pagesizes import A4  # Standard A4 page size for the generated report
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # Paragraph style base + custom styles
from reportlab.lib.units import cm  # cm unit for margins and spacing
from reportlab.platypus import (  # Flowable building blocks assembled into the final PDF story
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Module-level logger
logger = logging.getLogger(__name__)  # Resolves to "src.services.pdf_service"

# ---------------------------------------------------------------------------
# Layout and brand constants
# ---------------------------------------------------------------------------

_PAGE_MARGIN_CM = 2.0  # Uniform page margin on all four sides
_MIN_COLUMN_WIDTH_CM = 1.4  # No column shrinks below this, regardless of how short its content is
_MAX_COLUMN_WIDTH_FRACTION = 0.45  # No single column exceeds this share of the table width
_BRAND_COLOR = colors.HexColor("#0f3460")  # Matches the UI's dark blue brand color
_HEADER_TEXT_COLOR = colors.HexColor("#1a1a2e")  # Matches the UI's dark navy heading color
_META_TEXT_COLOR = colors.HexColor("#6b7280")  # Muted grey for the audited URL line
_TABLE_HEADER_BG = colors.HexColor("#f0f4ff")  # Matches the UI's light blue table header background
_TABLE_GRID_COLOR = colors.HexColor("#dde3ec")  # Matches the UI's table border color
_ROW_ALT_BG = colors.HexColor("#f9fbfd")  # Matches the UI's zebra-stripe row background
_FOOTER_TEXT_COLOR = colors.HexColor("#94a3b8")  # Muted footer text color

# ---------------------------------------------------------------------------
# Inline Markdown/HTML parsing helpers
# ---------------------------------------------------------------------------

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`]+?)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^(-{3,}|_{3,}|\*{3,})$")
_BULLET_ITEM_RE = re.compile(r"^[-*]\s+(.*)$")
_NUMBERED_ITEM_RE = re.compile(r"^\d+\.\s+(.*)$")
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")

# Reports occasionally embed raw HTML lists inside table cells (the LLM's
# way of formatting multiple recommendations in one cell). These are
# expanded into bullet lines rather than left as unrendered HTML tags.
_HTML_LIST_BLOCK_RE = re.compile(r"<(ul|ol)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_HTML_LIST_ITEM_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_LINE_BREAK_TOKEN = "@@BR@@"  # Placeholder immune to html.escape(); swapped for <br/> last


def _expand_html_lists(text: str) -> str:
    """Convert embedded <ul>/<ol><li> HTML into bullet lines joined by a line-break token."""

    def _replace_block(match: re.Match[str]) -> str:
        items = _HTML_LIST_ITEM_RE.findall(match.group(2))
        return _LINE_BREAK_TOKEN.join(f"\u2022 {item.strip()}" for item in items)

    return _HTML_LIST_BLOCK_RE.sub(_replace_block, text)


def _markdown_line_to_xml(text: str) -> str:
    """
    Convert one line of report Markdown (optionally containing embedded HTML
    lists, bold/italic/code spans, or links) into ReportLab's mini-HTML markup.

    Order matters: HTML lists are expanded first, then HTML entities already
    present in the source (e.g. "&amp;") are unescaped and re-escaped exactly
    once to avoid double-escaping, and only then are Markdown spans converted
    to ReportLab tags — those tags must survive the XML-escaping step.
    """
    expanded = _expand_html_lists(text)
    unescaped = html.unescape(expanded)
    escaped = html.escape(unescaped, quote=False)
    escaped = _LINK_RE.sub(r'<link href="\2" color="#0f3460">\1</link>', escaped)
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = _ITALIC_RE.sub(r"<i>\1</i>", escaped)
    escaped = _INLINE_CODE_RE.sub(r'<font face="Courier">\1</font>', escaped)
    escaped = escaped.replace(_LINE_BREAK_TOKEN, "<br/>")
    return escaped.strip()


def _split_table_row(row: str) -> list[str]:
    """
    Split a Markdown table row into trimmed cell values.

    Honors backslash-escaped pipes (``\\|``) as literal characters rather
    than column separators, so titles/URLs containing a literal "|" are not
    truncated mid-cell.
    """
    trimmed = row.strip().strip("|")
    raw_cells = re.split(r"(?<!\\)\|", trimmed)
    return [cell.strip().replace("\\|", "|") for cell in raw_cells]


def _is_table_separator(line: str) -> bool:
    """Return True if line is a Markdown table header separator row (e.g. ``|---|:--:|``)."""
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return bool(cells) and all(_TABLE_SEPARATOR_CELL_RE.match(cell) for cell in cells)


def _plain_text_length(cell: str) -> int:
    """Approximate a cell's rendered character length, stripping Markdown/HTML markup first."""
    expanded = _expand_html_lists(cell)
    plain = html.unescape(expanded)
    plain = _LINK_RE.sub(r"\1", plain)
    plain = _BOLD_RE.sub(r"\1", plain)
    plain = _ITALIC_RE.sub(r"\1", plain)
    plain = _INLINE_CODE_RE.sub(r"\1", plain)
    # Bulleted lines wrap independently, so the longest single line — not the
    # concatenated total — best represents the width that line actually needs.
    lines = plain.split(_LINE_BREAK_TOKEN)
    return max((len(line.strip()) for line in lines), default=0)


def _compute_column_widths(
    header_cells: list[str], rows: list[list[str]], available_width: float
) -> list[float]:
    """
    Distribute available_width across columns in proportion to their content length.

    Short columns (e.g. a numeric index) shrink toward _MIN_COLUMN_WIDTH_CM instead
    of receiving an equal share, while columns with long text (e.g. recommendations)
    receive the freed-up space, capped at _MAX_COLUMN_WIDTH_FRACTION of the table.
    """
    column_count = len(header_cells)
    if column_count == 0:
        return []

    weights: list[float] = []
    for col_index in range(column_count):
        lengths = [_plain_text_length(header_cells[col_index])]
        lengths.extend(
            _plain_text_length(row[col_index]) for row in rows if col_index < len(row)
        )
        # The 75th percentile avoids letting one unusually long outlier cell
        # dominate the column while still favoring genuinely long content.
        lengths.sort()
        representative = lengths[int(len(lengths) * 0.75)]
        weights.append(max(representative, 3))  # Floor avoids zero-weight columns (e.g. "#")

    min_width = _MIN_COLUMN_WIDTH_CM * cm
    max_width = available_width * _MAX_COLUMN_WIDTH_FRACTION
    total_weight = sum(weights)

    widths = [(weight / total_weight) * available_width for weight in weights]
    widths = [min(max(width, min_width), max_width) for width in widths]

    # Iteratively push any remaining/over-allocated width onto columns that
    # still have headroom (below max_width) or slack (above min_width), so a
    # column pinned at one bound never absorbs space meant for another.
    for _ in range(column_count + 2):
        remainder = available_width - sum(widths)
        if abs(remainder) < 1e-6:
            break
        if remainder > 0:
            eligible = [i for i in range(column_count) if widths[i] < max_width - 1e-9]
        else:
            eligible = [i for i in range(column_count) if widths[i] > min_width + 1e-9]
        if not eligible:
            break
        eligible_weight_sum = sum(weights[i] for i in eligible)
        for i in eligible:
            widths[i] += remainder * (weights[i] / eligible_weight_sum)
        widths = [min(max(width, min_width), max_width) for width in widths]

    # A table with many narrow columns can hit its min-width floor for every
    # column and still exceed available_width; scale down uniformly as a last
    # resort so the table never overflows the page.
    total = sum(widths)
    if total > available_width:
        widths = [width * (available_width / total) for width in widths]

    return widths


# ---------------------------------------------------------------------------
# Paragraph styles
# ---------------------------------------------------------------------------

def _build_styles() -> dict[str, ParagraphStyle]:
    """Return the named ParagraphStyles used throughout the rendered report."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle", parent=base["Title"], fontSize=18, leading=22,
            textColor=_BRAND_COLOR, alignment=TA_LEFT, spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "ReportMeta", parent=base["Normal"], fontSize=9,
            textColor=_META_TEXT_COLOR, spaceAfter=10,
        ),
        "h1": ParagraphStyle(
            "H1", parent=base["Heading1"], fontSize=15, leading=18,
            textColor=_BRAND_COLOR, spaceBefore=14, spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontSize=12.5, leading=15,
            textColor=_HEADER_TEXT_COLOR, spaceBefore=10, spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "H3", parent=base["Heading3"], fontSize=11, leading=13.5,
            textColor=colors.HexColor("#374151"), spaceBefore=8, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["Normal"], fontSize=9.5, leading=13.5, spaceAfter=6,
        ),
        "table_header": ParagraphStyle(
            "TableHeader", parent=base["Normal"], fontSize=8, leading=10.5,
            fontName="Helvetica-Bold", textColor=_HEADER_TEXT_COLOR,
        ),
        "table_cell": ParagraphStyle(
            "TableCell", parent=base["Normal"], fontSize=8, leading=10.5,
        ),
    }


# ---------------------------------------------------------------------------
# Block-level Markdown rendering
# ---------------------------------------------------------------------------

def _render_table(table_lines: list[str], styles: dict[str, ParagraphStyle]) -> Table:
    """Render one Markdown table (header row + data rows, separator already excluded) as a ReportLab Table."""
    header_cells = _split_table_row(table_lines[0])
    column_count = len(header_cells)

    normalized_rows: list[list[str]] = []
    for line in table_lines[1:]:
        row = _split_table_row(line)
        if len(row) < column_count:
            row = row + [""] * (column_count - len(row))
        elif len(row) > column_count:
            row = row[:column_count]
        normalized_rows.append(row)

    header_row = [Paragraph(_markdown_line_to_xml(cell), styles["table_header"]) for cell in header_cells]
    body_rows = [
        [Paragraph(_markdown_line_to_xml(cell), styles["table_cell"]) for cell in row]
        for row in normalized_rows
    ]
    table_data = [header_row] + body_rows

    available_width = A4[0] - 2 * _PAGE_MARGIN_CM * cm
    column_widths = _compute_column_widths(header_cells, normalized_rows, available_width)

    table = Table(table_data, colWidths=column_widths, repeatRows=1)
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), _TABLE_HEADER_BG),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, _TABLE_GRID_COLOR),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for row_index in range(1, len(table_data)):
        if row_index % 2 == 0:
            style_commands.append(("BACKGROUND", (0, row_index), (-1, row_index), _ROW_ALT_BG))
    table.setStyle(TableStyle(style_commands))
    return table


def _render_markdown_blocks(markdown_report: str, styles: dict[str, ParagraphStyle]) -> list:
    """Parse the full Markdown report into a flat list of ReportLab flowables."""
    flowables: list = []
    lines = markdown_report.replace("\r\n", "\n").split("\n")

    paragraph_buffer: list[str] = []
    list_buffer: list[str] = []
    list_ordered = False

    def _flush_paragraph() -> None:
        if not paragraph_buffer:
            return
        text = " ".join(paragraph_buffer).strip()
        if text:
            flowables.append(Paragraph(_markdown_line_to_xml(text), styles["body"]))
        paragraph_buffer.clear()

    def _flush_list() -> None:
        if not list_buffer:
            return
        items = [
            ListItem(Paragraph(_markdown_line_to_xml(item), styles["body"]))
            for item in list_buffer
        ]
        flowables.append(
            ListFlowable(
                items,
                bulletType="1" if list_ordered else "bullet",
                leftIndent=1.1 * cm,
                bulletFontSize=9,
                spaceBefore=2,
                spaceAfter=8,
            )
        )
        list_buffer.clear()

    index = 0
    while index < len(lines):
        stripped = lines[index].strip()

        if not stripped:
            _flush_paragraph()
            _flush_list()
            index += 1
            continue

        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            _flush_paragraph()
            _flush_list()
            level = min(len(heading_match.group(1)), 3)
            style_key = {1: "h1", 2: "h2", 3: "h3"}[level]
            flowables.append(Paragraph(_markdown_line_to_xml(heading_match.group(2)), styles[style_key]))
            index += 1
            continue

        if _HR_RE.match(stripped):
            _flush_paragraph()
            _flush_list()
            flowables.append(Spacer(1, 0.2 * cm))
            flowables.append(HRFlowable(width="100%", thickness=0.75, color=_TABLE_GRID_COLOR))
            flowables.append(Spacer(1, 0.3 * cm))
            index += 1
            continue

        is_table_start = (
            stripped.startswith("|") and stripped.endswith("|")
            and index + 1 < len(lines) and _is_table_separator(lines[index + 1])
        )
        if is_table_start:
            _flush_paragraph()
            _flush_list()
            table_lines = [stripped]
            index += 2  # Skip the header row and its separator row
            while index < len(lines) and lines[index].strip().startswith("|") and lines[index].strip().endswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            flowables.append(_render_table(table_lines, styles))
            flowables.append(Spacer(1, 0.4 * cm))
            continue

        bullet_match = _BULLET_ITEM_RE.match(stripped)
        numbered_match = _NUMBERED_ITEM_RE.match(stripped) if not bullet_match else None
        if bullet_match or numbered_match:
            _flush_paragraph()
            if not list_buffer:
                list_ordered = bool(numbered_match)
            list_buffer.append((bullet_match or numbered_match).group(1))
            index += 1
            continue

        _flush_list()
        paragraph_buffer.append(stripped)
        index += 1

    _flush_paragraph()
    _flush_list()
    return flowables


def _build_title_block(url: str, styles: dict[str, ParagraphStyle]) -> list:
    """Return the fixed title/meta flowables shown at the top of every rendered report."""
    return [
        Paragraph("SEO Audit Report", styles["title"]),
        Paragraph(_markdown_line_to_xml(url), styles["meta"]),
        HRFlowable(width="100%", thickness=1, color=_BRAND_COLOR),
        Spacer(1, 0.4 * cm),
    ]


def _draw_footer(canvas, doc, audit_id: str) -> None:  # noqa: ANN001 - ReportLab callback signature
    """Draw the audit ID and page number footer on every page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_FOOTER_TEXT_COLOR)
    canvas.drawString(_PAGE_MARGIN_CM * cm, 1.2 * cm, f"AI SEO Agent — Audit ID: {audit_id}")
    canvas.drawRightString(A4[0] - _PAGE_MARGIN_CM * cm, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def render_report_pdf(markdown_report: str, url: str, audit_id: str) -> bytes:
    """
    Render a Markdown SEO audit report into a paginated PDF document.

    Args:
        markdown_report: Full Markdown text of the report, as persisted.
        url: The audited URL, shown in the PDF's title block.
        audit_id: Unique audit identifier, shown in the PDF's footer.

    Returns:
        The rendered PDF file content as bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=_PAGE_MARGIN_CM * cm,
        bottomMargin=_PAGE_MARGIN_CM * cm,
        leftMargin=_PAGE_MARGIN_CM * cm,
        rightMargin=_PAGE_MARGIN_CM * cm,
        title=f"SEO Audit Report - {url}",
    )

    styles = _build_styles()
    story = _build_title_block(url, styles) + _render_markdown_blocks(markdown_report, styles)

    doc.build(
        story,
        onFirstPage=lambda canvas, doc_template: _draw_footer(canvas, doc_template, audit_id),
        onLaterPages=lambda canvas, doc_template: _draw_footer(canvas, doc_template, audit_id),
    )

    pdf_bytes = buffer.getvalue()
    logger.info("Rendered PDF report for audit_id=%s (%d bytes)", audit_id, len(pdf_bytes))
    return pdf_bytes
