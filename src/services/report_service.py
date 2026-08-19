"""
src/services/report_service.py

LLM-backed SEO audit report generation service.

Responsibility: take the structured audit evidence produced by
extractor_service and the guidance context loaded by prompt_loader,
call the configured LLM provider via llm_service.generate_text(), and
return a completed Markdown SEO audit report.

The report_service is the only module that calls the LLM (through the
shared llm_service dispatcher). All other services are deterministic.
Keeping LLM usage isolated here makes it easy to audit token usage,
swap models, and mock the API in tests.

Hallucination prevention
------------------------
The evidence package passed to the LLM is the only factual input.
The system prompt explicitly instructs the LLM to use only verified
evidence and to write ``Could not be verified in this audit.`` for
every field that was not measurable from static content.

Public interface
----------------
    build_audit_context(
        normalized_url,
        site_evidence,
        settings,
    ) -> AuditContext

    generate_report_sections(
        context,
        prompt_context,
        settings,
    ) -> dict[str, str]

    assemble_report_markdown(
        sections,
    ) -> str

    validate_assembled_report(
        markdown_report,
        master_report_structure,
        context,
    ) -> list[str]

    assemble_and_validate_report(
        sections,
        master_report_structure,
        context,
    ) -> AssembledReportResult

    generate_report(
        normalized_url,
        evidence,
        prompt_context,
        settings,
    ) -> ReportResult
"""

import logging  # Standard logging — records every LLM call attempt, success, and failure
import os  # os.makedirs creates the reports/ output directory if it does not exist
import re  # re.finditer extracts PART headings from the template at runtime
import uuid  # uuid.uuid4 generates a unique ID for each audit
from dataclasses import dataclass  # dataclass defines the structured result returned to the caller
from datetime import datetime, timezone  # datetime.now(timezone.utc) for timezone-aware UTC timestamps
from pathlib import Path  # Path handles OS-agnostic file paths for report storage
from urllib.parse import urlparse  # Derives a human-readable page name from a URL's path

from src.config import Settings  # Settings provides the API key and model configuration
from src.services.analysis_service import (  # Deterministic evidence -> score/finding scoring; per-page report notes/rows
    analyze_site,
    build_homepage_element_rows,
    build_page_seo_notes,
    build_priority_page_row,
)
from src.services.audit_models import (
    AuditContext,
    CompetitorGap,
    CompetitorOverview,
    Finding,
    InventorySectionData,
    KeywordOpportunity,
    LocationOpportunity,
    PageReportRow,
    PerformanceEvidence,
    ResearchBundle,
    ResearchClaim,
    ResearchStatus,
    ScoreBreakdown,
    Severity,
    SiteEvidence,
)
from src.services.extractor_service import (
    AuditEvidence,      # Structured verified SEO data extracted from the website
    RobotsTxtEvidence,  # Robots.txt findings — used in evidence formatting
    SitemapEvidence,    # Sitemap accessibility data — used in evidence formatting
)
from src.services.fetch_service import is_transient_status_code  # Shared 429/5xx retry-eligibility rule
from src.services.llm_service import generate_text  # Provider-neutral dispatcher (Gemini/Perplexity/OpenAI)
from src.services.prompt_loader import PromptContext  # Loaded guidance files context
from src.services.report_data_service import (  # Deterministic AuditContext -> section-data projections
    build_inventory_section_data,
    build_on_page_section_data,
    build_technical_section_data,
)
from src.services.research_service import classify_local_business, research_site

# Module-level logger
logger = logging.getLogger(__name__)  # Resolves to "src.services.report_service"

def _extract_required_part_headings(master_report_structure: str) -> tuple[str, ...]:
    """
    Derive required PART/SECTION headings from the live template content.

    Reads the headings that are actually present in MASTER_REPORT_STRUCTURE.md
    so the validator always reflects the current template, regardless of how
    many parts/sections the file contains.
    """
    return tuple(
        m.group(0)
        for m in re.finditer(r"^# (?:PART|SECTION) \d+:", master_report_structure, re.MULTILINE)
    )


# Phrases that must never leak into a client-facing report — see the
# "Originality & Source Integrity" rules in seo_audit.prompt.md.
#
# NOTE: "perplexity"/"chatgpt"/"gemini" are NOT banned as bare words — SECTION 5.2
# (AI Search / GEO Visibility) legitimately discusses these platforms as SEO
# targets. Only self-attribution phrasing (the model naming itself as the
# report's author/tool) is contamination and gets flagged.
_BANNED_PHRASES: tuple[str, ...] = (
    "comet browser",
    "google docs",
    "convert to google docs",
    "generated by perplexity",
    "generated using perplexity",
    "generated with perplexity",
    "created by perplexity",
    "as perplexity",
    "i am perplexity",
    "generated by chatgpt",
    "generated using chatgpt",
    "generated with chatgpt",
    "created by chatgpt",
    "as chatgpt",
    "i am chatgpt",
    "generated by gemini",
    "generated using gemini",
    "generated with gemini",
    "created by gemini",
    "as an ai language model",
)


def _find_banned_phrases(markdown_report: str) -> list[str]:
    """
    Return any contamination/branding phrases found in the generated report.

    Guards against AI-tool branding, prior chat transcripts, or workflow
    instructions leaking into the client-facing report.
    """
    lowered: str = markdown_report.lower()
    return [phrase for phrase in _BANNED_PHRASES if phrase in lowered]


def _extract_section_body(markdown_report: str, heading_prefix: str) -> str | None:
    """
    Return the text following a heading line starting with heading_prefix,
    up to the next Markdown heading (any level), or None if not found.

    Used to inspect the content of a specific subsection (e.g. "## 7.2")
    without depending on its exact trailing wording.
    """
    pattern = re.compile(
        rf"^{re.escape(heading_prefix)}[^\n]*\n([\s\S]*?)(?=^#{{1,6}} |\Z)",
        re.MULTILINE,
    )
    match = pattern.search(markdown_report)
    return match.group(1).strip() if match else None


def _validate_location_section(markdown_report: str) -> list[str]:
    """
    Enforce SECTION 3's conditional rule: exactly one of 3.2 or 3.3 must be
    completed, with the other explicitly marked not applicable.

    Returns a list of human-readable issue descriptions (empty if the
    section is well-formed or SECTION 3 is absent from this template).
    """
    section_32: str | None = _extract_section_body(markdown_report, "## 3.2")
    section_33: str | None = _extract_section_body(markdown_report, "## 3.3")

    if section_32 is None or section_33 is None:
        # Missing headings are already reported by the required-heading check.
        return []

    is_32_not_applicable: bool = "not applicable" in section_32.lower()
    is_33_not_applicable: bool = "not applicable" in section_33.lower()

    if is_32_not_applicable and is_33_not_applicable:
        return ["SECTION 3 sections 3.2 and 3.3 are both marked not applicable — exactly one must be completed"]
    if not is_32_not_applicable and not is_33_not_applicable:
        return ["SECTION 3 sections 3.2 and 3.3 are both completed — exactly one must be marked not applicable"]
    return []


def _find_table_blocks(markdown_report: str) -> list[list[str]]:
    """
    Return each Markdown table in the report as a list of its pipe-delimited
    lines (header row, separator row, and all data rows).
    """
    tables: list[list[str]] = []
    current: list[str] = []

    for line in markdown_report.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            current.append(stripped)
        else:
            if len(current) >= 2:
                tables.append(current)
            current = []
    if len(current) >= 2:
        tables.append(current)

    return tables


def _split_table_row(row: str) -> list[str]:
    """Split a Markdown table row into trimmed cell values, dropping the outer pipes."""
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _deduplicate_table_rows(markdown_report: str) -> str:
    """
    Drop repeated recommendation rows from every Markdown table in the report.

    Recommendation-style tables (e.g. PART 2.1's Issues Table and PART 3's
    on-page tables) can restate the same recommendation for different pages
    across independent section-generation calls. When a table has an "Action"/"Recommendation"/
    "Recommended" column, rows are deduplicated on that column's value alone
    (the actual repeated advice); otherwise the whole row is compared, as a
    general safety net against exact-duplicate rows in any other table.

    When the table also has a per-row identifier column ("Element", "Page", or
    "URL" — e.g. PART 3.1's Homepage Elements Table or PART 3.2's Priority
    Pages Table), the identifier is combined with the recommendation to form
    the dedup key. Otherwise, two distinct elements/pages that happen to share
    a generic recommendation such as "No change needed." would be wrongly
    collapsed into one row, silently dropping real deterministic content.
    """
    dedup_column_names = {"action", "recommendation", "recommended"}
    identifier_column_names = {"element", "page", "url"}
    output_lines: list[str] = []
    table_buffer: list[str] = []

    def flush_table() -> None:
        if len(table_buffer) < 2:
            output_lines.extend(table_buffer)
            table_buffer.clear()
            return
        header, separator, *data_rows = table_buffer
        header_cells = [cell.lower() for cell in _split_table_row(header)]
        dedup_column_index = next(
            (index for index, name in enumerate(header_cells) if name in dedup_column_names),
            None,
        )
        identifier_column_index = next(
            (index for index, name in enumerate(header_cells) if name in identifier_column_names),
            None,
        )

        seen: set[str] = set()
        deduplicated_rows: list[str] = []
        for row in data_rows:
            if dedup_column_index is not None:
                cells = _split_table_row(row)
                if dedup_column_index >= len(cells):
                    deduplicated_rows.append(row)  # malformed row width — not this helper's concern
                    continue
                key = cells[dedup_column_index].strip().lower()
                if identifier_column_index is not None and identifier_column_index < len(cells):
                    key = f"{cells[identifier_column_index].strip().lower()}|{key}"
            else:
                key = row.strip().lower()

            if key in seen:
                continue  # Same recommendation already kept — drop the repeat
            seen.add(key)
            deduplicated_rows.append(row)

        output_lines.append(header)
        output_lines.append(separator)
        output_lines.extend(deduplicated_rows)
        table_buffer.clear()

    for line in markdown_report.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table_buffer.append(line)
        else:
            flush_table()
            output_lines.append(line)
    flush_table()

    return "\n".join(output_lines)


def _validate_citation_columns(markdown_report: str, *, exclude_table_headings: frozenset[str] = frozenset()) -> list[str]:
    """
    Flag data rows in Source/Retrieved-cited tables (PARTS 5-7) that omit a citation.

    Any table whose header includes both a "Source" and "Retrieved" column
    must not contain a data row with an empty value in either column — see
    the External Research Citation Rules in seo_audit.prompt.md.

    Args:
        exclude_table_headings: "### {heading}" table headings to skip validating —
            used only for the per-group pre-injection retry check
            (see generate_report_sections()), where these tables are always
            force-overwritten by a verified renderer regardless of what the
            LLM drafted, so a citation gap there is never a repairable
            narrative issue worth asking the model to fix.
    """
    excluded_tables: set[tuple[str, ...]] = {
        tuple(table)
        for heading in exclude_table_headings
        for table in _find_table_after_heading(markdown_report, heading)
    }

    issues: list[str] = []

    for table in _find_table_blocks(markdown_report):
        if tuple(table) in excluded_tables:
            continue

        header_cells: list[str] = [cell.lower() for cell in _split_table_row(table[0])]
        if "source" not in header_cells or "retrieved" not in header_cells:
            continue

        source_index: int = header_cells.index("source")
        retrieved_index: int = header_cells.index("retrieved")

        # table[1] is the "---|---" separator row; data rows start at index 2.
        for row_number, data_row in enumerate(table[2:], start=1):
            cells: list[str] = _split_table_row(data_row)
            if len(cells) <= max(source_index, retrieved_index):
                continue  # malformed row width — not this validator's concern

            if not cells[source_index] or not cells[retrieved_index]:
                issues.append(
                    f"Row {row_number} of a Source/Retrieved table is missing a citation value"
                )

    return issues


# ---------------------------------------------------------------------------
# Audit context builder — groundwork for the Phase 4 section pipeline
# ---------------------------------------------------------------------------

def _summarize_business(site_evidence: SiteEvidence) -> str:
    """Best-effort one-line business summary from verified homepage evidence, for research prompt context only."""
    homepage = site_evidence.homepage
    parts: list[str] = [part for part in (homepage.page_title, homepage.meta_description) if part]
    return ". ".join(parts)


