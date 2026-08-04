"""
src/services/report_service.py

LLM-backed SEO audit report generation service.

Responsibility: take the structured audit evidence produced by
extractor_service and the guidance context loaded by prompt_loader,
call the Gemini LLM, and return a completed Markdown SEO audit report.

The report_service is the only module that calls the LLM.  All other
services are deterministic.  Keeping LLM usage isolated here makes it
easy to audit token usage, swap models, and mock the API in tests.

Hallucination prevention
------------------------
The evidence package passed to the LLM is the only factual input.
The system prompt explicitly instructs Gemini to use only verified
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

import asyncio  # asyncio.to_thread runs the synchronous Gemini SDK call in a thread pool
import logging  # Standard logging — records every LLM call attempt, success, and failure
import os  # os.makedirs creates the reports/ output directory if it does not exist
import re  # re.finditer extracts PART headings from the template at runtime
import uuid  # uuid.uuid4 generates a unique ID for each audit
from dataclasses import dataclass  # dataclass defines the structured result returned to the caller
from datetime import datetime, timezone  # datetime.now(timezone.utc) for timezone-aware UTC timestamps
from pathlib import Path  # Path handles OS-agnostic file paths for report storage

import google.generativeai as genai  # Google Gemini SDK — used when llm_provider=gemini
from openai import AsyncOpenAI  # OpenAI-compatible client — used when llm_provider=perplexity

from src.config import Settings  # Settings provides the API key and model configuration
from src.services.analysis_service import analyze_site  # Deterministic evidence -> score/finding scoring
from src.services.audit_models import (
    AuditContext,
    Finding,
    ResearchBundle,
    ResearchClaim,
    ScoreBreakdown,
    Severity,
    SiteEvidence,
)
from src.services.extractor_service import (
    AuditEvidence,      # Structured verified SEO data extracted from the website
    RobotsTxtEvidence,  # Robots.txt findings — used in evidence formatting
    SitemapEvidence,    # Sitemap accessibility data — used in evidence formatting
)
from src.services.prompt_loader import PromptContext  # Loaded guidance files context
from src.services.research_service import classify_local_business, research_site

# Module-level logger
logger = logging.getLogger(__name__)  # Resolves to "src.services.report_service"

def _extract_required_part_headings(master_report_structure: str) -> tuple[str, ...]:
    """
    Derive required PART headings from the live template content.

    Reads the headings that are actually present in MASTER_REPORT_STRUCTURE.md
    so the validator always reflects the current template, regardless of how
    many parts the file contains.
    """
    return tuple(
        m.group(0)
        for m in re.finditer(r"^# PART \d+:", master_report_structure, re.MULTILINE)
    )


# Phrases that must never leak into a client-facing report — see the
# "Originality & Source Integrity" rules in seo_audit.prompt.md.
_BANNED_PHRASES: tuple[str, ...] = (
    "perplexity",
    "comet browser",
    "chatgpt",
    "google docs",
    "convert to google docs",
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
    Enforce PART 7's conditional rule: exactly one of 7.2 or 7.3 must be
    completed, with the other explicitly marked not applicable.

    Returns a list of human-readable issue descriptions (empty if the
    section is well-formed or PART 7 is absent from this template).
    """
    section_72: str | None = _extract_section_body(markdown_report, "## 7.2")
    section_73: str | None = _extract_section_body(markdown_report, "## 7.3")

    if section_72 is None or section_73 is None:
        # Missing headings are already reported by the required-heading check.
        return []

    is_72_not_applicable: bool = "not applicable" in section_72.lower()
    is_73_not_applicable: bool = "not applicable" in section_73.lower()

    if is_72_not_applicable and is_73_not_applicable:
        return ["PART 7 sections 7.2 and 7.3 are both marked not applicable — exactly one must be completed"]
    if not is_72_not_applicable and not is_73_not_applicable:
        return ["PART 7 sections 7.2 and 7.3 are both completed — exactly one must be marked not applicable"]
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


