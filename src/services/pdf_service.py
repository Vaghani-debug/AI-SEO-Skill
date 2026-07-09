"""
src/services/pdf_service.py

Markdown-to-PDF report generation service.

Responsibility: convert the Markdown SEO audit report produced by
report_service into a professional, client-ready PDF file and save
it to the local reports/ directory.

The PDF is built with ReportLab Platypus — the document-layout engine
included in the reportlab package.  The conversion pipeline is:

    Markdown text
        ↓  markdown.markdown()
    HTML string
        ↓  BeautifulSoup
    parsed element tree
        ↓  _convert_element()
    list of ReportLab flowables
        ↓  SimpleDocTemplate.build()
    PDF file on disk

The MVP prioritises readability over polished branding.  Tables and
complex nested formatting are simplified rather than reproduced exactly.

Public interface
----------------
    generate_pdf(
        audit_id,
        normalized_url,
        markdown_report,
        created_at,
        settings,
    ) -> Path
"""

import logging  # Standard logging — records file creation and any conversion warnings
import os  # os.makedirs ensures the reports/ directory exists before writing
from datetime import datetime  # Type annotation for the created_at parameter
from pathlib import Path  # Path provides OS-agnostic file path handling

import markdown as md_lib  # markdown.markdown() converts Markdown text to an HTML string
from bs4 import BeautifulSoup, Tag  # BeautifulSoup walks the HTML tree; Tag is the element type

from reportlab.lib import colors  # colors.HexColor lets us use brand hex values
from reportlab.lib.enums import TA_CENTER, TA_LEFT  # Text alignment constants
from reportlab.lib.pagesizes import A4  # A4 paper size (210×297 mm)
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # Style base classes
from reportlab.lib.units import mm  # mm converts millimetres to ReportLab's internal points unit
from reportlab.platypus import (
    HRFlowable,       # A horizontal rule — used between major report sections
    ListFlowable,     # A formatted bullet or numbered list container
    ListItem,         # A single item inside a ListFlowable
    Paragraph,        # A block of text with a ParagraphStyle applied
    SimpleDocTemplate,  # The simplest Platypus document builder
    Spacer,           # Empty vertical space between elements
)

from src.config import Settings  # Settings provides the reports directory path

# Module-level logger
logger = logging.getLogger(__name__)  # Resolves to "src.services.pdf_service"

# ---------------------------------------------------------------------------
# Brand colours (shared across styles)
# ---------------------------------------------------------------------------

_NAVY: colors.HexColor = colors.HexColor("#0f3460")   # Brand dark navy — used for headings
_DARK: colors.HexColor = colors.HexColor("#1a1a2e")   # Near-black body text colour
_GREY: colors.HexColor = colors.HexColor("#6b7280")   # Muted grey for secondary text
_RULE: colors.HexColor = colors.HexColor("#dde3ec")   # Light blue-grey for horizontal rules


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def generate_pdf(
    audit_id: str,
    normalized_url: str,
    markdown_report: str,
    created_at: datetime,
    settings: Settings,
) -> Path:
    """
    Convert a Markdown SEO audit report to a PDF file and save it to disk.

    The PDF is saved as ``{settings.reports_dir}/{audit_id}.pdf``.

    Args:
        audit_id: Unique audit identifier — used as the PDF filename.
        normalized_url: The audited website URL — shown in the PDF header.
        markdown_report: The full Markdown text returned by report_service.
        created_at: The audit completion timestamp — shown in the PDF header.
        settings: Application settings providing the reports directory path.

    Returns:
        Path to the generated PDF file.

    Raises:
        OSError: If the file cannot be written to the reports directory.
    """
    logger.info("Generating PDF for audit: %s (%s)", audit_id, normalized_url)

    # Ensure the reports/ directory exists; create it if necessary
    os.makedirs(settings.reports_dir, exist_ok=True)
    # exist_ok=True means no error if the directory already exists

    # Build the output file path: reports/{audit_id}.pdf
    output_path: Path = Path(settings.reports_dir) / f"{audit_id}.pdf"

    # Build the custom paragraph styles used throughout the PDF
    styles = _build_styles()

    # Convert the Markdown report to a list of ReportLab flowables
    flowables = _build_cover_header(normalized_url, created_at, styles)
    # Add the cover header first — always at the top of the document

    flowables.extend(_markdown_to_flowables(markdown_report, styles))
    # Then append all the report sections converted from Markdown

    # Create the PDF document and write all flowables to disk
    doc = SimpleDocTemplate(
        str(output_path),       # ReportLab expects a string path, not a Path object
        pagesize=A4,            # A4 is 210×297 mm — suitable for client-facing reports
        leftMargin=20 * mm,     # 20 mm left margin — readable on screen and in print
        rightMargin=20 * mm,    # 20 mm right margin
        topMargin=20 * mm,      # 20 mm top margin
        bottomMargin=20 * mm,   # 20 mm bottom margin
        title=f"SEO Audit Report — {normalized_url}",  # PDF document metadata title
        author="AI SEO Agent",  # PDF document metadata author
        subject="SEO Audit Report",  # PDF document metadata subject
    )

    doc.build(flowables)
    # doc.build() renders all flowables in order and writes the PDF file to disk

    logger.info(
        "PDF generated successfully: %s (%.1f KB)",
        output_path,
        output_path.stat().st_size / 1024,  # Log file size in kilobytes
    )

    return output_path  # Return the path for the download endpoint