async def build_audit_context(
    normalized_url: str,
    site_evidence: SiteEvidence,
    settings: Settings,
    audit_id: str | None = None,
) -> AuditContext:
    """
    Assemble the immutable AuditContext for one audit: deterministic
    scoring, local-business classification, and external research are
    each independent stages combined here, once, before any
    section-generation call begins.

    Args:
        normalized_url: The website URL that was audited.
        site_evidence: Verified evidence from crawl_service/extractor_service.
        settings: Application settings (LLM provider and research configuration).
        audit_id: Pre-generated ID to reuse (e.g. from an already-created
            job record); a new one is generated if not supplied.

    Returns:
        AuditContext ready to be passed unchanged to every section call.
    """
    score_breakdown: ScoreBreakdown = analyze_site(site_evidence)
    is_local_business, city_or_region = classify_local_business(site_evidence)
    business_summary: str = _summarize_business(site_evidence)

    research: ResearchBundle = await research_site(
        normalized_url, business_summary, settings, is_local_business, city_or_region,
    )

    context = AuditContext(
        audit_id=audit_id or str(uuid.uuid4()),
        normalized_url=normalized_url,
        site_evidence=site_evidence,
        score_breakdown=score_breakdown,
        research=research,
        is_local_business=is_local_business,
        city_or_region=city_or_region,
        created_at=datetime.now(timezone.utc),
    )
    logger.info(
        "build_audit_context: audit_id=%s url=%s is_local_business=%s overall_score=%.1f",
        context.audit_id, normalized_url, is_local_business, score_breakdown.overall_score,
    )
    return context


# ---------------------------------------------------------------------------
# Section groups and section-scoped evidence formatting — Phase 4 pipeline
# ---------------------------------------------------------------------------
# Fixed, deterministic grouping of MASTER_REPORT_STRUCTURE.md PART/SECTION
# headings into section-generation calls, so no single call has to hold the
# entire site's evidence in context.

_SECTION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("site_inventory", ("PART 1",)),
    ("technical_and_onpage", ("PART 2", "PART 3")),
    ("keyword_strategy", ("SECTION 1",)),
    ("competitor_analysis", ("SECTION 2",)),
    ("location_or_market_expansion", ("SECTION 3",)),
    ("structured_data_and_off_page", ("SECTION 4", "SECTION 5")),
)

# Groups whose ENTIRE template content already has a deterministic Python
# renderer (Steps 6-8) - these skip the LLM call entirely, so the model never
# has any opportunity to invent or rewrite a technical/on-page fact.
_DETERMINISTIC_ONLY_GROUPS: frozenset[str] = frozenset({"technical_and_onpage"})


def _format_findings(findings: list[Finding], empty_message: str) -> str:
    """Render a (pre-filtered) list of Finding(s) as a Markdown list, or a clear empty message if none apply."""
    if not findings:
        return empty_message

    lines: list[str] = []
    for finding in findings:
        lines.append(
            f"- **{finding.title}** (Severity: {finding.severity.value}, Effort: {finding.effort.value})\n"
            f"  - Category: {finding.category}\n"
            f"  - Description: {finding.description}\n"
            f"  - Business impact: {finding.business_impact}\n"
            f"  - Recommendation: {finding.recommendation}\n"
            f"  - Evidence: {', '.join(finding.evidence_urls) if finding.evidence_urls else 'Site-wide'}"
        )
    return "\n".join(lines)


def _format_claims(claims: list[ResearchClaim], empty_message: str) -> str:
    """Render ResearchClaim(s) as a Markdown list with mandatory citations, or a clear empty message."""
    if not claims:
        return empty_message

    lines: list[str] = []
    for claim in claims:
        lines.append(
            f"- **{claim.claim}**: {claim.value} "
            f"(Source: [{claim.source_title}]({claim.source_url}), retrieved {claim.retrieved_date}, "
            f"{claim.confidence})"
        )
    return "\n".join(lines)


def _format_keyword_opportunities(opportunities: list[KeywordOpportunity], empty_message: str) -> str:
    """Render KeywordOpportunity row(s) as a Markdown list with mandatory citations, or a clear empty message."""
    if not opportunities:
        return empty_message

    lines: list[str] = []
    for opportunity in opportunities:
        volume = opportunity.estimated_volume or "No sourced estimate"
        target_page = opportunity.target_page or "No clear existing page match"
        lines.append(
            f"- **{opportunity.keyword}** (Intent: {opportunity.search_intent}, Est. volume: {volume}, "
            f"Target page: {target_page}) "
            f"(Source: [{opportunity.source_title}]({opportunity.source_url}), retrieved {opportunity.retrieved_date})"
        )
    return "\n".join(lines)


def _format_competitor_overview(competitors: list[CompetitorOverview], empty_message: str) -> str:
    """Render CompetitorOverview row(s) as a Markdown list with mandatory citations, or a clear empty message."""
    if not competitors:
        return empty_message

    lines: list[str] = []
    for competitor in competitors:
        authority = competitor.estimated_authority or "No sourced estimate"
        lines.append(
            f"- **{competitor.competitor_name}** ({competitor.website}) - Focus: {competitor.focus}, "
            f"Estimated authority: {authority} "
            f"(Source: [{competitor.source_title}]({competitor.source_url}), retrieved {competitor.retrieved_date})"
        )
    return "\n".join(lines)


def _format_competitor_gaps(gaps: list[CompetitorGap], empty_message: str) -> str:
    """Render CompetitorGap row(s) as a Markdown list with mandatory citations, or a clear empty message."""
    if not gaps:
        return empty_message

    lines: list[str] = []
    for gap in gaps:
        lines.append(
            f"- **{gap.keyword}**: {gap.competitor_position} — Your gap: {gap.your_gap} "
            f"(Source: [{gap.source_title}]({gap.source_url}), retrieved {gap.retrieved_date})"
        )
    return "\n".join(lines)


def _format_location_opportunities(opportunities: list[LocationOpportunity], empty_message: str) -> str:
    """Render LocationOpportunity row(s) as a Markdown list with mandatory citations, or a clear empty message."""
    if not opportunities:
        return empty_message

    lines: list[str] = []
    for opportunity in opportunities:
        volume = opportunity.estimated_volume or "No sourced estimate"
        lines.append(
            f"- **{opportunity.city_or_region}** - {opportunity.primary_keyword} (Est. volume: {volume}, "
            f"Priority: {opportunity.priority}) "
            f"(Source: [{opportunity.source_title}]({opportunity.source_url}), retrieved {opportunity.retrieved_date})"
        )
    return "\n".join(lines)


def _format_performance_evidence(performance: PerformanceEvidence | None) -> str:
    """Render raw Core Web Vitals / PageSpeed evidence for section 2.4, or an honest not-collected message."""
    if performance is None or not performance.is_available:
        return "No Core Web Vitals / PageSpeed data was collected for this audit."

    lines = [f"PageSpeed Insights data collected ({performance.data_source} data, source: {performance.source_url}):"]
    if performance.performance_score is not None:
        lines.append(f"- Performance score: {performance.performance_score:.0f}/100")
    if performance.largest_contentful_paint_ms is not None:
        lines.append(f"- Largest Contentful Paint (LCP): {performance.largest_contentful_paint_ms / 1000:.1f}s")
    if performance.cumulative_layout_shift is not None:
        lines.append(f"- Cumulative Layout Shift (CLS): {performance.cumulative_layout_shift:.2f}")
    if performance.interaction_to_next_paint_ms is not None:
        lines.append(f"- Interaction to Next Paint (INP): {performance.interaction_to_next_paint_ms:.0f}ms")
    return "\n".join(lines)