def _validate_citation_columns(markdown_report: str) -> list[str]:
    """
    Flag data rows in Source/Retrieved-cited tables (PARTS 5-7) that omit a citation.

    Any table whose header includes both a "Source" and "Retrieved" column
    must not contain a data row with an empty value in either column — see
    the External Research Citation Rules in seo_audit.prompt.md.
    """
    issues: list[str] = []

    for table in _find_table_blocks(markdown_report):
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
) -> AuditContext:
    """
    Assemble the immutable AuditContext for one audit: deterministic
    scoring, local-business classification, and external research are
    each independent stages combined here, once, before any
    section-generation call begins.

    Args:
        normalized_url: The website URL that was audited.
        site_evidence: Verified evidence from crawl_service/extractor_service.
        settings: Application settings (Perplexity research configuration).

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
        audit_id=str(uuid.uuid4()),
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
# Fixed, deterministic grouping of MASTER_REPORT_STRUCTURE.md PART headings
# into section-generation calls, so no single call has to hold the entire
# site's evidence in context. The executive summary is generated last, from
# the score/findings/research already assembled for every other group.

_SECTION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("site_inventory", ("PART 2",)),
    ("technical_and_onpage", ("PART 3", "PART 4")),
    ("keyword_strategy", ("PART 5",)),
    ("competitor_analysis", ("PART 6",)),
    ("location_or_market_expansion", ("PART 7",)),
    ("structured_data_and_execution", ("PART 8", "PART 9", "PART 10", "PART 11")),
    ("executive_summary", ("PART 1",)),
)


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


def _format_site_inventory_evidence(site_evidence: SiteEvidence) -> str:
    """Compact evidence slice for PART 2 (full website audit / URL inventory)."""
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
        return f"Technical SEO findings:\n{technical}\n\nOn-Page & Content findings:\n{onpage}"

    if group_name == "keyword_strategy":
        return _format_claims(
            context.research.keyword_opportunities,
            "No keyword opportunities were found with a citable source.",
        )

    if group_name == "competitor_analysis":
        competitors = _format_claims(
            context.research.competitors, "No real competitors were found with a citable source.",
        )
        analysis = _format_claims(
            context.research.competitor_analysis,
            "No competitor strengths/gaps were found with a citable source.",
        )
        return f"Competitors:\n{competitors}\n\nCompetitor analysis:\n{analysis}"

    if group_name == "location_or_market_expansion":
        if context.is_local_business and context.city_or_region:
            return (
                f"Business classification: Local/service-area business (region: {context.city_or_region})\n"
                + _format_claims(
                    context.research.local_demand, "No local demand signals were found with a citable source.",
                )
            )
        return (
            "Business classification: Not local/service-area (or no region could be determined)\n"
            + _format_claims(
                context.research.audience_expansion,
                "No audience/market expansion opportunities were found with a citable source.",
            )
        )

    if group_name == "structured_data_and_execution":
        remaining = _format_findings(
            [f for f in findings if f.category in ("Accessibility", "Security", "Performance")],
            "No Accessibility, Security, or Performance findings were recorded in this audit.",
        )
        authority = _format_claims(
            context.research.authority_opportunities,
            "No off-page/authority opportunities were found with a citable source.",
        )
        return f"Remaining deterministic findings:\n{remaining}\n\nOff-page/authority opportunities:\n{authority}"

    if group_name == "executive_summary":
        category_lines = "\n".join(
            f"- {category.category}: {category.score:.1f}/100 (weight {category.weight_percent:.0f}%)"
            for category in context.score_breakdown.category_scores
        )
        top_priority = [f for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH)]
        priority_text = _format_findings(top_priority, "No Critical or High severity findings in this audit.")
        return (
            f"Overall score: {context.score_breakdown.overall_score:.1f}/100\n\n"
            f"Category scores:\n{category_lines}\n\n"
            f"Top priority (Critical/High) findings:\n{priority_text}"
        )

    raise ValueError(f"Unknown section group: {group_name!r}")


def _extract_part_templates(master_report_structure: str, part_headings: tuple[str, ...]) -> str:
    """
    Extract only the named "# PART N:" template blocks (heading through the
    next top-level PART heading or end of file), in the given order.

    Keeps one section-generation call's template input limited to the PARTs
    it is actually responsible for, instead of the entire 11-PART template.
    """
    blocks: list[str] = []
    for heading_prefix in part_headings:
        pattern = re.compile(
            rf"^(# {re.escape(heading_prefix)}:[^\n]*\n[\s\S]*?)(?=^# PART \d+:|\Z)",
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
) -> str:
    """Build the user-turn message for one section-generation call."""
    template_slice: str = _extract_part_templates(master_report_structure, part_headings)
    heading_list: str = ", ".join(part_headings)

    return (
        f"Generate ONLY the following section(s) of the SEO audit report for: {normalized_url}\n\n"
        "## MANDATORY OUTPUT RULES\n"
        f"- Write ONLY {heading_list}. Do not write any other PART.\n"
        "- Reproduce the provided template exactly, in the same order, with the same headings, "
        "sub-headings, and table column names.\n"
        "- Do not add, remove, rename, or reorder sections.\n"
        "- Fill every section/table cell using only the verified evidence and cited research below "
        "— never invent facts.\n"
        "- Do not output any extra wrapper text, commentary, or explanation before or after the section.\n\n"
        "## TEMPLATE TO FILL (VERBATIM STRUCTURE)\n\n"
        f"{template_slice}\n\n"
        "---\n\n"
        "## EVIDENCE FOR THIS SECTION ONLY\n\n"
        f"{section_evidence}"
    )


async def _call_llm(system_prompt: str, user_message: str, settings: Settings) -> str:
    """Dispatch to the configured LLM provider, used only by the Phase 4 section pipeline."""
    if settings.llm_provider == "perplexity":
        return await _call_perplexity(system_prompt=system_prompt, user_message=user_message, settings=settings)
    return await _call_gemini(system_prompt=system_prompt, user_message=user_message, settings=settings)


async def generate_report_sections(
    context: AuditContext,
    prompt_context: PromptContext,
    settings: Settings,
) -> dict[str, str]:
    """
    Generate each section group's Markdown independently.

    Each group is validated (required headings, banned phrases, PART 7's
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
        section_evidence: str = _format_section_evidence(group_name, context)
        user_message: str = _build_section_user_message(
            context.normalized_url, part_headings, section_evidence, prompt_context.master_report_structure,
        )

        section_markdown: str = await _call_llm(system_prompt, user_message, settings)

        required_headings: tuple[str, ...] = tuple(f"# {heading}:" for heading in part_headings)
        missing: list[str] = _missing_required_report_parts(section_markdown, required_headings)
        banned: list[str] = _find_banned_phrases(section_markdown)
        location_issues: list[str] = (
            _validate_location_section(section_markdown) if group_name == "location_or_market_expansion" else []
        )
        citation_issues: list[str] = _validate_citation_columns(section_markdown)

        if missing or banned or location_issues or citation_issues:
            logger.warning(
                "Section '%s' for %s failed validation (missing=%s banned=%s location=%s citation=%s); retrying once",
                group_name, context.normalized_url, missing, banned, location_issues, citation_issues,
            )
            retry_message: str = _build_retry_user_message(
                user_message, missing, banned, location_issues, citation_issues,
            )
            section_markdown = await _call_llm(system_prompt, retry_message, settings)

            missing = _missing_required_report_parts(section_markdown, required_headings)
            banned = _find_banned_phrases(section_markdown)
            location_issues = (
                _validate_location_section(section_markdown) if group_name == "location_or_market_expansion" else []
            )
            citation_issues = _validate_citation_columns(section_markdown)
            if missing or banned or location_issues or citation_issues:
                logger.warning(
                    "Section '%s' for %s still failed validation after retry; keeping best-effort output",
                    group_name, context.normalized_url,
                )

        sections[group_name] = section_markdown
        logger.info(
            "Section '%s' generated for %s (%d chars)", group_name, context.normalized_url, len(section_markdown),
        )

    return sections


def _first_part_number(group_name: str) -> int:
    """The lowest PART number owned by a section group, e.g. 3 for technical_and_onpage (PART 3, PART 4)."""
    part_headings: tuple[str, ...] = next(headings for name, headings in _SECTION_GROUPS if name == group_name)
    return min(int(heading.split(" ")[1]) for heading in part_headings)


def assemble_report_markdown(sections: dict[str, str]) -> str:
    """
    Combine generate_report_sections()'s per-group Markdown into one final report.

    Groups are ordered by the lowest PART number they own (template order),
    not generation order — so the executive summary (PART 1) leads the
    document even though it is generated last, from the other groups'
    already-known findings and research.

    Args:
        sections: The dict returned by generate_report_sections().

    Returns:
        The assembled Markdown report, sections in PART 1-11 order.
    """
    ordered_group_names: list[str] = sorted(sections.keys(), key=_first_part_number)
    return "\n\n".join(sections[group_name] for group_name in ordered_group_names)


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
        known.update(context.site_evidence.inventory.urls)

    claim_groups: tuple[list[ResearchClaim], ...] = (
        context.research.keyword_opportunities,
        context.research.competitors,
        context.research.competitor_analysis,
        context.research.authority_opportunities,
        context.research.local_demand,
        context.research.audience_expansion,
    )
    known.update(claim.source_url for claims in claim_groups for claim in claims)
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


def _score_text_variants(score: float) -> tuple[str, ...]:
    """Textual forms a score might legitimately be written in, e.g. 82.5 -> ('82.5', '82', '83')."""
    return (f"{score:.1f}", str(int(score)), str(round(score)))


def _validate_score_consistency(markdown_report: str, context: AuditContext) -> list[str]:
    """
    Flag a computed overall/category score that never appears in the report text.

    The scores in context.score_breakdown are deterministic (analysis_service),
    not LLM output — the report narrative must quote these exact figures
    rather than a number the LLM invented independently.
    """
    issues: list[str] = []
    overall = context.score_breakdown.overall_score
    if not any(variant in markdown_report for variant in _score_text_variants(overall)):
        issues.append(f"Computed overall score {overall} does not appear anywhere in the report")

    for category_score in context.score_breakdown.category_scores:
        if not any(variant in markdown_report for variant in _score_text_variants(category_score.score)):
            issues.append(
                f"Computed {category_score.category} score {category_score.score} does not appear anywhere "
                "in the report",
            )
    return issues


# Metrics this MVP's crawler never measures — see analysis_service's "Lighthouse/
# Core Web Vitals data is collected" note and AI_REPORT_GUIDELINES.md Section 8
# ("Invent page speed scores", "Invent Core Web Vitals", "Invent keyword rankings",
# "Invent backlinks"). A specific number for any of these can only be invented.
_UNSUPPORTED_METRIC_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:LCP|INP|CLS)\b\D{0,15}\d", "a specific Core Web Vitals value"),
    (r"\b(?:pagespeed|lighthouse)\D{0,15}\d", "a specific PageSpeed/Lighthouse score"),
    (r"\brank(?:s|ed|ing)?\D{0,10}#?\d+\D{0,15}(?:for|on)\b", "a specific keyword ranking position"),
    (r"\b\d+\D{0,10}backlinks?\b", "a specific backlink count"),
)