# ---------------------------------------------------------------------------
# Style definitions
# ---------------------------------------------------------------------------

def _build_styles() -> dict[str, ParagraphStyle]:
    """
    Build and return a dictionary of named ParagraphStyle objects.

    All PDF elements use styles from this dictionary so changing the look
    of the entire document only requires editing this function.

    Returns:
        Dict mapping style name strings to ParagraphStyle instances.
    """
    base = getSampleStyleSheet()  # Start from ReportLab's default styles

    return {
        "cover_url": ParagraphStyle(
            name="cover_url",
            parent=base["Normal"],         # Inherit font metrics from Normal
            fontSize=11,                   # Medium size for the URL
            textColor=_GREY,               # Muted grey — secondary information
            alignment=TA_CENTER,           # Centre-aligned on the cover header
            spaceAfter=2 * mm,             # Small gap below the URL
        ),
        "cover_date": ParagraphStyle(
            name="cover_date",
            parent=base["Normal"],
            fontSize=9,                    # Small — date is tertiary information
            textColor=_GREY,
            alignment=TA_CENTER,
            spaceAfter=4 * mm,
        ),
        "h1": ParagraphStyle(
            name="h1",
            parent=base["Normal"],
            fontSize=18,                   # Large heading for top-level report sections
            fontName="Helvetica-Bold",     # Bold weight for section titles
            textColor=_NAVY,               # Brand navy — matches the web UI
            spaceBefore=6 * mm,            # Space above to visually separate sections
            spaceAfter=3 * mm,             # Space below before the section content
        ),
        "h2": ParagraphStyle(
            name="h2",
            parent=base["Normal"],
            fontSize=14,
            fontName="Helvetica-Bold",
            textColor=_NAVY,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
        ),
        "h3": ParagraphStyle(
            name="h3",
            parent=base["Normal"],
            fontSize=12,
            fontName="Helvetica-Bold",
            textColor=_DARK,               # Dark (not navy) for sub-sub-headings
            spaceBefore=3 * mm,
            spaceAfter=1 * mm,
        ),
        "h4": ParagraphStyle(
            name="h4",
            parent=base["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=_DARK,
            spaceBefore=2 * mm,
            spaceAfter=1 * mm,
        ),
        "body": ParagraphStyle(
            name="body",
            parent=base["Normal"],
            fontSize=10,                   # Standard body text size
            textColor=_DARK,
            leading=15,                    # Line height — 15pt gives comfortable reading
            spaceAfter=2 * mm,
        ),
        "bullet": ParagraphStyle(
            name="bullet",
            parent=base["Normal"],
            fontSize=10,
            textColor=_DARK,
            leading=14,
            leftIndent=5 * mm,             # Indent bullet text to align with the bullet symbol
            spaceAfter=1 * mm,
        ),
        "table_header": ParagraphStyle(
            name="table_header",
            parent=base["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=_NAVY,
        ),
        "table_cell": ParagraphStyle(
            name="table_cell",
            parent=base["Normal"],
            fontSize=9,
            textColor=_DARK,
        ),
    }


# ---------------------------------------------------------------------------
# Cover header builder
# ---------------------------------------------------------------------------

def _build_cover_header(
    normalized_url: str,
    created_at: datetime,
    styles: dict[str, ParagraphStyle],
) -> list:
    """
    Build the PDF cover header flowables.

    The header displays the report title, audited URL, and audit date
    at the top of the first page, separated from the body by a rule.

    Args:
        normalized_url: The audited website URL.
        created_at: The audit completion timestamp.
        styles: The style dictionary from _build_styles().

    Returns:
        List of ReportLab flowables for the cover header.
    """
    flowables = []

    # Report title — large centred heading
    flowables.append(Paragraph(
        "SEO Audit Report",  # Fixed title for all reports
        ParagraphStyle(
            name="title",
            fontSize=24,                   # Large display size for the title
            fontName="Helvetica-Bold",
            textColor=_NAVY,
            alignment=TA_CENTER,           # Centred on the page
            spaceAfter=3 * mm,
        ),
    ))

    # Audited URL — shown below the title
    flowables.append(Paragraph(
        _escape_xml(normalized_url),  # Escape any & or < characters in the URL
        styles["cover_url"],
    ))

    # Audit date — formatted as day-month-year
    date_str: str = created_at.strftime("%d %B %Y at %H:%M UTC")
    # strftime formats: %d=day, %B=full month name, %Y=4-digit year, %H:%M=time
    flowables.append(Paragraph(
        f"Generated: {date_str}",
        styles["cover_date"],
    ))

    # Horizontal rule separating the header from the report body
    flowables.append(HRFlowable(
        width="100%",         # Spans the full page width between margins
        thickness=1.5,        # 1.5pt rule — visible but not heavy
        color=_NAVY,          # Brand navy colour to match the title
        spaceAfter=6 * mm,    # Space below the rule before the first section
    ))

    return flowables


# ---------------------------------------------------------------------------
# Markdown → flowable converter
# ---------------------------------------------------------------------------

def _markdown_to_flowables(markdown_text: str, styles: dict[str, ParagraphStyle]) -> list:
    """
    Convert a Markdown string to a list of ReportLab flowables.

    Pipeline:
      1. Convert Markdown to HTML using the `markdown` library.
      2. Parse the HTML with BeautifulSoup.
      3. Walk the top-level child elements and map each to flowables.

    Supported Markdown elements:
      - # h1, ## h2, ### h3, #### h4
      - Paragraphs (plain text, **bold**, *italic*, inline code)
      - Unordered lists (- item)
      - Ordered lists (1. item)
      - Horizontal rules (---)
      - Blockquotes (> text)
      - Tables (simplified: headings only, no complex cell formatting)

    Unsupported elements are converted to plain paragraphs so nothing is lost.

    Args:
        markdown_text: The Markdown-formatted SEO audit report.
        styles: The style dictionary from _build_styles().

    Returns:
        List of ReportLab flowables ready to pass to SimpleDocTemplate.build().
    """
    if not markdown_text or not markdown_text.strip():
        return [Paragraph("No report content.", styles["body"])]
    # Guard against empty input — returns a placeholder paragraph

    # Convert Markdown to HTML using the `markdown` library
    # The 'tables' and 'nl2br' extensions improve table and newline handling
    html: str = md_lib.markdown(
        markdown_text,
        extensions=["tables", "nl2br"],  # tables: HTML table output; nl2br: \n → <br>
    )

    # Parse the HTML with BeautifulSoup for structured element traversal
    soup = BeautifulSoup(html, "lxml")
    # "lxml" is the fast C-based parser installed in requirements.txt

    flowables: list = []

    # Walk the direct children of the <body> (or document root) element
    # BeautifulSoup wraps the HTML fragment differently depending on the input,
    # so we access the children of the first meaningful container
    root = soup.find("body") or soup  # Use <body> if present; otherwise the whole document
    for child in root.children:
        if isinstance(child, Tag):
            flowables.extend(_convert_element(child, styles))
        # Non-Tag nodes (NavigableString whitespace) are skipped

    return flowables if flowables else [Paragraph("No report content.", styles["body"])]


def _convert_element(element: Tag, styles: dict[str, ParagraphStyle]) -> list:
    """
    Convert a single BeautifulSoup Tag to one or more ReportLab flowables.

    Args:
        element: A top-level HTML element from the parsed Markdown output.
        styles: The style dictionary.

    Returns:
        List of zero or more flowables representing this element.
    """
    tag: str = element.name.lower()  # Element tag name e.g. "h1", "p", "ul"

    # --- Headings -----------------------------------------------------------

    if tag == "h1":
        return [Paragraph(_inline_to_html(element), styles["h1"])]

    if tag == "h2":
        return [Paragraph(_inline_to_html(element), styles["h2"])]

    if tag == "h3":
        return [Paragraph(_inline_to_html(element), styles["h3"])]

    if tag in ("h4", "h5", "h6"):
        # Flatten h4–h6 to the h4 style — report unlikely to need deep nesting
        return [Paragraph(_inline_to_html(element), styles["h4"])]

    # --- Paragraphs ---------------------------------------------------------

    if tag == "p":
        text: str = _inline_to_html(element)  # Preserve inline bold/italic
        if not text.strip():
            return []  # Skip empty paragraphs
        return [Paragraph(text, styles["body"])]

    # --- Unordered lists ----------------------------------------------------

    if tag == "ul":
        items = _build_list_items(element, styles, ordered=False)
        if not items:
            return []
        return [ListFlowable(
            items,
            bulletType="bullet",   # Solid bullet symbol •
            leftIndent=10 * mm,    # Indent list from the left margin
            bulletFontSize=8,      # Bullet symbol size
            spaceAfter=2 * mm,
        )]

    # --- Ordered lists ------------------------------------------------------

    if tag == "ol":
        items = _build_list_items(element, styles, ordered=True)
        if not items:
            return []
        return [ListFlowable(
            items,
            bulletType="1",       # Numbered: 1. 2. 3. ...
            leftIndent=10 * mm,
            bulletFontSize=10,
            spaceAfter=2 * mm,
        )]

    # --- Horizontal rules ---------------------------------------------------

    if tag == "hr":
        return [
            Spacer(1, 2 * mm),   # Small space before the rule
            HRFlowable(
                width="100%",
                thickness=0.5,
                color=_RULE,     # Light grey rule for section dividers
            ),
            Spacer(1, 2 * mm),   # Small space after the rule
        ]

    # --- Blockquotes --------------------------------------------------------

    if tag == "blockquote":
        inner_text: str = element.get_text(separator=" ", strip=True)
        if not inner_text:
            return []
        return [Paragraph(
            _escape_xml(inner_text),
            ParagraphStyle(
                name="blockquote",
                parent=styles["body"],
                leftIndent=10 * mm,    # Indent the blockquote
                textColor=_GREY,       # Muted grey for quoted text
                fontSize=10,
                italicAngle=0,
            ),
        )]

    # --- Tables -------------------------------------------------------------

    if tag == "table":
        # Simplified table: rendered as consecutive indented paragraphs
        # Full table rendering requires reportlab.platypus.Table which is complex
        # for the MVP; we fall back to plain text rows instead of crashing
        result = []
        for row in element.find_all("tr"):
            cells = row.find_all(["th", "td"])  # Find all header or data cells
            if cells:
                row_text = " | ".join(
                    _escape_xml(cell.get_text(strip=True))
                    for cell in cells
                )
                style = styles["table_header"] if cells[0].name == "th" else styles["table_cell"]
                result.append(Paragraph(row_text, style))
        return result if result else []

    # --- Fallback -----------------------------------------------------------

    # Any other element (div, span, pre, code blocks, etc.) is rendered as body text
    fallback_text: str = _escape_xml(element.get_text(separator=" ", strip=True))
    if fallback_text:
        return [Paragraph(fallback_text, styles["body"])]

    return []  # No content to render


# ---------------------------------------------------------------------------
# List helper
# ---------------------------------------------------------------------------

def _build_list_items(
    list_element: Tag,
    styles: dict[str, ParagraphStyle],
    ordered: bool,
) -> list[ListItem]:
    """
    Convert the <li> children of a <ul> or <ol> element to ListItem objects.

    Args:
        list_element: The <ul> or <ol> BeautifulSoup Tag.
        styles: The style dictionary.
        ordered: True for numbered lists, False for bullet lists.

    Returns:
        List of ListItem flowables.
    """
    items: list[ListItem] = []

    for li in list_element.find_all("li", recursive=False):
        # recursive=False: only direct <li> children — skips nested lists
        text: str = _inline_to_html(li)
        if not text.strip():
            continue  # Skip blank list items

        items.append(ListItem(
            Paragraph(text, styles["bullet"]),  # Each item is a Paragraph
        ))

    return items


# ---------------------------------------------------------------------------
# Inline HTML helper
# ---------------------------------------------------------------------------

def _inline_to_html(element: Tag) -> str:
    """
    Convert the inner content of an element to an HTML string that
    ReportLab's Paragraph can render.

    ReportLab Paragraph supports a limited HTML subset:
      <b>bold</b>, <i>italic</i>, <u>underline</u>, <br/>, &amp; &lt; &gt;

    This function maps BeautifulSoup child nodes to that subset.

    Args:
        element: A BeautifulSoup Tag whose children are to be serialised.

    Returns:
        HTML string suitable for a ReportLab Paragraph.
    """
    parts: list[str] = []

    for child in element.children:
        if isinstance(child, Tag):
            child_tag: str = child.name.lower()
            child_text: str = _escape_xml(child.get_text())

            if child_tag in ("strong", "b"):
                parts.append(f"<b>{child_text}</b>")

            elif child_tag in ("em", "i"):
                parts.append(f"<i>{child_text}</i>")

            elif child_tag == "code":
                # Inline code — rendered in a slightly smaller font
                parts.append(f"<font size='9' face='Courier'>{child_text}</font>")

            elif child_tag == "a":
                # Links — render as underlined text (ReportLab supports basic link tags)
                href = child.get("href", "")
                parts.append(f"<u>{child_text}</u>")
                # Note: href links are not rendered as clickable in PDF with basic Platypus

            elif child_tag == "br":
                parts.append("<br/>")  # Explicit line break

            else:
                # Any other inline element: render its text content
                parts.append(_escape_xml(child.get_text()))

        else:
            # NavigableString — plain text node
            parts.append(_escape_xml(str(child)))

    return "".join(parts)


# ---------------------------------------------------------------------------
# XML escape helper
# ---------------------------------------------------------------------------

def _escape_xml(text: str) -> str:
    """
    Escape characters that would break ReportLab's XML-based Paragraph rendering.

    ReportLab's Paragraph parser is XML-based and will raise an error if
    raw & < > characters appear in the text content.

    Args:
        text: A plain text string.

    Returns:
        The same text with XML special characters replaced by HTML entities.
    """
    return (
        text
        .replace("&", "&amp;")   # Must be first — otherwise already-escaped entities get double-escaped
        .replace("<", "&lt;")    # Less-than: would be interpreted as an XML tag opening
        .replace(">", "&gt;")    # Greater-than: would be interpreted as an XML tag closing
    )