def _format_site_inventory_evidence(site_evidence: SiteEvidence) -> str:
    """Compact evidence slice for PART 1 (full website audit / URL inventory)."""
    inventory = site_evidence.inventory
    total_urls = inventory.total_url_count if inventory else 0
    sampled_pages = [site_evidence.homepage, *site_evidence.sampled_pages]

    lines: list[str] = [
        f"Base URL: {site_evidence.base_url}",
        f"Final URL after redirects: {site_evidence.final_url}",
        f"Total URLs discovered in sitemap(s): {total_urls}",
        f"Pages sampled and analyzed: {len(sampled_pages)}",
        "",
        "Sampled pages:",
    ]
    for page in sampled_pages:
        lines.append(f"- {page.url} (type: {page.page_type.value}, status: {page.http_status})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic PART 1 inventory table rendering (Step 6 — Phase 3)
# ---------------------------------------------------------------------------
# Pure Markdown renderers built from InventorySectionData (report_data_service.py)
# instead of asking the LLM to author page rows/notes from thin evidence text.
# Wiring these into generate_report_sections() in place of the LLM's own PART 1
# table output is Step 9's job, not this module's rendering functions.


def _derive_page_name(url: str) -> str:
    """Derive a human-readable page name from a URL's path, e.g. /services/hair-transplant/ -> Hair Transplant."""
    path = urlparse(url).path.strip("/")
    if not path:
        return "Homepage"
    slug = path.rsplit("/", 1)[-1]
    words = [word for word in re.split(r"[-_]+", slug) if word]
    return " ".join(word.capitalize() for word in words) if words else "Homepage"


def _escape_table_cell(value: str) -> str:
    """Escape pipe characters so a cell value can never break a Markdown table row."""
    return value.replace("|", "\\|")


def _render_seo_notes_cell(page: PageReportRow) -> str:
    """Render one crawled page's three deterministic SEO notes as the required inline HTML bullet list."""
    notes = build_page_seo_notes(page)
    return "<ul>" + "".join(f"<li>{_escape_table_cell(note)}</li>" for note in notes) + "</ul>"


def _render_page_inventory_table(rows: list[PageReportRow]) -> str:
    """
    Render one "#Index | Page Name | URL | Title Tag | SEO Notes" Markdown
    table for a list of crawled PageReportRow(s).

    Only pass crawled rows (Core Pages or Subpages) — sitemap-only rows must
    never appear here, since they have no verified title or per-page notes to render.
    """
    lines = [
        "| #Index | Page Name (derived from URL) | URL | Title Tag | SEO Notes |",
        "|--------|-------------------------------|-----|-----------|-----------|",
    ]
    for index, page in enumerate(rows, start=1):
        page_name = _derive_page_name(page.url)
        title_cell = _escape_table_cell(page.page_title) if page.page_title else "Missing"
        notes_cell = _render_seo_notes_cell(page)
        lines.append(f"| {index} | {page_name} | {page.url} | {title_cell} | {notes_cell} |")
    return "\n".join(lines)


def render_core_pages_table(inventory: InventorySectionData) -> str:
    """Render PART 1.1's Core Pages Table deterministically from projected rows."""
    return _render_page_inventory_table(inventory.core_pages)


def render_subpages_table(inventory: InventorySectionData) -> str:
    """Render PART 1.2's Subpages Table deterministically from projected rows."""
    return _render_page_inventory_table(inventory.subpages)


# ---------------------------------------------------------------------------
# Deterministic PART 2 factual body rendering (Step 7 — Phase 3)
# ---------------------------------------------------------------------------
# Pure Markdown renderers for PART 2's factual bodies (robots, sitemap, PageSpeed,
# indexability/crawlability, schema, and the Critical/High issues table), built from
# TechnicalSectionData (report_data_service.py). LLM prose may still explain impact,
# but the yes/no status and any technical issue must come from these functions, not
# be decided or invented by the model. Wiring these into generate_report_sections()
# in place of the LLM's own PART 2 output is Step 9's job, not this module's rendering
# functions.


def render_critical_high_issues_table(findings: list[Finding]) -> str:
    """Render PART 2.1's Issues Table directly from Critical/High severity Finding objects."""
    issues = [finding for finding in findings if finding.severity in (Severity.CRITICAL, Severity.HIGH)]
    lines = [
        "| Issue | Severity | Business Impact | Recommendation |",
        "|-------|----------|------------------|-----------------|",
    ]
    if not issues:
        lines.append("| No Critical or High severity issues were found. | — | — | — |")
        return "\n".join(lines)
    for finding in issues:
        lines.append(
            f"| {_escape_table_cell(finding.title)} | {finding.severity.value} | "
            f"{_escape_table_cell(finding.business_impact)} | {_escape_table_cell(finding.recommendation)} |"
        )
    return "\n".join(lines)


def render_robots_txt_section(robots: RobotsTxtEvidence | None) -> str:
    """Render PART 2.2's robots.txt status/directives deterministically."""
    if robots is None:
        return "Robots.txt evidence was not collected for this audit."
    lines = [f"- Accessible: {'Yes' if robots.is_accessible else 'No'} (HTTP {robots.http_status})"]
    if robots.blocks_root_path:
        lines.append("- **Blocks the entire site from crawling** (a `Disallow: /` rule applies to all robots).")
    lines.append(
        f"- Disallow rules: {', '.join(robots.disallow_rules)}" if robots.disallow_rules
        else "- No Disallow rules apply to all robots."
    )
    if robots.allow_rules:
        lines.append(f"- Allow rules: {', '.join(robots.allow_rules)}")
    lines.append(
        f"- Sitemap(s) declared in robots.txt: {', '.join(robots.sitemap_urls)}" if robots.sitemap_urls
        else "- No Sitemap directive was found in robots.txt."
    )
    return "\n".join(lines)


def render_sitemap_section(sitemaps: list[SitemapEvidence]) -> str:
    """Render PART 2.3's accessible sitemap URLs and discovered counts deterministically."""
    if not sitemaps:
        return "No XML sitemap was found or verified for this site."
    lines: list[str] = []
    for sitemap in sitemaps:
        if sitemap.is_accessible:
            lines.append(f"- {sitemap.url}: accessible (HTTP {sitemap.http_status}), {sitemap.url_count} URL(s) listed.")
        else:
            lines.append(f"- {sitemap.url}: not accessible (HTTP {sitemap.http_status}).")
    return "\n".join(lines)


_PERFORMANCE_SOURCE_LABELS = {
    "field": "real-user field data (Chrome UX Report)",
    "lab": "lab-simulated data (a single Lighthouse run)",
}


def render_pagespeed_section(performance: PerformanceEvidence | None) -> str:
    """Render PART 2.4's PageSpeed source and exact available metrics deterministically."""
    if performance is None or not performance.is_available:
        return "PageSpeed Insights data was not available for this audit."

    source_label = _PERFORMANCE_SOURCE_LABELS.get(performance.data_source, performance.data_source or "an unspecified source")
    lines = [f"- Data source: {source_label}, audited URL: {performance.source_url}"]
    metric_lines = []
    if performance.performance_score is not None:
        metric_lines.append(f"- Performance score: {performance.performance_score:.0f}/100")
    if performance.largest_contentful_paint_ms is not None:
        metric_lines.append(f"- Largest Contentful Paint (LCP): {performance.largest_contentful_paint_ms / 1000:.1f}s")
    if performance.cumulative_layout_shift is not None:
        metric_lines.append(f"- Cumulative Layout Shift (CLS): {performance.cumulative_layout_shift:.2f}")
    if performance.interaction_to_next_paint_ms is not None:
        metric_lines.append(f"- Interaction to Next Paint (INP): {performance.interaction_to_next_paint_ms:.0f}ms")
    lines.extend(metric_lines if metric_lines else ["- No individual Core Web Vitals metrics were available."])
    return "\n".join(lines)


def render_indexability_section(pages: list[PageReportRow], robots: RobotsTxtEvidence | None) -> str:
    """Render PART 2.5's indexability/crawlability state deterministically from verified evidence."""
    lines: list[str] = []
    if robots is not None and robots.blocks_root_path:
        lines.append("- **Crawlability: Blocked** — robots.txt disallows all robots from the entire site.")
    else:
        lines.append("- Crawlability: Not blocked at the site level by robots.txt.")

    noindex_pages = [page for page in pages if page.meta_robots and "noindex" in page.meta_robots.lower()]
    if noindex_pages:
        lines.append(f"- Indexability: {len(noindex_pages)} of {len(pages)} analyzed page(s) carry a noindex directive.")
        lines.extend(f"  - {page.url}" for page in noindex_pages)
    else:
        lines.append(f"- Indexability: None of the {len(pages)} analyzed page(s) are blocked by a noindex directive.")
    return "\n".join(lines)


def render_schema_section(schema_types: list[str]) -> str:
    """Render PART 2.6's detected structured data types deterministically."""
    if not schema_types:
        return "No structured data (schema.org) was detected on any analyzed page."
    return "- Detected schema types: " + ", ".join(schema_types)


# ---------------------------------------------------------------------------
# Deterministic PART 3 on-page table rendering (Step 8 — Phase 3)
# ---------------------------------------------------------------------------
# Pure Markdown renderers for PART 3's homepage/priority-page tables and content-
# quality summary, built from OnPageSectionData (report_data_service.py). Values,
# issue labels, and recommendations come only from analysis_service's deterministic
# element checks — never invented by an LLM. Wiring these into
# generate_report_sections() in place of the LLM's own PART 3 output is Step 9's
# job, not this module's rendering functions.


def render_homepage_elements_table(homepage: PageReportRow) -> str:
    """Render PART 3.1's Homepage Elements Table deterministically."""
    lines = [
        "| Element | Current | Issue | Recommended |",
        "|---------|---------|-------|-------------|",
    ]
    for element, current, issue, recommendation in build_homepage_element_rows(homepage):
        lines.append(
            f"| {element} | {_escape_table_cell(current)} | {_escape_table_cell(issue)} | "
            f"{_escape_table_cell(recommendation)} |"
        )
    return "\n".join(lines)


def render_priority_pages_table(priority_pages: list[PageReportRow]) -> str:
    """Render PART 3.2's Priority Pages Table deterministically."""
    lines = [
        "| Page | Title Tag Issue | Meta Description Issue | Heading Issue | Recommendation |",
        "|------|------------------|-------------------------|----------------|-----------------|",
    ]
    if not priority_pages:
        lines.append("| No priority pages were analyzed. | — | — | — | — |")
        return "\n".join(lines)
    for page in priority_pages:
        url, title_issue, description_issue, heading_issue, recommendation = build_priority_page_row(page)
        lines.append(
            f"| {url} | {_escape_table_cell(title_issue)} | {_escape_table_cell(description_issue)} | "
            f"{_escape_table_cell(heading_issue)} | {_escape_table_cell(recommendation)} |"
        )
    return "\n".join(lines)


def render_content_quality_section(content_findings: list[Finding]) -> str:
    """Render PART 3.3's Content Quality Assessment deterministically, with exact counts and affected URLs."""
    if not content_findings:
        return "No content quality issues were found across the analyzed pages."
    lines: list[str] = []
    for finding in content_findings:
        lines.append(f"- **{finding.title}**: {finding.description}")
        lines.append(f"  - Recommendation: {finding.recommendation}")
        affected = ", ".join(finding.evidence_urls) if finding.evidence_urls else "Site-wide"
        lines.append(f"  - Affected URLs: {affected}")
    return "\n".join(lines)


def _render_technical_and_onpage_section(context: AuditContext) -> str:
    """
    Render the entire "technical_and_onpage" group (PART 2 + PART 3) from
    verified evidence, using the Step 6-8 renderers for every subsection.

    Every heading in this group already maps 1:1 to a deterministic renderer,
    so this group is never sent to the LLM (see _DETERMINISTIC_ONLY_GROUPS) -
    the model has no chance to invent a technical/on-page fact or rewrite a
    deterministic table here.
    """
    technical = build_technical_section_data(context)
    on_page = build_on_page_section_data(context)

    return (
        "# PART 2: TECHNICAL SEO AUDIT\n\n"
        "## 2.1 Critical & High Priority Issues\n\n"
        "### Issues Table\n\n"
        f"{render_critical_high_issues_table(technical.findings)}\n\n"
        "## 2.2 Robots.txt Analysis\n\n"
        f"{render_robots_txt_section(technical.robots_txt)}\n\n"
        "## 2.3 XML Sitemap Analysis\n\n"
        f"{render_sitemap_section(technical.sitemaps)}\n\n"
        "## 2.4 Core Web Vitals & Page Speed\n\n"
        f"{render_pagespeed_section(technical.performance)}\n\n"
        "## 2.5 Indexability & Crawlability\n\n"
        f"{render_indexability_section(technical.pages, technical.robots_txt)}\n\n"
        "## 2.6 Structured Data Status\n\n"
        f"{render_schema_section(technical.detected_schema_types)}\n\n"
        "---\n\n"
        "# PART 3: ON-PAGE & CONTENT AUDIT\n\n"
        "## 3.1 Homepage On-Page Review\n\n"
        "### Homepage Elements Table\n\n"
        f"{render_homepage_elements_table(on_page.homepage)}\n\n"
        "## 3.2 Priority Pages On-Page Review\n\n"
        "### Priority Pages Table\n\n"
        f"{render_priority_pages_table(on_page.priority_pages)}\n\n"
        "## 3.3 Content Quality Assessment\n\n"
        f"{render_content_quality_section(on_page.content_findings)}"
    )


def _replace_heading_block(markdown: str, heading_text: str, replacement: str) -> str:
    """
    Force everything under a "### {heading_text}" heading (up to the next
    heading) to `replacement`, regardless of what is already there.

    Appends the heading and `replacement` at the end of `markdown` if the
    heading is missing entirely, so a deterministic table is never silently
    dropped because the model omitted its heading.
    """
    pattern = re.compile(rf"(^### {re.escape(heading_text)}\s*\n)([\s\S]*?)(?=^#{{1,3}} |\Z)", re.MULTILINE)
    if pattern.search(markdown):
        return pattern.sub(lambda m: f"{m.group(1)}{replacement}\n\n", markdown, count=1)
    return f"{markdown.rstrip()}\n\n### {heading_text}\n\n{replacement}\n"


def _inject_inventory_tables(section_markdown: str, core_pages_table: str, subpages_table: str) -> str:
    """
    Force PART 1.1's Core Pages Table and 1.2's Subpages Table to the
    verified, deterministically rendered tables, regardless of what the
    model wrote under those headings - the model is never trusted to author
    or faithfully reproduce these tables.
    """
    section_markdown = _replace_heading_block(section_markdown, "Core Pages Table", core_pages_table)
    return _replace_heading_block(section_markdown, "Subpages Table", subpages_table)


# ---------------------------------------------------------------------------
# Deterministic Section 1-3 opportunity table rendering (Step 14 — Phase 4)
# ---------------------------------------------------------------------------
# Pure renderers for SECTION 1/2/3's citation-bearing opportunity tables,
# built only from accepted (citation-verified) typed research rows - never
# from the LLM's own table authorship. Narrative subsections (1.3 Keyword-to-
# Page Mapping, 3.1 Applicability Assessment, 3.3 Audience & Market Expansion
# narrative) remain genuine LLM judgment and are left untouched; only the
# fixed-column citation tables are overwritten via _replace_heading_block().

# Statuses meaning the research attempt itself did not complete cleanly -
# an empty table here must never read the same as a genuine zero-result search.
_RESEARCH_UNAVAILABLE_STATUSES: frozenset[ResearchStatus] = frozenset(
    {ResearchStatus.PARSE_FAILED, ResearchStatus.CITATION_FAILED, ResearchStatus.PROVIDER_FAILED}
)


def _render_research_status_note(status: ResearchStatus | None) -> str:
    """
    A concise, honest narrative appended below an opportunity table when it
    has no rows - distinguishing a genuine zero-result search (no_results)
    from a research-availability gap (parse/citation/provider failure) so
    neither is ever read as "no market opportunity exists".
    """
    if status == ResearchStatus.NO_RESULTS:
        return (
            "\n\n*Bounded, cited research returned no verified rows for this category "
            "— a genuine zero-result search, not a research failure.*"
        )
    if status in _RESEARCH_UNAVAILABLE_STATUSES:
        return (
            f"\n\n*Research for this category could not be completed ({status.value}). "
            "This reflects a research-availability gap, not an absence of market opportunity.*"
        )
    return ""


def _render_keyword_opportunities_table(opportunities: list[KeywordOpportunity], status: ResearchStatus | None) -> str:
    lines = [
        "| # | Keyword | Search Intent | Est. Monthly Searches | Target Page | Source | Retrieved |",
        "|---|---------|----------------|-------------------------|--------------|--------|-----------|",
    ]
    for index, opportunity in enumerate(opportunities, start=1):
        volume = opportunity.estimated_volume or "No sourced estimate"
        target_page = opportunity.target_page or "No clear existing page match"
        lines.append(
            f"| {index} | {_escape_table_cell(opportunity.keyword)} | {_escape_table_cell(opportunity.search_intent)} | "
            f"{_escape_table_cell(volume)} | {_escape_table_cell(target_page)} | "
            f"[{_escape_table_cell(opportunity.source_title)}]({opportunity.source_url}) | {opportunity.retrieved_date} |"
        )
    return "\n".join(lines) + _render_research_status_note(status)


def render_primary_keywords_table(opportunities: list[KeywordOpportunity], status: ResearchStatus | None) -> str:
    """Render SECTION 1.1's Primary Keywords Table deterministically from accepted, citation-verified rows."""
    return _render_keyword_opportunities_table(opportunities, status)


def render_long_tail_keywords_table(opportunities: list[KeywordOpportunity], status: ResearchStatus | None) -> str:
    """Render SECTION 1.2's Long-Tail Keywords Table deterministically from accepted, citation-verified rows."""
    return _render_keyword_opportunities_table(opportunities, status)


def render_competitor_overview_table(competitors: list[CompetitorOverview], status: ResearchStatus | None) -> str:
    """Render SECTION 2.1's Competitor Overview Table deterministically from accepted, citation-verified rows."""
    lines = [
        "| Competitor | Website | Estimated Authority | Focus | Source | Retrieved |",
        "|------------|---------|----------------------|-------|--------|-----------|",
    ]
    for competitor in competitors:
        authority = competitor.estimated_authority or "No sourced estimate"
        lines.append(
            f"| {_escape_table_cell(competitor.competitor_name)} | {_escape_table_cell(competitor.website)} | "
            f"{_escape_table_cell(authority)} | {_escape_table_cell(competitor.focus)} | "
            f"[{_escape_table_cell(competitor.source_title)}]({competitor.source_url}) | {competitor.retrieved_date} |"
        )
    return "\n".join(lines) + _render_research_status_note(status)


def render_competitor_gap_table(gaps: list[CompetitorGap], status: ResearchStatus | None) -> str:
    """Render SECTION 2.2's Keyword Gap Table deterministically, derived only from already-accepted competitors."""
    lines = [
        "| Keyword | Competitor Position | Your Gap | Source | Retrieved |",
        "|---------|----------------------|----------|--------|-----------|",
    ]
    for gap in gaps:
        lines.append(
            f"| {_escape_table_cell(gap.keyword)} | {_escape_table_cell(gap.competitor_position)} | "
            f"{_escape_table_cell(gap.your_gap)} | [{_escape_table_cell(gap.source_title)}]({gap.source_url}) | "
            f"{gap.retrieved_date} |"
        )
    return "\n".join(lines) + _render_research_status_note(status)


def render_location_opportunity_table(opportunities: list[LocationOpportunity], status: ResearchStatus | None) -> str:
    """Render SECTION 3.2's Location Opportunity Table deterministically from accepted, citation-verified rows."""
    lines = [
        "| City/Region | Primary Keyword | Est. Monthly Searches | Priority | Source | Retrieved |",
        "|-------------|-------------------|-------------------------|----------|--------|-----------|",
    ]
    for opportunity in opportunities:
        volume = opportunity.estimated_volume or "No sourced estimate"
        lines.append(
            f"| {_escape_table_cell(opportunity.city_or_region)} | {_escape_table_cell(opportunity.primary_keyword)} | "
            f"{_escape_table_cell(volume)} | {_escape_table_cell(opportunity.priority)} | "
            f"[{_escape_table_cell(opportunity.source_title)}]({opportunity.source_url}) | {opportunity.retrieved_date} |"
        )
    return "\n".join(lines) + _render_research_status_note(status)


def _inject_keyword_tables(section_markdown: str, primary_table: str, long_tail_table: str) -> str:
    """Force SECTION 1.1/1.2's tables to the verified, deterministically rendered tables."""
    section_markdown = _replace_heading_block(section_markdown, "Primary Keywords Table", primary_table)
    return _replace_heading_block(section_markdown, "Long-Tail Keywords Table", long_tail_table)


def _inject_competitor_tables(section_markdown: str, overview_table: str, gap_table: str) -> str:
    """Force SECTION 2.1/2.2's tables to the verified, deterministically rendered tables."""
    section_markdown = _replace_heading_block(section_markdown, "Competitor Overview Table", overview_table)
    return _replace_heading_block(section_markdown, "Keyword Gap Table", gap_table)


def _inject_location_table(section_markdown: str, location_table: str) -> str:
    """
    Force SECTION 3.2's Location Opportunity Table to the verified,
    deterministically rendered table.

    Only called when the business is local/service-area with a known
    region - i.e. exactly when 3.2 legitimately applies - so this never
    fabricates a table under a "Not Applicable" 3.2 for a non-local
    business or an insufficient-location-evidence case.
    """
    return _replace_heading_block(section_markdown, "Location Opportunity Table", location_table)


def _format_section_evidence(group_name: str, context: AuditContext) -> str:
    """
    Build the compact, section-scoped evidence slice for one section group -
    only the AuditContext data relevant to that group, so no single
    section-generation call has to hold the entire site's evidence.

    Raises:
        ValueError: If group_name is not one of the names in _SECTION_GROUPS.
    """
    findings = context.score_breakdown.findings

    if group_name == "site_inventory":
        return _format_site_inventory_evidence(context.site_evidence)

    if group_name == "technical_and_onpage":
        technical = _format_findings(
            [f for f in findings if f.category == "Technical SEO"],
            "No Technical SEO findings were recorded in this audit.",
        )
        onpage = _format_findings(
            [f for f in findings if f.category in ("On-Page SEO", "Content Quality")],
            "No On-Page or Content Quality findings were recorded in this audit.",
        )
        performance_evidence = _format_performance_evidence(context.site_evidence.performance)
        performance_findings = _format_findings(
            [f for f in findings if f.category == "Performance"],
            "No Performance findings were recorded in this audit.",
        )
        return (
            f"Technical SEO findings:\n{technical}\n\n"
            f"On-Page & Content findings:\n{onpage}\n\n"
            f"Core Web Vitals / Performance evidence (section 2.4):\n{performance_evidence}\n\n"
            f"Performance findings:\n{performance_findings}"
        )

    if group_name == "keyword_strategy":
        primary = _format_keyword_opportunities(
            context.research.primary_keywords,
            "No primary keyword opportunities were found with a citable source.",
        )
        long_tail = _format_keyword_opportunities(
            context.research.long_tail_keywords,
            "No long-tail keyword opportunities were found with a citable source.",
        )
        return f"Primary keyword opportunities:\n{primary}\n\nLong-tail keyword opportunities:\n{long_tail}"

    if group_name == "competitor_analysis":
        competitors = _format_competitor_overview(
            context.research.competitors, "No real competitors were found with a citable source.",
        )
        analysis = _format_competitor_gaps(
            context.research.competitor_analysis,
            "No competitor strengths/gaps were found with a citable source.",
        )
        return f"Competitors:\n{competitors}\n\nCompetitor analysis:\n{analysis}"

    if group_name == "location_or_market_expansion":
        if context.is_local_business and context.city_or_region:
            return (
                f"Business classification: Local/service-area business (region: {context.city_or_region})\n"
                + _format_location_opportunities(
                    context.research.local_demand, "No local demand signals were found with a citable source.",
                )
            )
        if context.is_local_business:
            return (
                "Business classification: Local/service-area business, but no service region could be "
                "determined from crawl evidence (insufficient_location_evidence).\n"
                "No location-specific research was run because the service area is unknown - "
                "state this limitation plainly rather than inventing a region."
            )
        return (
            "Business classification: Not local/service-area\n"
            + _format_claims(
                context.research.audience_expansion,
                "No audience/market expansion opportunities were found with a citable source.",
            )
        )

    if group_name == "structured_data_and_off_page":
        remaining = _format_findings(
            [f for f in findings if f.category in ("Accessibility", "Security")],
            "No Accessibility or Security findings were recorded in this audit.",
        )
        authority = _format_claims(
            context.research.authority_opportunities,
            "No off-page/authority opportunities were found with a citable source.",
        )
        brand_presence = _format_claims(
            context.research.brand_presence,
            "No existing brand presence signals were found with a citable source.",
        )
        return (
            f"Remaining deterministic findings:\n{remaining}\n\n"
            f"Off-page/authority opportunities:\n{authority}\n\n"
            f"Existing brand presence (SEO_RULES Section 5):\n{brand_presence}"
        )

    raise ValueError(f"Unknown section group: {group_name!r}")


def _extract_part_templates(master_report_structure: str, part_headings: tuple[str, ...]) -> str:
    """
    Extract only the named "# PART N:"/"# SECTION N:" template blocks (heading
    through the next top-level PART/SECTION heading or end of file), in the
    given order.

    Keeps one section-generation call's template input limited to the
    parts/sections it is actually responsible for, instead of the entire template.
    """
    blocks: list[str] = []
    for heading_prefix in part_headings:
        pattern = re.compile(
            rf"^(# {re.escape(heading_prefix)}:[^\n]*\n[\s\S]*?)(?=^# (?:PART|SECTION) \d+:|\Z)",
            re.MULTILINE,
        )
        match = pattern.search(master_report_structure)
        if match:
            blocks.append(match.group(1).strip())
    return "\n\n".join(blocks)


def _build_section_user_message(
    normalized_url: str,
    part_headings: tuple[str, ...],
    section_evidence: str,
    master_report_structure: str,
    *,
    deterministic_table_note: str = "",
) -> str:
    """
    Build the user-turn message for one section-generation call.

    Args:
        deterministic_table_note: An optional extra MANDATORY OUTPUT RULES
            line bounding the model away from tables this group already
            renders deterministically (the system overwrites them
            regardless, but this keeps the model from wasting effort
            inventing rows that will never be used).
    """
    template_slice: str = _extract_part_templates(master_report_structure, part_headings)
    heading_list: str = ", ".join(part_headings)
    extra_rule: str = f"- {deterministic_table_note}\n" if deterministic_table_note else ""

    return (
        f"Generate ONLY the following section(s) of the SEO audit report for: {normalized_url}\n\n"
        "## MANDATORY OUTPUT RULES\n"
        f"- Write ONLY {heading_list}. Do not write any other PART.\n"
        "- Reproduce the provided template exactly, in the same order, with the same headings, "
        "sub-headings, and table column names.\n"
        "- Do not add, remove, rename, or reorder sections.\n"
        "- Fill every section/table cell using only the verified evidence and cited research below "
        "— never invent facts.\n"
        "- Do not output any extra wrapper text, commentary, or explanation before or after the section.\n"
        f"{extra_rule}\n"
        "## TEMPLATE TO FILL (VERBATIM STRUCTURE)\n\n"
        f"{template_slice}\n\n"
        "---\n\n"
        "## EVIDENCE FOR THIS SECTION ONLY\n\n"
        f"{section_evidence}"
    )


# Bounds the model away from PART 1.1/1.2's Core Pages/Subpages Tables, which
# are always overwritten by _inject_inventory_tables() after generation
# regardless of what the model writes.
_SITE_INVENTORY_TABLE_NOTE = (
    "The Core Pages Table (1.1) and Subpages Table (1.2) are already finalized from verified crawl data — "
    "leave their table body empty (headers only) rather than inventing rows; the system inserts the "
    "verified tables automatically."
)

# Same purpose as _SITE_INVENTORY_TABLE_NOTE, one per LLM-generating group whose
# citation tables are always force-injected afterward — Step 17 (Phase 5): the
# model is never asked to repair these tables, so it should not waste effort
# drafting rows for them in the first place.
_KEYWORD_STRATEGY_TABLE_NOTE = (
    "The Primary Keywords Table (1.1) and Long-Tail Keywords Table (1.2) are already finalized from "
    "verified, citation-checked research — leave their table body empty (headers only) rather than "
    "inventing rows; the system inserts the verified tables automatically."
)
_COMPETITOR_ANALYSIS_TABLE_NOTE = (
    "The Competitor Overview Table (2.1) and Keyword Gap Table (2.2) are already finalized from "
    "verified, citation-checked research — leave their table body empty (headers only) rather than "
    "inventing rows; the system inserts the verified tables automatically."
)
_LOCATION_TABLE_NOTE = (
    "The Location Opportunity Table (3.2) is already finalized from verified, citation-checked research — "
    "leave its table body empty (headers only) rather than inventing rows; the system inserts the "
    "verified table automatically."
)

# The "### {heading}" table headings each group's tables are always force-injected
# under, regardless of what the LLM drafted — used to exclude those tables from
# the per-group pre-injection citation check (see generate_report_sections()),
# since asking the LLM to repair a citation gap there is never a repairable
# narrative issue: the draft is discarded and overwritten either way.
_DETERMINISTIC_TABLE_HEADINGS_BY_GROUP: dict[str, frozenset[str]] = {
    "site_inventory": frozenset({"Core Pages Table", "Subpages Table"}),
    "keyword_strategy": frozenset({"Primary Keywords Table", "Long-Tail Keywords Table"}),
    "competitor_analysis": frozenset({"Competitor Overview Table", "Keyword Gap Table"}),
    "location_or_market_expansion": frozenset({"Location Opportunity Table"}),
}


# ---------------------------------------------------------------------------
# Deterministic fallback narrative — Step 16 (Phase 5)
# ---------------------------------------------------------------------------
# Used only when a group's LLM narrative still fails validation after one
# retry: rather than keep malformed/contradictory best-effort Markdown, this
# substitutes a plain, always-structurally-valid summary built entirely from
# `context`'s own verified findings/research, reusing the same formatters
# _format_section_evidence() uses to instruct the model in the first place.

_FALLBACK_NARRATIVE_NOTICE = (
    "Automated narrative generation for this section did not pass validation after one retry. "
    "The summary below is generated directly from this audit's verified findings and research."
)


def _build_location_fallback_markdown(context: AuditContext) -> str:
    """Build SECTION 3's fallback, preserving the 3.2/3.3 "exactly one not applicable" rule."""
    research = context.research
    if context.is_local_business and context.city_or_region:
        section_32_body = _format_location_opportunities(
            research.local_demand, "No location-specific research was accepted for this audit.",
        )
        section_33_body = "Not applicable — this business was classified as local with a known service region."
    elif context.is_local_business:
        # Step 13's insufficient_location_evidence case: no region could be determined,
        # so no location research was ever run — 3.2 is the one marked not applicable.
        section_32_body = (
            "Not applicable — no service region could be determined for this local business "
            "(insufficient location evidence)."
        )
        section_33_body = (
            "This business was classified as local, but no service region could be determined from "
            "crawl evidence, so location-specific research was not run."
        )
    else:
        section_32_body = "Not applicable — this business was not classified as a local/service-area business."
        section_33_body = _format_claims(
            research.audience_expansion, "No audience/market expansion opportunities were accepted for this audit.",
        )

    return (
        "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n"
        f"{_FALLBACK_NARRATIVE_NOTICE}\n\n"
        "## 3.1 Applicability Assessment\n\n"
        f"Business classification: {'local/service-area' if context.is_local_business else 'not local/service-area'}"
        f"{f' (region: {context.city_or_region})' if context.city_or_region else ''}.\n\n"
        "## 3.2 Local Location Opportunities\n\n"
        f"{section_32_body}\n\n"
        "## 3.3 Audience & Market Expansion\n\n"
        f"{section_33_body}\n"
    )


def _build_fallback_section_markdown(group_name: str, part_headings: tuple[str, ...], context: AuditContext) -> str:
    """
    Build a safe, always-structurally-valid Markdown block for one section
    group entirely from `context`'s verified findings/research — used only
    once a group's LLM narrative fails validation twice.

    Guarantees the required "# {heading}:" headings and includes no banned
    phrases or citation-less tables, so it never needs its own retry.
    """
    if group_name == "location_or_market_expansion":
        return _build_location_fallback_markdown(context)

    findings = context.score_breakdown.findings
    if group_name == "keyword_strategy":
        body = (
            "Primary keyword opportunities:\n"
            + _format_keyword_opportunities(
                context.research.primary_keywords, "No primary keyword opportunities were accepted for this audit.",
            )
            + "\n\nLong-tail keyword opportunities:\n"
            + _format_keyword_opportunities(
                context.research.long_tail_keywords, "No long-tail keyword opportunities were accepted for this audit.",
            )
        )
    elif group_name == "competitor_analysis":
        body = (
            "Competitors:\n"
            + _format_competitor_overview(
                context.research.competitors, "No competitors were accepted for this audit.",
            )
            + "\n\nCompetitor gaps:\n"
            + _format_competitor_gaps(
                context.research.competitor_analysis, "No competitor gaps were accepted for this audit.",
            )
        )
    else:
        # site_inventory and structured_data_and_off_page: a plain findings summary.
        body = _format_findings(findings, "No additional findings were recorded for this audit.")

    return "\n\n".join(
        f"# {heading}: SUMMARY\n\n{_FALLBACK_NARRATIVE_NOTICE}\n\n{body}" for heading in part_headings
    ) + "\n"


async def generate_report_sections(
    context: AuditContext,
    prompt_context: PromptContext,
    settings: Settings,
) -> dict[str, str]:
    """
    Generate each section group's Markdown independently.

    Each group is validated (required headings, banned phrases, SECTION 3's
    conditional rule, and Source/Retrieved citation columns) and retried at
    most once on failure. A group is stored in the returned dict as soon as
    it succeeds (or exhausts its retry) — one section's failure never
    discards sections already generated (checkpointing).

    Args:
        context: The immutable AuditContext built by build_audit_context().
        prompt_context: Loaded guidance files from prompt_loader.load_prompt_context().
        settings: Application settings providing the LLM provider/API key.

    Returns:
        A dict mapping group_name -> generated Markdown, in _SECTION_GROUPS order.
    """
    audit_prompt_with_url: str = prompt_context.audit_prompt.replace("{{website_url}}", context.normalized_url)
    context_with_url = PromptContext(
        audit_prompt=audit_prompt_with_url,
        seo_skill=prompt_context.seo_skill,
        master_report_structure=prompt_context.master_report_structure,
        ai_guidelines=prompt_context.ai_guidelines,
    )
    system_prompt: str = context_with_url.combined_system_prompt

    sections: dict[str, str] = {}

    for group_name, part_headings in _SECTION_GROUPS:
        if group_name in _DETERMINISTIC_ONLY_GROUPS:
            # Every heading in this group already has a Python renderer (Steps 6-8) —
            # no LLM call is made, so the model has no chance to invent or rewrite a fact.
            section_markdown = _render_technical_and_onpage_section(context)
            sections[group_name] = section_markdown
            logger.info(
                "Section '%s' rendered deterministically for %s (%d chars, no LLM call)",
                group_name, context.normalized_url, len(section_markdown),
            )
            continue

        section_evidence: str = _format_section_evidence(group_name, context)
        if group_name == "site_inventory":
            deterministic_table_note = _SITE_INVENTORY_TABLE_NOTE
        elif group_name == "keyword_strategy":
            deterministic_table_note = _KEYWORD_STRATEGY_TABLE_NOTE
        elif group_name == "competitor_analysis":
            deterministic_table_note = _COMPETITOR_ANALYSIS_TABLE_NOTE
        elif group_name == "location_or_market_expansion" and context.is_local_business and context.city_or_region:
            deterministic_table_note = _LOCATION_TABLE_NOTE
        else:
            deterministic_table_note = ""
        user_message: str = _build_section_user_message(
            context.normalized_url, part_headings, section_evidence, prompt_context.master_report_structure,
            deterministic_table_note=deterministic_table_note,
        )

        section_markdown: str = await generate_text(system_prompt, user_message, settings)

        excluded_table_headings: frozenset[str] = _DETERMINISTIC_TABLE_HEADINGS_BY_GROUP.get(group_name, frozenset())
        required_headings: tuple[str, ...] = tuple(f"# {heading}:" for heading in part_headings)
        missing: list[str] = _missing_required_report_parts(section_markdown, required_headings)
        banned: list[str] = _find_banned_phrases(section_markdown)
        location_issues: list[str] = (
            _validate_location_section(section_markdown) if group_name == "location_or_market_expansion" else []
        )
        citation_issues: list[str] = _validate_citation_columns(
            section_markdown, exclude_table_headings=excluded_table_headings,
        )

        if missing or banned or location_issues or citation_issues:
            logger.warning(
                "Section '%s' for %s failed validation (missing=%s banned=%s location=%s citation=%s); retrying once",
                group_name, context.normalized_url, missing, banned, location_issues, citation_issues,
            )
            retry_message: str = _build_retry_user_message(
                user_message, missing, banned, location_issues, citation_issues,
            )
            section_markdown = await generate_text(system_prompt, retry_message, settings)

            missing = _missing_required_report_parts(section_markdown, required_headings)
            banned = _find_banned_phrases(section_markdown)
            location_issues = (
                _validate_location_section(section_markdown) if group_name == "location_or_market_expansion" else []
            )
            citation_issues = _validate_citation_columns(
                section_markdown, exclude_table_headings=excluded_table_headings,
            )
            if missing or banned or location_issues or citation_issues:
                logger.warning(
                    "Section '%s' for %s still failed validation after retry "
                    "(missing=%s banned=%s location=%s citation=%s); substituting deterministic fallback narrative",
                    group_name, context.normalized_url, missing, banned, location_issues, citation_issues,
                )
                section_markdown = _build_fallback_section_markdown(group_name, part_headings, context)

        if group_name == "site_inventory":
            # Never trust the model's own Core Pages/Subpages Tables — always
            # overwrite them with the deterministically rendered, verified tables.
            inventory = build_inventory_section_data(context)
            section_markdown = _inject_inventory_tables(
                section_markdown,
                render_core_pages_table(inventory),
                render_subpages_table(inventory),
            )
        elif group_name == "keyword_strategy":
            # Never trust the model's own keyword tables — always overwrite them
            # with the deterministically rendered, citation-verified rows.
            section_markdown = _inject_keyword_tables(
                section_markdown,
                render_primary_keywords_table(
                    context.research.primary_keywords, context.research.research_statuses.get("primary_keywords"),
                ),
                render_long_tail_keywords_table(
                    context.research.long_tail_keywords, context.research.research_statuses.get("long_tail_keywords"),
                ),
            )
        elif group_name == "competitor_analysis":
            # Never trust the model's own competitor tables — always overwrite them
            # with the deterministically rendered, citation-verified rows.
            section_markdown = _inject_competitor_tables(
                section_markdown,
                render_competitor_overview_table(
                    context.research.competitors, context.research.research_statuses.get("competitors"),
                ),
                render_competitor_gap_table(
                    context.research.competitor_analysis, context.research.research_statuses.get("competitor_analysis"),
                ),
            )
        elif group_name == "location_or_market_expansion" and context.is_local_business and context.city_or_region:
            # Only applies when SECTION 3.2 legitimately applies (local business with a
            # known region) — never fabricates a table for a non-local business or an
            # insufficient-location-evidence case, where 3.2 must stay "Not Applicable".
            section_markdown = _inject_location_table(
                section_markdown,
                render_location_opportunity_table(
                    context.research.local_demand, context.research.research_statuses.get("local_demand"),
                ),
            )

        sections[group_name] = section_markdown
        logger.info(
            "Section '%s' generated for %s (%d chars)", group_name, context.normalized_url, len(section_markdown),
        )

    return sections


def assemble_report_markdown(sections: dict[str, str]) -> str:
    """
    Combine generate_report_sections()'s per-group Markdown into one final report.

    Groups are joined in _SECTION_GROUPS' declaration order, which is the
    report's final read order.

    Args:
        sections: The dict returned by generate_report_sections().

    Returns:
        The assembled Markdown report, in the template's PART/SECTION order.
    """
    return "\n\n".join(sections[name] for name, _ in _SECTION_GROUPS if name in sections)


def _validate_no_empty_table_cells(markdown_report: str) -> list[str]:
    """
    Flag any Markdown table data row (in any table) that contains a blank cell.

    An empty cell almost always means the LLM skipped a value it should have
    populated from evidence/research, rather than an intentional entry — see
    the "no empty cells" rule in Step 17 of the report pipeline plan.
    """
    issues: list[str] = []
    for table in _find_table_blocks(markdown_report):
        for row_number, data_row in enumerate(table[2:], start=1):
            cells: list[str] = _split_table_row(data_row)
            if any(not cell for cell in cells):
                issues.append(f"Row {row_number} of a table has one or more empty cells")
    return issues


def _validate_table_column_counts(markdown_report: str) -> list[str]:
    """
    Flag any Markdown table data row (in any table) whose cell count differs
    from its own header row.

    Complements `_validate_citation_columns()`, which only checks Source/
    Retrieved citation tables — this catches malformed row widths in every
    table in the report, not just citation tables.
    """
    issues: list[str] = []
    for table in _find_table_blocks(markdown_report):
        header_cell_count: int = len(_split_table_row(table[0]))
        for row_number, data_row in enumerate(table[2:], start=1):
            cell_count: int = len(_split_table_row(data_row))
            if cell_count != header_cell_count:
                issues.append(
                    f"Row {row_number} of a table has {cell_count} cells, "
                    f"but its header row has {header_cell_count}",
                )
    return issues


def _known_source_urls(context: AuditContext) -> set[str]:
    """Every URL this audit actually has evidence for: crawled pages, sitemap entries, and research sources."""
    known: set[str] = {context.site_evidence.base_url, context.site_evidence.final_url}
    known.update(page.url for page in (context.site_evidence.homepage, *context.site_evidence.sampled_pages))
    if context.site_evidence.inventory is not None:
        known.update(entry.url for entry in context.site_evidence.inventory.entries)

    claim_groups: tuple[list[ResearchClaim], ...] = (
        context.research.authority_opportunities,
        context.research.audience_expansion,
    )
    known.update(claim.source_url for claims in claim_groups for claim in claims)
    known.update(
        opportunity.source_url
        for opportunities in (context.research.primary_keywords, context.research.long_tail_keywords)
        for opportunity in opportunities
    )
    known.update(competitor.source_url for competitor in context.research.competitors)
    known.update(competitor.website for competitor in context.research.competitors)
    known.update(gap.source_url for gap in context.research.competitor_analysis)
    known.update(opportunity.source_url for opportunity in context.research.local_demand)
    return known


def _validate_url_provenance(markdown_report: str, context: AuditContext) -> list[str]:
    """
    Flag any http(s) URL inside a report table cell that is not one of the
    site's own crawled/sitemap URLs nor a source_url research_service
    actually returned.

    Guards against the LLM inventing a plausible-looking citation URL
    instead of reusing one it was actually given as evidence.
    """
    known_urls: set[str] = _known_source_urls(context)
    url_pattern = re.compile(r"https?://\S+")
    issues: list[str] = []
    for table in _find_table_blocks(markdown_report):
        for data_row in table[2:]:
            for cell in _split_table_row(data_row):
                for raw_url in url_pattern.findall(cell):
                    url = raw_url.rstrip(").,;")
                    if url not in known_urls:
                        issues.append(f"Table cell cites a URL not found in crawl evidence or research: {url}")
    return issues


# Metrics this MVP's crawler never measures at all — see the "Prohibited
# Claims" rules in AI_REPORT_GUIDELINES.md. A specific number for either can
# only be invented, regardless of what other evidence was collected.
_UNSUPPORTED_METRIC_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brank(?:s|ed|ing)?\D{0,10}#?\d+\D{0,15}(?:for|on)\b", "a specific keyword ranking position"),
    (r"\b\d+\D{0,10}backlinks?\b", "a specific backlink count"),
)