def _validate_no_unsupported_metric_claims(markdown_report: str) -> list[str]:
    """
    Flag report text stating a specific Core Web Vitals value, PageSpeed/
    Lighthouse score, keyword ranking position, or backlink count.

    None of these are measured by this MVP's crawler, so any such number
    in the report was necessarily invented by the LLM rather than derived
    from evidence.
    """
    issues: list[str] = []
    for pattern, description in _UNSUPPORTED_METRIC_PATTERNS:
        if re.search(pattern, markdown_report, re.IGNORECASE):
            issues.append(f"Report appears to state {description}, which this audit does not measure")
    return issues


# REPORT_SPECIFICATION.md's "AI Executive Summary" section: "Maximum: 400 words."
_EXECUTIVE_SUMMARY_MAX_WORDS = 400


def _validate_executive_summary_length(markdown_report: str) -> list[str]:
    """Enforce REPORT_SPECIFICATION.md's 400-word maximum for the Executive Summary (PART 1)."""
    match = re.search(r"^# PART 1:[^\n]*\n([\s\S]*?)(?=^# PART \d+:|\Z)", markdown_report, re.MULTILINE)
    if match is None:
        return []

    word_count = len(match.group(1).split())
    if word_count > _EXECUTIVE_SUMMARY_MAX_WORDS:
        return [
            f"Executive summary is {word_count} words, exceeding the "
            f"{_EXECUTIVE_SUMMARY_MAX_WORDS}-word maximum (REPORT_SPECIFICATION.md)",
        ]
    return []


