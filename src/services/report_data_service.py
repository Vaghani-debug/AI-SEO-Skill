"""
src/services/report_data_service.py

Deterministic evidence projection layer for the new report pipeline.

Responsibility: build the report-facing section data structures defined in
audit_models.py (InventorySectionData/TechnicalSectionData/OnPageSectionData)
from one immutable AuditContext, so later rendering never has to re-derive
which pages were actually crawled, which are sitemap-only, or which
deterministic findings belong to which section — that decision is made once,
here, from already-verified evidence.

This module contains no LLM calls and no Markdown rendering. It only
reshapes verified evidence already produced by crawl_service/extractor_service
(SiteEvidence) and analysis_service (ScoreBreakdown). Uncrawled sitemap-only
pages are projected with no title/status/content facts — those must never be
invented downstream.

Public interface:
    build_inventory_section_data(context) -> InventorySectionData
    build_technical_section_data(context) -> TechnicalSectionData
    build_on_page_section_data(context) -> OnPageSectionData
"""

from src.services.audit_models import (
    AuditContext,
    Finding,
    InventorySectionData,
    OnPageSectionData,
    PageEvidence,
    PageReportRow,
    PageType,
    SitemapEntry,
    TechnicalSectionData,
)

_TECHNICAL_CATEGORY = "Technical SEO"
_ON_PAGE_CATEGORY = "On-Page SEO"
_CONTENT_CATEGORY = "Content Quality"


def _page_evidence_to_row(page: PageEvidence) -> PageReportRow:
    """Project one crawled PageEvidence into a PageReportRow, preserving every verified field."""
    return PageReportRow(
        url=page.url,
        page_type=page.page_type,
        was_crawled=True,
        http_status=page.http_status,
        page_title=page.page_title,
        meta_description=page.meta_description,
        canonical_url=page.canonical_url,
        page_language=page.page_language,
        meta_robots=page.meta_robots,
        h1_tags=list(page.h1_tags),
        h2_tags=list(page.h2_tags),
        word_count=page.word_count,
        schema_types=list(page.schema_types),
        internal_links=list(page.internal_links),
        external_links=list(page.external_links),
        redirect_chain=list(page.redirect_chain),
        used_playwright_fallback=page.used_playwright_fallback,
        attempt_count=page.attempt_count,
    )


def _sitemap_entry_to_row(entry: SitemapEntry) -> PageReportRow:
    """Project one uncrawled sitemap-only entry with no invented title/status/content facts."""
    return PageReportRow(
        url=entry.url,
        page_type=entry.page_type or PageType.UTILITY,
        was_crawled=False,
        source_sitemap=entry.source_sitemap,
        sitemap_lastmod=entry.lastmod,
    )


def _crawled_pages(context: AuditContext) -> list[PageEvidence]:
    """Every page this audit actually fetched: the homepage plus every sampled page, in a stable order."""
    return [context.site_evidence.homepage, *context.site_evidence.sampled_pages]


def build_inventory_section_data(context: AuditContext) -> InventorySectionData:
    """
    Build PART 1's Core Pages / Subpages / sitemap-only evidence.

    Core pages are crawled pages classified PageType.CORE (always includes
    the homepage); subpages are every other crawled page. Sitemap-only pages
    are inventory entries that were discovered but never selected for
    crawling — a renderer must never invent their title, status, or content.
    """
    crawled = _crawled_pages(context)
    crawled_urls = {page.url for page in crawled}

    core_pages = [_page_evidence_to_row(page) for page in crawled if page.page_type == PageType.CORE]
    subpages = [_page_evidence_to_row(page) for page in crawled if page.page_type != PageType.CORE]

    inventory = context.site_evidence.inventory
    sitemap_only_pages: list[PageReportRow] = []
    if inventory is not None:
        sitemap_only_pages = [
            _sitemap_entry_to_row(entry) for entry in inventory.entries if entry.url not in crawled_urls
        ]

    total_discovered = inventory.total_url_count if inventory is not None else len(crawled)

    return InventorySectionData(
        core_pages=core_pages,
        subpages=subpages,
        sitemap_only_pages=sitemap_only_pages,
        total_discovered=total_discovered,
        total_analyzed=len(crawled),
    )


def build_technical_section_data(context: AuditContext) -> TechnicalSectionData:
    """
    Build PART 2's verified technical evidence: deterministic Technical SEO
    findings, robots.txt/sitemap/PageSpeed evidence, every schema.org @type
    detected across crawled pages, and each crawled page's technical fields.
    """
    crawled = _crawled_pages(context)
    findings: list[Finding] = [
        finding for finding in context.score_breakdown.findings if finding.category == _TECHNICAL_CATEGORY
    ]

    detected_schema_types: list[str] = []
    seen_schema: set[str] = set()
    for page in crawled:
        for schema_type in page.schema_types:
            if schema_type not in seen_schema:
                seen_schema.add(schema_type)
                detected_schema_types.append(schema_type)

    return TechnicalSectionData(
        findings=findings,
        robots_txt=context.site_evidence.robots_txt,
        sitemaps=list(context.site_evidence.sitemaps),
        performance=context.site_evidence.performance,
        detected_schema_types=detected_schema_types,
        pages=[_page_evidence_to_row(page) for page in crawled],
    )


def build_on_page_section_data(context: AuditContext) -> OnPageSectionData:
    """
    Build PART 3's verified homepage/priority-page evidence plus on-page and
    content-quality findings. Priority pages are every sampled (non-homepage)
    page, in the same order they were crawled.
    """
    on_page_findings = [
        finding for finding in context.score_breakdown.findings if finding.category == _ON_PAGE_CATEGORY
    ]
    content_findings = [
        finding for finding in context.score_breakdown.findings if finding.category == _CONTENT_CATEGORY
    ]

    return OnPageSectionData(
        homepage=_page_evidence_to_row(context.site_evidence.homepage),
        priority_pages=[_page_evidence_to_row(page) for page in context.site_evidence.sampled_pages],
        on_page_findings=on_page_findings,
        content_findings=content_findings,
    )