# Core Web Vitals / PageSpeed metrics — only unsupported when this audit's
# PerformanceEvidence (src/services/pagespeed_service.py) was unavailable. When
# real data was collected, the report is expected to cite it, so these patterns
# are skipped (see _validate_no_unsupported_metric_claims).
_UNSUPPORTED_PERFORMANCE_METRIC_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:LCP|INP|CLS)\b\D{0,15}\d", "a specific Core Web Vitals value"),
    (r"\b(?:pagespeed|lighthouse)\D{0,15}\d", "a specific PageSpeed/Lighthouse score"),
)


def _validate_no_unsupported_metric_claims(markdown_report: str, performance_evidence_available: bool) -> list[str]:
    """
    Flag report text stating a specific keyword ranking position or backlink
    count (never measured by this MVP), or a specific Core Web Vitals /
    PageSpeed value when no real PerformanceEvidence was collected for this
    audit — in that case the number was necessarily invented rather than
    derived from evidence.
    """
    issues: list[str] = []
    patterns = _UNSUPPORTED_METRIC_PATTERNS
    if not performance_evidence_available:
        patterns += _UNSUPPORTED_PERFORMANCE_METRIC_PATTERNS

    for pattern, description in patterns:
        if re.search(pattern, markdown_report, re.IGNORECASE):
            issues.append(f"Report appears to state {description}, which this audit does not measure")
    return issues