def validate_assembled_report(markdown_report: str, master_report_structure: str, context: AuditContext) -> list[str]:
    """
    Run every report-level validation rule against one assembled Markdown report.

    This is the single entry point called after assemble_report_markdown().
    It only reports issues — it does not retry or repair anything itself,
    since per-section retries already happened in generate_report_sections().

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
    issues.extend(_validate_score_consistency(markdown_report, context))
    issues.extend(_validate_no_unsupported_metric_claims(markdown_report))
    issues.extend(_validate_executive_summary_length(markdown_report))

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

    This is Step 17's "fail with a useful checkpoint status" step: callers
    get back the assembled Markdown (kept even if imperfect, since
    per-section retries already happened in generate_report_sections())
    together with a definitive is_valid verdict and the specific issues
    found, instead of calling assemble_report_markdown() and
    validate_assembled_report() separately and interpreting an empty
    issues list themselves.

    Args:
        sections: The dict returned by generate_report_sections().
        master_report_structure: The live template, used to derive required PART headings.
        context: The AuditContext used to generate this report.

    Returns:
        AssembledReportResult with the assembled Markdown, any issues found, and is_valid.
    """
    markdown_report: str = assemble_report_markdown(sections)
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
) -> ReportResult:
    """
    Generate a Markdown SEO audit report using the Gemini LLM.

    Workflow:
      1. Validate the API key is configured.
      2. Format the verified evidence as a structured text block.
      3. Substitute the website URL into the audit prompt template.
      4. Build the LLM request (system prompt + user message).
      5. Call Gemini asynchronously (non-blocking).
      6. Return the report with a unique audit ID and timestamp.

    Args:
        normalized_url: The website URL that was audited.
        evidence: Verified SEO data from extractor_service.extract().
        prompt_context: Loaded guidance files from prompt_loader.load_prompt_context().
        settings: Application settings providing the Gemini API key and model name.

    Returns:
        ReportResult containing the Markdown report, audit ID, and metadata.

    Raises:
        ValueError: If the Gemini API key is not configured.
        RuntimeError: If the LLM fails to return a usable response.
    """
    logger.info("Starting report generation for: %s", normalized_url)

    # --- Step 1: Validate API key -------------------------------------------

    if settings.llm_provider == "perplexity":
        if not settings.perplexity_api_key:
            raise ValueError(
                "PERPLEXITY_API_KEY is not configured. "
                "Add it to the .env file: PERPLEXITY_API_KEY=your_key_here"
            )
    elif not settings.gemini_api_key:
        # Fail fast with a clear message rather than crashing inside the Gemini SDK
        raise ValueError(
            "GEMINI_API_KEY is not configured. "
            "Add it to the .env file: GEMINI_API_KEY=your_key_here"
        )

    # --- Step 2: Format the audit evidence as structured text ---------------

    evidence_text: str = _format_evidence(normalized_url, evidence)
    # Converts the AuditEvidence dataclass into a readable Markdown-formatted block
    # This is the "user message" that tells the LLM what was found on the website

    # --- Step 3: Substitute URL into the audit prompt -----------------------

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

    # --- Step 4: Build LLM request ------------------------------------------

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

    # --- Step 5: Call the configured LLM provider --------------------------

    if settings.llm_provider == "perplexity":
        markdown_report: str = await _call_perplexity(
            system_prompt=system_prompt,
            user_message=user_message,
            settings=settings,
        )
    else:
        markdown_report: str = await _call_gemini(
            system_prompt=system_prompt,
            user_message=user_message,
            settings=settings,
        )

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

    # Validate PART 7's conditional location/market-expansion rule.
    location_issues: list[str] = _validate_location_section(markdown_report)
    if location_issues:
        logger.warning(
            "Generated report for %s has PART 7 conditional issues: %s",
            normalized_url,
            "; ".join(location_issues),
        )

    # Validate that every Source/Retrieved-cited table (PARTS 5-7) has no
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
            if settings.llm_provider == "perplexity":
                markdown_report = await _call_perplexity(
                    system_prompt=system_prompt,
                    user_message=retry_user_message,
                    settings=settings,
                )
            else:
                markdown_report = await _call_gemini(
                    system_prompt=system_prompt,
                    user_message=retry_user_message,
                    settings=settings,
                )

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
                    "Retry report for %s still has PART 7 conditional issues: %s",
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

    audit_id: str = str(uuid.uuid4())
    # Generate a unique ID for this audit — used as the filename and download key

    created_at: datetime = datetime.now(timezone.utc)
    # Record the UTC completion time using a timezone-aware datetime (avoids DeprecationWarning)

    logger.info(
        "Report generated for %s: audit_id=%s, length=%d chars",
        normalized_url,
        audit_id,
        len(markdown_report),
    )

    return ReportResult(
        audit_id=audit_id,
        normalized_url=normalized_url,
        markdown_report=markdown_report,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# LLM call helpers
# ---------------------------------------------------------------------------

async def _call_gemini(
    system_prompt: str,
    user_message: str,
    settings: Settings,
) -> str:
    """
    Call the Gemini API asynchronously.

    The Gemini SDK is synchronous; asyncio.to_thread() keeps the FastAPI
    event loop unblocked while waiting for the response.

    Args:
        system_prompt: Combined guidance context as the system instruction.
        user_message: Formatted audit evidence as the user turn.
        settings: Provides GEMINI_API_KEY and GEMINI_MODEL.

    Returns:
        Markdown report text from the LLM.

    Raises:
        RuntimeError: If the LLM returns an empty or blocked response.
    """
    logger.info("Calling Gemini model: %s", settings.gemini_model)

    genai.configure(api_key=settings.gemini_api_key)
    # Configure the SDK with the API key from .env — done here (not at module import)
    # so tests can mock genai before any configuration takes place

    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=system_prompt,
    )

    try:
        response = await asyncio.to_thread(model.generate_content, user_message)
    except Exception as llm_error:
        logger.error("Gemini API call failed: %s", llm_error)
        raise RuntimeError(
            f"LLM report generation failed: {llm_error}. "
            "Check GEMINI_API_KEY in .env and verify the API is reachable."
        ) from llm_error

    if not response or not response.text:
        logger.error("Gemini returned an empty or blocked response")
        raise RuntimeError(
            "The LLM returned an empty response. "
            "This may occur if the request was blocked by safety filters. "
            "Try with a different URL or check the Gemini safety settings."
        )

    logger.info("Gemini response received: %d characters", len(response.text))
    return response.text