# ---------------------------------------------------------------------------
# Section-aware evidence validators (Step 15 — Phase 5)
# ---------------------------------------------------------------------------
# These validators check the fully assembled report against `context` itself,
# independent of whichever renderer/injection call produced it. They exist to
# catch a *pipeline* regression (a group never generated, an injection call
# skipped, a retry/dedup/assembly step corrupting a block) — not to repeat
# the renderer unit tests in report_data_service.py/analysis_service.py.


def _find_table_after_heading(markdown_report: str, heading_text: str) -> list[list[str]]:
    """Return the Markdown table(s) found under a "### {heading_text}" heading, or [] if absent."""
    body = _extract_section_body(markdown_report, f"### {heading_text}")
    return _find_table_blocks(body) if body is not None else []


def _validate_inventory_table_coverage(markdown_report: str, context: AuditContext) -> list[str]:
    """
    Every crawled page (homepage + sampled) must appear exactly once across
    the Core Pages Table and Subpages Table combined, with a Title Tag cell
    matching the verified page_title (or "Missing").
    """
    inventory = build_inventory_section_data(context)
    expected_rows: dict[str, PageReportRow] = {row.url: row for row in (*inventory.core_pages, *inventory.subpages)}
    issues: list[str] = []
    seen_urls: list[str] = []

    for heading in ("Core Pages Table", "Subpages Table"):
        for table in _find_table_after_heading(markdown_report, heading):
            header_cells = [cell.lower() for cell in _split_table_row(table[0])]
            if "url" not in header_cells or "title tag" not in header_cells:
                continue
            url_index, title_index = header_cells.index("url"), header_cells.index("title tag")
            for data_row in table[2:]:
                cells = _split_table_row(data_row)
                if len(cells) <= max(url_index, title_index):
                    continue  # malformed row width — not this validator's concern
                url = cells[url_index]
                seen_urls.append(url)
                page = expected_rows.get(url)
                if page is None:
                    continue  # an unknown URL is already flagged by _validate_url_provenance()
                expected_title = _escape_table_cell(page.page_title) if page.page_title else "Missing"
                if cells[title_index] != expected_title:
                    issues.append(
                        f"Inventory table's Title Tag cell for {url} is \"{cells[title_index]}\", "
                        f"but verified evidence has \"{expected_title}\""
                    )

    for url in expected_rows:
        occurrences = seen_urls.count(url)
        if occurrences == 0:
            issues.append(f"Crawled page {url} is missing from both the Core Pages Table and Subpages Table")
        elif occurrences > 1:
            issues.append(f"Crawled page {url} appears {occurrences} times across the inventory tables (expected exactly once)")

    return issues


def _validate_seo_notes_cell_counts(markdown_report: str) -> list[str]:
    """Every SEO Notes cell in the Core Pages/Subpages tables must contain exactly three `<li>` items."""
    issues: list[str] = []
    for heading in ("Core Pages Table", "Subpages Table"):
        for table in _find_table_after_heading(markdown_report, heading):
            header_cells = [cell.lower() for cell in _split_table_row(table[0])]
            if "seo notes" not in header_cells:
                continue
            notes_index = header_cells.index("seo notes")
            for row_number, data_row in enumerate(table[2:], start=1):
                cells = _split_table_row(data_row)
                if notes_index >= len(cells):
                    continue  # malformed row width — not this validator's concern
                li_count = cells[notes_index].count("<li>")
                if li_count != 3:
                    issues.append(f"Row {row_number} of the {heading} has {li_count} SEO Notes <li> item(s), expected exactly 3")
    return issues


# Phrases that hedge a transient HTTP status observation — their presence means the
# report is not stating the status as a settled, confirmed fact.
_HTTP_CLAIM_HEDGE_TERMS: tuple[str, ...] = (
    "unconfirmed", "single", "transient", "observed once", "verify", "re-check",
    "recheck", "may ", "possibly", "temporary", "intermittent",
)


def _validate_no_unconfirmed_http_claims(markdown_report: str, context: AuditContext) -> list[str]:
    """
    Flag report text stating a 429/5xx status as settled fact for a page
    where that status was only ever observed once and never confirmed by a
    retry (see fetch_service.is_transient_status_code()/PageEvidence.attempt_count).
    """
    technical = build_technical_section_data(context)
    unconfirmed_codes = {
        page.http_status
        for page in technical.pages
        if page.http_status is not None and is_transient_status_code(page.http_status) and page.attempt_count <= 1
    }
    if not unconfirmed_codes:
        return []

    issues: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", markdown_report)
    for status_code in unconfirmed_codes:
        code_pattern = re.compile(rf"\b{status_code}\b")
        for sentence in sentences:
            if code_pattern.search(sentence) and not any(term in sentence.lower() for term in _HTTP_CLAIM_HEDGE_TERMS):
                issues.append(
                    f"Report states HTTP {status_code} without hedging, but this status was only "
                    "observed once and never confirmed by a retry"
                )
                break  # one flagged sentence per status code is enough to report the issue
    return issues


def _validate_deterministic_blocks_present(markdown_report: str, context: AuditContext) -> list[str]:
    """
    Confirm every deterministic block this pipeline renders — inventory
    tables, PART 2/3's technical/on-page facts, and SECTION 1-3's opportunity
    tables — is present in the assembled report exactly as this audit's
    evidence and accepted research rows require.

    Re-renders each block from `context` using the same pure renderers the
    pipeline itself calls and requires the exact output to appear verbatim in
    the final Markdown. A mismatch here means the pipeline failed to inject a
    block, or a later step (retry, dedup, assembly) corrupted it — it also
    means a deterministic table silently lost its rows, or a research table's
    rows no longer exactly match the accepted typed research results.
    """
    inventory = build_inventory_section_data(context)
    technical = build_technical_section_data(context)
    on_page = build_on_page_section_data(context)
    research = context.research

    expected_blocks: dict[str, str] = {
        "Core Pages Table": render_core_pages_table(inventory),
        "Subpages Table": render_subpages_table(inventory),
        "Issues Table": render_critical_high_issues_table(technical.findings),
        "robots.txt section": render_robots_txt_section(technical.robots_txt),
        "sitemap section": render_sitemap_section(technical.sitemaps),
        "PageSpeed section": render_pagespeed_section(technical.performance),
        "indexability section": render_indexability_section(technical.pages, technical.robots_txt),
        "schema section": render_schema_section(technical.detected_schema_types),
        "Homepage Elements Table": render_homepage_elements_table(on_page.homepage),
        "Priority Pages Table": render_priority_pages_table(on_page.priority_pages),
        "Content Quality section": render_content_quality_section(on_page.content_findings),
        "Primary Keywords Table": render_primary_keywords_table(
            research.primary_keywords, research.research_statuses.get("primary_keywords"),
        ),
        "Long-Tail Keywords Table": render_long_tail_keywords_table(
            research.long_tail_keywords, research.research_statuses.get("long_tail_keywords"),
        ),
        "Competitor Overview Table": render_competitor_overview_table(
            research.competitors, research.research_statuses.get("competitors"),
        ),
        "Keyword Gap Table": render_competitor_gap_table(
            research.competitor_analysis, research.research_statuses.get("competitor_analysis"),
        ),
    }
    if context.is_local_business and context.city_or_region:
        expected_blocks["Location Opportunity Table"] = render_location_opportunity_table(
            research.local_demand, research.research_statuses.get("local_demand"),
        )

    return [
        f"Report is missing the expected deterministic {label} content"
        for label, expected in expected_blocks.items()
        if expected not in markdown_report
    ]


# Removed in the Phase 1 template reduction (docs/ADR/ADR-001-MVP-Architecture.md) — must never reappear.
_REMOVED_SECTION_HEADINGS: tuple[str, ...] = ("# SECTION 6:", "# SECTION 7:", "# SECTION 8:")


def _validate_removed_sections_absent(markdown_report: str) -> list[str]:
    """Flag any reappearance of a Section 6-8 heading removed from the canonical template."""
    return [f"Report contains a removed heading: {heading}" for heading in _REMOVED_SECTION_HEADINGS if heading in markdown_report]