async def _call_perplexity(
    system_prompt: str,
    user_message: str,
    settings: Settings,
) -> str:
    """
    Call the Perplexity API asynchronously using the OpenAI-compatible client.

    Args:
        system_prompt: Combined guidance context as the system instruction.
        user_message: Formatted audit evidence as the user turn.
        settings: Provides PERPLEXITY_API_KEY and PERPLEXITY_MODEL.

    Returns:
        Markdown report text from the LLM.

    Raises:
        RuntimeError: If the LLM returns an empty or missing response.
    """
    logger.info("Calling Perplexity model: %s", settings.perplexity_model)

    client = AsyncOpenAI(
        api_key=settings.perplexity_api_key,
        base_url="https://api.perplexity.ai",
    )

    try:
        response = await client.chat.completions.create(
            model=settings.perplexity_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=16000,  # sonar-pro needs an explicit limit for long reports
        )
    except Exception as llm_error:
        logger.error("Perplexity API call failed: %s", llm_error)
        raise RuntimeError(
            f"LLM report generation failed: {llm_error}. "
            "Check PERPLEXITY_API_KEY in .env and verify the API is reachable."
        ) from llm_error

    if not response or not response.choices or not response.choices[0].message.content:
        logger.error("Perplexity returned an empty or missing response")
        raise RuntimeError(
            "The LLM returned an empty response. "
            "Check your PERPLEXITY_API_KEY and model name in .env."
        )

    text: str = response.choices[0].message.content
    logger.info("Perplexity response received: %d characters", len(text))
    return text


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
    Build the user-turn message for the Gemini conversation.

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
            "- Every SEO Notes cell must contain exactly three URL-specific improvement bullets separated by <br> — no bold labels, just the improvements.\n"
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
    PART 7 conditional-section violations, and/or missing citations.

    Args:
        original_user_message: The original report-generation user message.
        missing_parts: Required PART headings not found in the first output.
        banned_phrases: Contamination/branding phrases found in the first output.
        location_issues: PART 7 conditional-section rule violations, if any.
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
            "Your previous output violated the PART 7 conditional-section rule.\n"
            "Exactly one of section 7.2 (Local Location Opportunities) or 7.3 "
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