class ReportIntegrityError(RuntimeError):
    """
    Raised when a deterministic report block — content rendered purely from
    AuditContext by this pipeline's own renderers, never touched by the LLM
    — fails validation after assembly.

    This can only be caused by a pipeline bug (a missed injection call, a
    dedup step corrupting a table, evidence wired to the wrong renderer),
    never by LLM narrative quality, so it is raised instead of tolerated as
    a "best effort" checkpoint issue.
    """


def _validate_deterministic_integrity(markdown_report: str, context: AuditContext) -> list[str]:
    """
    Run only the checks whose target content is produced entirely by this
    pipeline's own pure renderers and force-injected regardless of what the
    LLM wrote (Core/Subpages inventory tables and every block
    _validate_deterministic_blocks_present() re-renders from context).

    A non-empty result here is always a programming error, never an LLM
    content-quality issue — see ReportIntegrityError.
    """
    issues: list[str] = []
    issues.extend(_validate_inventory_table_coverage(markdown_report, context))
    issues.extend(_validate_seo_notes_cell_counts(markdown_report))
    issues.extend(_validate_deterministic_blocks_present(markdown_report, context))
    return issues


def validate_assembled_report(markdown_report: str, master_report_structure: str, context: AuditContext) -> list[str]:
    """
    Run every report-level validation rule against one assembled Markdown report.

    This is the single entry point called after assemble_report_markdown().
    It only reports issues — it does not retry or repair anything itself,
    since per-section retries already happened in generate_report_sections().

    Note: this includes the deterministic-integrity checks (see
    _validate_deterministic_integrity()) for completeness/inspection, but
    assemble_and_validate_report() checks those separately first and raises
    ReportIntegrityError before ever calling this function if they fail —
    so by the time this runs in the normal pipeline, they are guaranteed
    empty. This function's own copy of those checks stays useful for direct
    callers/tests that want the full issue list without raising.

    Args:
        markdown_report: The full assembled report from assemble_report_markdown().
        master_report_structure: The live template, used to derive required PART headings.
        context: The AuditContext used to generate this report, for URL provenance checks.

    Returns:
        A flat list of human-readable issue descriptions, empty if the report is well-formed.
    """
    required_headings: tuple[str, ...] = _extract_required_part_headings(master_report_structure)
    issues: list[str] = []

    missing_parts: list[str] = _missing_required_report_parts(markdown_report, required_headings)
    if missing_parts:
        issues.append(f"Missing required PART headings: {', '.join(missing_parts)}")

    banned_phrases: list[str] = _find_banned_phrases(markdown_report)
    if banned_phrases:
        issues.append(f"Contains banned contamination phrases: {', '.join(banned_phrases)}")

    issues.extend(_validate_location_section(markdown_report))
    issues.extend(_validate_citation_columns(markdown_report))
    issues.extend(_validate_no_empty_table_cells(markdown_report))
    issues.extend(_validate_table_column_counts(markdown_report))
    issues.extend(_validate_url_provenance(markdown_report, context))
    performance = context.site_evidence.performance
    performance_evidence_available = performance is not None and performance.is_available
    issues.extend(_validate_no_unsupported_metric_claims(markdown_report, performance_evidence_available))
    issues.extend(_validate_deterministic_integrity(markdown_report, context))
    issues.extend(_validate_no_unconfirmed_http_claims(markdown_report, context))
    issues.extend(_validate_removed_sections_absent(markdown_report))
    return issues


@dataclass
class AssembledReportResult:
    """The outcome of assembling and validating one report: the Markdown plus a pass/fail verdict."""

    markdown_report: str
    # The full assembled Markdown report from assemble_report_markdown()

    issues: list[str]
    # Every issue found by validate_assembled_report() (empty = well-formed)

    is_valid: bool
    # True only if issues is empty — the checkpoint/fail signal callers act on


def assemble_and_validate_report(
    sections: dict[str, str],
    master_report_structure: str,
    context: AuditContext,
) -> AssembledReportResult:
    """
    Assemble generate_report_sections()'s output into one report and validate it in a single call.

    Callers get back the assembled Markdown (kept even if it has narrative
    issues, since per-section retries/fallback already happened in
    generate_report_sections()) together with a definitive is_valid verdict
    and the specific issues found, instead of calling
    assemble_report_markdown() and validate_assembled_report() separately
    and interpreting an empty issues list themselves.

    Raises:
        ReportIntegrityError: If any deterministic block (content this
            pipeline force-injects from AuditContext, never touched by the
            LLM) failed to survive assembly intact — always a pipeline bug,
            never an LLM content-quality issue, so it is never returned as
            a soft "best effort" checkpoint issue.

    Args:
        sections: The dict returned by generate_report_sections().
        master_report_structure: The live template, used to derive required PART headings.
        context: The AuditContext used to generate this report.

    Returns:
        AssembledReportResult with the assembled Markdown, any issues found, and is_valid.
    """
    markdown_report: str = _deduplicate_table_rows(assemble_report_markdown(sections))

    integrity_issues: list[str] = _validate_deterministic_integrity(markdown_report, context)
    if integrity_issues:
        raise ReportIntegrityError(
            f"Assembled report for audit {context.audit_id} failed deterministic integrity validation: "
            + "; ".join(integrity_issues)
        )

    issues: list[str] = validate_assembled_report(markdown_report, master_report_structure, context)
    return AssembledReportResult(markdown_report=markdown_report, issues=issues, is_valid=not issues)


# ---------------------------------------------------------------------------
# Result data model
# ---------------------------------------------------------------------------

@dataclass
class ReportResult:
    """
    The completed SEO audit report.

    Returned by generate_report() and consumed by the API route, which
    stores it in the reports/ folder and returns it to the UI.
    """

    audit_id: str
    # Unique identifier for this audit — used as the filename and PDF download key

    normalized_url: str
    # The URL that was audited — included in the report for reference

    markdown_report: str
    # The full Markdown-formatted SEO audit report generated by the LLM

    created_at: datetime
    # UTC timestamp when the report generation completed


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

async def generate_report(
    normalized_url: str,
    evidence: AuditEvidence,
    prompt_context: PromptContext,
    settings: Settings,
    audit_id: str | None = None,
) -> ReportResult:
    """
    Generate a Markdown SEO audit report using the configured LLM provider.

    Workflow:
      1. Format the verified evidence as a structured text block.
      2. Substitute the website URL into the audit prompt template.
      3. Build the LLM request (system prompt + user message).
      4. Call the configured provider asynchronously (non-blocking) via generate_text().
      5. Return the report with a unique audit ID and timestamp.

    Args:
        normalized_url: The website URL that was audited.
        evidence: Verified SEO data from extractor_service.extract().
        prompt_context: Loaded guidance files from prompt_loader.load_prompt_context().
        settings: Application settings providing the LLM provider, API key, and model name.
        audit_id: Pre-generated ID to reuse (e.g. from an already-created
            job record); a new one is generated if not supplied.

    Returns:
        ReportResult containing the Markdown report, audit ID, and metadata.

    Raises:
        ValueError: If the configured provider's API key is not set (raised by generate_text()).
        LLMProviderError: If the LLM fails to return a usable response.
    """
    logger.info("Starting report generation for: %s", normalized_url)

    # --- Step 1: Format the audit evidence as structured text ---------------

    evidence_text: str = _format_evidence(normalized_url, evidence)
    # Converts the AuditEvidence dataclass into a readable Markdown-formatted block
    # This is the "user message" that tells the LLM what was found on the website

    # --- Step 2: Substitute URL into the audit prompt -----------------------

    audit_prompt_with_url: str = prompt_context.audit_prompt.replace(
        "{{website_url}}",  # The template placeholder defined in seo_audit.prompt.md
        normalized_url,      # The actual URL being audited
    )
    # The audit prompt now contains the real URL where the placeholder was

    # Build the updated PromptContext with the URL-substituted audit prompt
    # This preserves the other guidance files unchanged
    context_with_url = PromptContext(
        audit_prompt=audit_prompt_with_url,
        seo_skill=prompt_context.seo_skill,
        master_report_structure=prompt_context.master_report_structure,
        ai_guidelines=prompt_context.ai_guidelines,
    )

    # --- Step 3: Build LLM request ------------------------------------------

    system_prompt: str = context_with_url.combined_system_prompt
    # The combined system prompt includes all four guidance files assembled in priority order:
    # AI guidelines → SEO methodology → Report spec → Audit prompt (with real URL)

    user_message: str = _build_user_message(
        normalized_url=normalized_url,
        evidence_text=evidence_text,
        master_report_structure=prompt_context.master_report_structure,
    )
    # The user message presents the verified evidence and asks for the report

    logger.debug(
        "LLM request prepared: system=%d chars, user=%d chars",
        len(system_prompt),
        len(user_message),
    )

    # --- Step 4: Call the configured LLM provider --------------------------

    markdown_report: str = await generate_text(system_prompt, user_message, settings)

    # Derive required headings from the template that was actually used for this run.
    # This reflects any parts the user has added or removed from MASTER_REPORT_STRUCTURE.md.
    required_headings: tuple[str, ...] = _extract_required_part_headings(
        prompt_context.master_report_structure
    )

    # Log structural drift if the model omits required report parts.
    # This keeps the request successful while making inconsistency observable.
    missing_parts: list[str] = _missing_required_report_parts(markdown_report, required_headings)
    if missing_parts:
        logger.warning(
            "Generated report for %s is missing required sections: %s",
            normalized_url,
            ", ".join(missing_parts),
        )

    # Detect provider branding/chat-transcript contamination (see Originality &
    # Source Integrity rules). This is checked alongside missing-part drift so
    # a single retry can address both issues in one extra call.
    banned_phrases: list[str] = _find_banned_phrases(markdown_report)
    if banned_phrases:
        logger.warning(
            "Generated report for %s contains banned contamination phrases: %s",
            normalized_url,
            ", ".join(banned_phrases),
        )

    # Validate SECTION 3's conditional location/market-expansion rule.
    location_issues: list[str] = _validate_location_section(markdown_report)
    if location_issues:
        logger.warning(
            "Generated report for %s has SECTION 3 conditional issues: %s",
            normalized_url,
            "; ".join(location_issues),
        )

    # Validate that every Source/Retrieved-cited table (SECTION 1-3) has no
    # rows with an empty citation column.
    citation_issues: list[str] = _validate_citation_columns(markdown_report)
    if citation_issues:
        logger.warning(
            "Generated report for %s has citation issues: %s",
            normalized_url,
            "; ".join(citation_issues),
        )

    if missing_parts or banned_phrases or location_issues or citation_issues:
        # One retry only when the model produced a partially structured report.
        # If all required headings are missing, a retry often repeats the same failure.
        all_headings_missing: bool = bool(missing_parts) and len(missing_parts) == len(required_headings)
        if not all_headings_missing:
            retry_user_message: str = _build_retry_user_message(
                user_message, missing_parts, banned_phrases, location_issues, citation_issues
            )
            markdown_report = await generate_text(system_prompt, retry_user_message, settings)

            missing_parts = _missing_required_report_parts(markdown_report, required_headings)
            banned_phrases = _find_banned_phrases(markdown_report)
            location_issues = _validate_location_section(markdown_report)
            citation_issues = _validate_citation_columns(markdown_report)
            if missing_parts:
                logger.warning(
                    "Retry report for %s is still missing required sections: %s",
                    normalized_url,
                    ", ".join(missing_parts),
                )
            if banned_phrases:
                logger.warning(
                    "Retry report for %s still contains banned contamination phrases: %s",
                    normalized_url,
                    ", ".join(banned_phrases),
                )
            if location_issues:
                logger.warning(
                    "Retry report for %s still has SECTION 3 conditional issues: %s",
                    normalized_url,
                    "; ".join(location_issues),
                )
            if citation_issues:
                logger.warning(
                    "Retry report for %s still has citation issues: %s",
                    normalized_url,
                    "; ".join(citation_issues),
                )
            if not missing_parts and not banned_phrases and not location_issues and not citation_issues:
                logger.info(
                    "Retry report for %s now includes all required PART sections with no contamination",
                    normalized_url,
                )
        else:
            logger.warning(
                "Retry skipped for %s because all required PART headings were missing in first response",
                normalized_url,
            )

    # --- Step 6: Assemble and return the result ----------------------------

    resolved_audit_id: str = audit_id or str(uuid.uuid4())
    # Only generate a fresh ID if the caller didn't already supply one

    created_at: datetime = datetime.now(timezone.utc)
    # Record the UTC completion time using a timezone-aware datetime (avoids DeprecationWarning)

    logger.info(
        "Report generated for %s: audit_id=%s, length=%d chars",
        normalized_url,
        resolved_audit_id,
        len(markdown_report),
    )

    return ReportResult(
        audit_id=resolved_audit_id,
        normalized_url=normalized_url,
        markdown_report=markdown_report,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Evidence formatting helpers
# ---------------------------------------------------------------------------

def _format_evidence(normalized_url: str, evidence: AuditEvidence) -> str:
    """
    Convert an AuditEvidence dataclass into a structured Markdown text block.

    This text is passed to the LLM as the factual input for the report.
    The LLM must use only this data — it must not invent additional findings.

    Args:
        normalized_url: The URL that was audited.
        evidence: The structured evidence from extractor_service.

    Returns:
        A Markdown-formatted string describing all verified findings.
    """
    lines: list[str] = []  # Accumulate text lines before joining

    lines.append(f"## Verified Audit Evidence for {normalized_url}")
    lines.append("")  # Blank line for readability

    # --- Homepage status ----------------------------------------------------

    lines.append("### Homepage")
    lines.append(f"- Final URL: {evidence.final_url or normalized_url}")
    lines.append(f"- HTTP Status: {evidence.http_status}")
    lines.append(f"- HTTPS: {'Yes' if evidence.is_https else 'No'}")
    lines.append("")

    # --- Page metadata -------------------------------------------------------

    lines.append("### Page Metadata")

    if evidence.page_title:
        lines.append(f"- Title: \"{evidence.page_title}\"")
        lines.append(f"- Title Length: {evidence.page_title_length} characters")
    else:
        lines.append("- Title: **Missing** — no <title> element found")
        lines.append("- Title Length: 0 characters")

    if evidence.meta_description:
        lines.append(f"- Meta Description: \"{evidence.meta_description}\"")
        lines.append(f"- Meta Description Length: {evidence.meta_description_length} characters")
    else:
        lines.append("- Meta Description: **Missing** — no <meta name=\"description\"> element found")
        lines.append("- Meta Description Length: 0 characters")

    if evidence.canonical_url:
        lines.append(f"- Canonical URL: {evidence.canonical_url}")
    else:
        lines.append("- Canonical URL: Not found in static HTML")

    if evidence.page_language:
        lines.append(f"- Page Language: {evidence.page_language}")
    else:
        lines.append("- Page Language: Not declared on <html> element")

    lines.append("")

    # --- Heading structure --------------------------------------------------

    lines.append("### Heading Structure")
    lines.append(f"- H1 Tags Found: {len(evidence.h1_tags)}")

    if evidence.h1_tags:
        for h1 in evidence.h1_tags:
            lines.append(f"  - \"{h1}\"")
    else:
        lines.append("  - **No H1 tags found**")

    lines.append(f"- H2 Tags Found: {len(evidence.h2_tags)}")

    if evidence.h2_tags[:5]:
        # Show only the first 5 H2s to keep the prompt concise
        for h2 in evidence.h2_tags[:5]:
            lines.append(f"  - \"{h2}\"")
        if len(evidence.h2_tags) > 5:
            lines.append(f"  - ... and {len(evidence.h2_tags) - 5} more H2 tags")

    lines.append("")

    # --- Links ---------------------------------------------------------------

    lines.append("### Links")
    lines.append(f"- Internal Links: {len(evidence.internal_links)} unique URLs found")
    lines.append(f"- External Links: {len(evidence.external_links)} unique URLs found")
    lines.append("")

    # --- Images --------------------------------------------------------------

    lines.append("### Images")
    lines.append(f"- Total Images: {len(evidence.images)}")
    lines.append(f"- Images Missing ALT Attribute: {evidence.images_missing_alt_count}")
    lines.append(f"- Images With Empty ALT (alt=\"\"): {evidence.images_empty_alt_count}")
    lines.append("")

    # --- robots.txt ----------------------------------------------------------

    lines.append("### robots.txt")

    if evidence.robots_txt is None:
        lines.append("- robots.txt: Not fetched")
    elif not evidence.robots_txt.is_accessible:
        lines.append(f"- robots.txt: Not accessible (HTTP {evidence.robots_txt.http_status})")
        lines.append("- Disallow Rules: Could not be verified in this audit.")
    else:
        lines.append(f"- robots.txt: Accessible (HTTP {evidence.robots_txt.http_status})")
        lines.append(f"- Blocks Root Path (/): {'**Yes — all robots blocked**' if evidence.robots_txt.blocks_root_path else 'No'}")

        if evidence.robots_txt.disallow_rules:
            lines.append(f"- Disallow Rules ({len(evidence.robots_txt.disallow_rules)} found):")
            for rule in evidence.robots_txt.disallow_rules[:10]:
                # Limit to 10 rules to keep the prompt a reasonable length
                lines.append(f"  - Disallow: {rule}")
            if len(evidence.robots_txt.disallow_rules) > 10:
                lines.append(f"  - ... and {len(evidence.robots_txt.disallow_rules) - 10} more rules")
        else:
            lines.append("- Disallow Rules: None (all paths accessible to robots)")

        if evidence.robots_txt.sitemap_urls:
            lines.append(f"- Sitemap URLs in robots.txt ({len(evidence.robots_txt.sitemap_urls)}):")
            for url in evidence.robots_txt.sitemap_urls:
                lines.append(f"  - {url}")
        else:
            lines.append("- Sitemap URLs in robots.txt: None declared")

    lines.append("")

    # --- Sitemaps ------------------------------------------------------------

    lines.append("### Sitemaps")

    if not evidence.sitemaps:
        lines.append("- No sitemaps were fetched.")
    else:
        for sitemap in evidence.sitemaps:
            status_text = (
                f"Accessible (HTTP {sitemap.http_status}, {sitemap.url_count} URLs)"
                if sitemap.is_accessible
                else f"Not accessible (HTTP {sitemap.http_status})"
            )
            lines.append(f"- {sitemap.url}: {status_text}")
            # Include discovered URLs so the LLM can populate page-inventory tables
            for u in sitemap.urls:
                lines.append(f"  - {u}")

    lines.append("")

    # --- Unverifiable fields -------------------------------------------------

    lines.append("### Technical Metrics That Could Not Be Verified")
    lines.append(
        "The following specific technical metrics CANNOT be measured from static content. "
        "For each metric listed below ONLY, write: \"Could not be verified in this audit.\" "
        "Do NOT use this phrase in SEO Notes, Slug-Based Topic, Current Status, or any "
        "recommendation column — those must always contain URL-based SEO strategy."
    )
    lines.append("")

    for field_name in evidence.unverifiable_fields:
        lines.append(f"- {field_name}")

    return "\n".join(lines)
    # Join all lines into a single string for the LLM user message


def _build_user_message(
    normalized_url: str,
    evidence_text: str,
    master_report_structure: str | None = None,
) -> str:
    """
    Build the user-turn message for the LLM conversation.

    The user message combines the task instruction and the evidence.
    Keeping the task instruction here (rather than only in the system prompt)
    reinforces the request in case the system instruction is truncated.

    Args:
        normalized_url: The website URL being audited.
        evidence_text: The formatted evidence text from _format_evidence().
        master_report_structure: Optional full report template text that must
                                 be reproduced verbatim by the LLM.

    Returns:
        A complete user message string ready to send to the LLM.
    """
    if master_report_structure:
        return (
            f"Generate the SEO audit report for: {normalized_url}\n\n"
            "## MANDATORY OUTPUT RULES\n"
            "- Reproduce the provided template exactly in the same order.\n"
            "- Keep all headings, sub-headings, and table column names unchanged.\n"
            "- Do not add, remove, rename, or reorder sections.\n"
            "- Fill every section/table cell using verified evidence and URL-based analysis.\n"
            "- Every SEO Notes cell must contain exactly three URL-specific improvement bullets "
            "formatted as an HTML <ul><li> list — no bold labels, just the improvements.\n"
            "- Never use <br> to separate multiple points in a table cell — use <ul><li> bullets instead.\n"
            "- Never write 'Not Detected' or 'Could not be verified in this audit.' in any table cell.\n"
            "- Do not output any extra wrapper text before or after the report.\n\n"
            "## TEMPLATE TO FILL (VERBATIM STRUCTURE)\n\n"
            f"{master_report_structure}\n\n"
            "---\n\n"
            "## VERIFIED EVIDENCE\n\n"
            f"{evidence_text}"
        )

    return (
        f"Please generate a complete, professional SEO audit report for: {normalized_url}\n\n"
        f"Use only the verified evidence below. "
        f"Do not invent any findings that are not supported by the evidence. "
        f"For every field listed under 'Fields That Could Not Be Verified', "
        f"write exactly: \"Could not be verified in this audit.\"\n\n"
        f"{evidence_text}"
    )


def _missing_required_report_parts(
    markdown_report: str,
    required_headings: tuple[str, ...],
) -> list[str]:
    """
    Return required PART headings that are missing from the generated report.

    Args:
        markdown_report: The LLM-generated Markdown text to validate.
        required_headings: PART headings extracted from the active template.

    This is a lightweight validation helper for observability only.
    """
    return [
        heading
        for heading in required_headings
        if heading not in markdown_report
    ]


def _build_retry_user_message(
    original_user_message: str,
    missing_parts: list[str],
    banned_phrases: list[str] | None = None,
    location_issues: list[str] | None = None,
    citation_issues: list[str] | None = None,
) -> str:
    """
    Build a second-pass instruction that fixes missing headings, contamination,
    SECTION 3 conditional-section violations, and/or missing citations.

    Every issue type this builds an instruction for is a repairable narrative
    issue — something the model's own prose/structure caused and can fix by
    rewriting. It never asks the model to repair a deterministic evidence
    block (a table always force-overwritten after generation, per
    _DETERMINISTIC_TABLE_HEADINGS_BY_GROUP): callers exclude those tables from
    citation_issues before calling this (see generate_report_sections()), and
    missing_parts/banned_phrases/location_issues can only ever describe
    genuine LLM narrative defects.

    Args:
        original_user_message: The original report-generation user message.
        missing_parts: Required PART headings not found in the first output.
        banned_phrases: Contamination/branding phrases found in the first output.
        location_issues: SECTION 3 conditional-section rule violations, if any.
        citation_issues: Source/Retrieved citation-column violations, if any.

    Returns:
        A reinforced user message for one additional LLM attempt.
    """
    instruction_blocks: list[str] = []

    if missing_parts:
        missing_text: str = "\n".join(f"- {heading}" for heading in missing_parts)
        instruction_blocks.append(
            "Your previous output was missing required report parts.\n"
            "Regenerate the full report using the exact same template structure.\n"
            "Do not omit any required PART heading.\n"
            "The following headings were missing and must be present exactly:\n"
            f"{missing_text}"
        )

    if banned_phrases:
        banned_text: str = "\n".join(f"- {phrase}" for phrase in banned_phrases)
        instruction_blocks.append(
            "Your previous output contained forbidden branding or contamination text.\n"
            "Regenerate the full report and do not include any of the following phrases, "
            "or similar references, anywhere in the output:\n"
            f"{banned_text}"
        )

    if location_issues:
        location_text: str = "\n".join(f"- {issue}" for issue in location_issues)
        instruction_blocks.append(
            "Your previous output violated the SECTION 3 conditional-section rule.\n"
            "Exactly one of section 3.2 (Local Location Opportunities) or 3.3 "
            "(Audience & Market Expansion Opportunities) must be completed, and the "
            "other must state it is not applicable.\n"
            f"{location_text}"
        )

    if citation_issues:
        citation_text: str = "\n".join(f"- {issue}" for issue in citation_issues)
        instruction_blocks.append(
            "Your previous output had table rows missing a Source or Retrieved value.\n"
            "Every row in a table with Source/Retrieved columns must cite where the "
            "estimate came from and the audit retrieval date — never leave either cell blank.\n"
            f"{citation_text}"
        )

    instructions: str = "\n\n".join(instruction_blocks)

    return (
        f"{original_user_message}\n\n"
        "## RETRY INSTRUCTION (MANDATORY)\n"
        f"{instructions}\n"
    )
