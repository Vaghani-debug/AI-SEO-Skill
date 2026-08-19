"""
src/services/audit_models.py

Shared typed contracts for the expanded, multi-page audit pipeline.

These dataclasses are the common vocabulary between crawl_service,
extractor_service, analysis_service, research_service, and
report_service so that site inventory, sampled-page evidence,
deterministic findings, scores, and externally researched claims all
have one canonical shape instead of being re-invented per module.

This module intentionally contains no business logic — it only defines
data. Deterministic scoring/finding logic lives in analysis_service;
external research retrieval lives in research_service.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# ---------------------------------------------------------------------------
# Shared per-page evidence primitives
# ---------------------------------------------------------------------------
# These live here (rather than in extractor_service, which imports them back)
# so that extractor_service can also import PageType/PageEvidence from this
# module without creating a circular import.


@dataclass
class ImageInfo:
    """
    Metadata for a single image element found in the HTML.

    Only information visible in the static HTML is recorded.
    Whether the image actually loads is not verified in the MVP.
    """

    src: str
    # The resolved URL of the image (absolute, after joining with the page base URL)

    alt: str
    # The alt attribute value; empty string "" if the attribute is present but blank

    has_alt_attribute: bool
    # True if an alt="" attribute exists at all (even if its value is empty)
    # False if the alt attribute is completely absent from the <img> tag


@dataclass
class RobotsTxtEvidence:
    """
    Verified findings extracted from the /robots.txt file.

    Only data present in the static file content is recorded.
    Whether Google has actually obeyed the rules cannot be verified here.
    """

    is_accessible: bool
    # True if the fetch returned HTTP 200; False for 404, timeout, or error

    http_status: int
    # The HTTP status code returned when fetching /robots.txt (0 if not fetched)

    disallow_rules: list[str]
    # All Disallow: values for the * (all robots) user-agent block

    allow_rules: list[str]
    # All Allow: values for the * (all robots) user-agent block

    sitemap_urls: list[str]
    # All Sitemap: directives found in robots.txt

    blocks_root_path: bool
    # True if Disallow: / or Disallow: /* appears in the * user-agent block
    # This would block Googlebot from crawling the entire site — a critical finding


@dataclass
class SitemapEvidence:
    """
    Verified accessibility status of one sitemap file.

    Only HTTP status and basic URL count are verified in the MVP.
    Full sitemap validation (canonical URLs, changefreq accuracy, etc.)
    requires a crawler and is out of scope.
    """

    url: str
    # The full URL of the sitemap that was fetched

    is_accessible: bool
    # True if the fetch returned HTTP 200

    http_status: int
    # HTTP status code returned (0 if not fetched)

    url_count: int
    # Number of <loc> elements found in the sitemap XML (0 if not accessible or not XML)

    urls: list[str] = field(default_factory=list)
    # Actual <loc> URLs; gives the LLM page inventory to populate tables without "Not Detected"


@dataclass
class SecurityHeadersEvidence:
    """
    Presence/value of key security-relevant HTTP response headers on the homepage.

    Captured directly from the already-fetched homepage response (fetch_service's
    FetchedResource.response_headers) — no extra network call is needed, so this
    evidence is free to collect and always verifiable (never guessed).
    """

    has_hsts: bool
    hsts_value: str | None

    has_content_security_policy: bool
    content_security_policy_value: str | None

    has_x_content_type_options: bool
    x_content_type_options_value: str | None

    has_x_frame_options: bool
    x_frame_options_value: str | None

    has_referrer_policy: bool
    referrer_policy_value: str | None


@dataclass
class PerformanceEvidence:
    """
    Core Web Vitals and Lighthouse performance data from Google PageSpeed
    Insights (free public API) for the homepage.

    data_source distinguishes real-user field data ("field", from the
    Chrome UX Report) from lab-simulated data ("lab", a single Lighthouse
    run) so the report never conflates the two. is_available is False
    (with every metric None) whenever PSI could not be reached or returned
    no usable data - never guessed or invented.
    """

    is_available: bool
    data_source: str
    # "field", "lab", or "" when is_available is False

    performance_score: float | None = None
    # Lighthouse Performance category score, 0-100

    largest_contentful_paint_ms: float | None = None
    cumulative_layout_shift: float | None = None
    interaction_to_next_paint_ms: float | None = None

    source_url: str = ""
    # The audited URL as submitted to PageSpeed Insights, kept as a citation


# ---------------------------------------------------------------------------
# Page classification
# ---------------------------------------------------------------------------


class PageType(str, Enum):
    """Deterministic classification used to build a representative crawl sample."""

    CORE = "core"
    # Navigation/core pages: home, about, contact, pricing, etc.

    SERVICE_PRODUCT = "service_product"
    # Service or product detail pages

    BLOG_ARTICLE = "blog_article"
    # Blog posts and long-form articles

    LOCATION = "location"
    # City/location landing pages (local/service-area businesses)

    CATEGORY = "category"
    # Category, tag, or listing pages

    UTILITY = "utility"
    # Legal, policy, search, pagination, and other non-content utility pages


# ---------------------------------------------------------------------------
# Site inventory
# ---------------------------------------------------------------------------


@dataclass
class SitemapEntry:
    """One URL discovered while walking the sitemap index."""

    url: str
    # The absolute page URL as listed in a sitemap

    source_sitemap: str
    # The sitemap file this URL was discovered in (supports nested sitemap indexes)

    lastmod: str | None = None
    # The <lastmod> value if present in the sitemap, else None

    page_type: PageType | None = None
    # Deterministic classification assigned by crawl_service.classify_url(), None until classified


@dataclass
class SiteInventory:
    """The full set of URLs discovered for a site, before sampling."""

    base_url: str
    # The normalized URL that was audited

    entries: list[SitemapEntry] = field(default_factory=list)
    # Every deduplicated URL discovered across all sitemaps (may exceed the crawl sample size)

    total_url_count: int = 0
    # len(entries) — kept as an explicit field so truncated evidence still reports the true total

    sampled_urls: list[str] = field(default_factory=list)
    # The stable, deterministic subset of entries selected for crawling (see crawl_service)


# ---------------------------------------------------------------------------
# Per-page evidence
# ---------------------------------------------------------------------------


@dataclass
class PageEvidence:
    """
    Verified SEO data extracted from a single crawled page.

    This generalizes the homepage-only fields already produced by
    extractor_service.AuditEvidence so the same shape can describe any
    sampled page (service, blog, location, category, etc.), not only
    the homepage.
    """

    url: str
    # The absolute URL of the crawled page

    page_type: PageType
    # Deterministic classification assigned during sampling

    http_status: int
    # HTTP response code (0 if the request failed entirely)

    is_https: bool
    # True if the final URL uses the https:// scheme

    used_playwright_fallback: bool
    # True if this page required browser rendering (JS-shell/empty static HTML)

    page_title: str | None
    meta_description: str | None
    canonical_url: str | None
    page_language: str | None

    meta_robots: str | None = None
    # Content attribute of <meta name="robots">, e.g. "noindex, nofollow"; None if absent

    open_graph: dict[str, str] = field(default_factory=dict)
    # Open Graph properties keyed without the "og:" prefix (e.g. {"title": ..., "image": ...})

    h1_tags: list[str] = field(default_factory=list)
    h2_tags: list[str] = field(default_factory=list)

    word_count: int = 0
    # Approximate visible body word count, used for thin-content checks

    schema_types: list[str] = field(default_factory=list)
    # Structured data @type values found on the page (e.g. "LocalBusiness", "Article")

    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)

    redirect_chain: list[str] = field(default_factory=list)
    # Intermediate URLs visited before reaching the final response, empty if no redirect

    attempt_count: int = 1
    # How many HTTP attempts produced this page's http_status (see fetch_service.py's
    # retry_on_transient_failure()); used to decide whether a non-200 status is confirmed
    # or just a single, unconfirmed observation (analysis_service.py).


@dataclass
class SiteEvidence:
    """
    Complete verified evidence for one audit: homepage detail plus the
    sampled multi-page crawl, robots.txt, and sitemap inventory.
    """

    base_url: str
    final_url: str

    homepage: PageEvidence
    # The homepage is always crawled and always classified as PageType.CORE

    sampled_pages: list[PageEvidence] = field(default_factory=list)
    # Non-homepage pages selected by the deterministic sampling strategy

    inventory: SiteInventory | None = None
    # The full sitemap inventory this sample was drawn from

    robots_txt: RobotsTxtEvidence | None = None
    sitemaps: list[SitemapEvidence] = field(default_factory=list)

    security_headers: SecurityHeadersEvidence | None = None
    # HTTP security headers observed on the homepage response; None if the homepage fetch failed

    performance: PerformanceEvidence | None = None
    # Core Web Vitals / Lighthouse data from PageSpeed Insights; None if PSI was unavailable

    unverifiable_fields: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic findings and scoring
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Matches the fixed severity vocabulary in seo_audit.prompt.md."""

    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INFORMATIONAL = "Informational"


class EffortLevel(str, Enum):
    """Estimated implementation effort for a recommendation."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


@dataclass
class Finding:
    """
    One deterministic SEO issue produced by analysis_service.

    Only measurable evidence may produce a Finding or a score_deduction;
    the LLM explains and prioritizes findings but never invents them or
    changes their score impact (see SCORING_ENGINE.md, Principle 4).
    """

    category: str
    # One of the SCORING_ENGINE.md categories, e.g. "Technical SEO", "On-Page SEO"

    title: str
    severity: Severity
    description: str
    # What is wrong, grounded in the evidence

    business_impact: str
    recommendation: str
    effort: EffortLevel

    evidence_urls: list[str] = field(default_factory=list)
    # The specific page URL(s) this finding was observed on

    score_deduction: float = 0.0
    # Points deducted from the category score for this finding


@dataclass
class CategoryScore:
    """One weighted category score, per SCORING_ENGINE.md Section 4."""

    category: str
    weight_percent: float
    score: float
    # 0-100 score for this category after all deductions


@dataclass
class ScoreBreakdown:
    """The full deterministic scoring result for one audit."""

    overall_score: float
    # 0-100 weighted overall score

    category_scores: list[CategoryScore] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# External research provenance
# ---------------------------------------------------------------------------


class ResearchStatus(str, Enum):
    """Outcome of one bounded research operation."""

    SUCCESS = "success"
    NO_RESULTS = "no_results"
    PARSE_FAILED = "parse_failed"
    CITATION_FAILED = "citation_failed"
    PROVIDER_FAILED = "provider_failed"
    INSUFFICIENT_LOCATION_EVIDENCE = "insufficient_location_evidence"
    # Deterministic (no LLM call made): the business was classified as local/service-area,
    # but no city_or_region signal was found in crawl evidence - never faked with a placeholder region.


@dataclass
class ResearchClaim:
    """
    One externally researched claim (keyword estimate, competitor fact,
    local demand signal, etc.) with mandatory provenance.

    Uncited numeric claims must never reach the report — every claim
    normalized by research_service carries a source and retrieval date.
    """

    claim: str
    # Plain-language statement of what was found, e.g. "Estimated monthly search volume"

    value: str
    # The estimated value/range as text, e.g. "1,000-10,000/mo"

    source_url: str
    source_title: str
    retrieved_date: str
    # ISO date (YYYY-MM-DD) the research was performed

    confidence: str = "Estimate"
    # Free-text confidence label, e.g. "Estimate", "High confidence"

    is_estimate: bool = True
    # True unless the claim is a directly verified fact (rare for external research)


@dataclass
class KeywordOpportunity:
    """
    One typed primary or long-tail keyword opportunity with mandatory
    provenance, matching Section 1's Primary/Long-Tail Keywords Table columns.
    """

    keyword: str
    search_intent: str
    source_url: str
    source_title: str
    retrieved_date: str

    estimated_volume: str | None = None
    # A sourced monthly search volume estimate as text (e.g. "1,000-10,000/mo"); None when no
    # citable estimate exists - a volume figure is never fabricated to fill this field.

    target_page: str | None = None
    # The most relevant existing page path on the site for this keyword; None if none fits.


@dataclass
class KeywordResearchResult:
    """Typed keyword opportunities plus an explicit outcome, preserving why none were returned."""

    status: ResearchStatus
    opportunities: list[KeywordOpportunity] = field(default_factory=list)
    error: str | None = None


@dataclass
class CompetitorOverview:
    """
    One typed real competitor with mandatory provenance, matching Section 2's
    Competitor Overview Table columns. website must be a citation-verified
    real URL - a competitor is never accepted on self-reported text alone.
    """

    competitor_name: str
    website: str
    focus: str
    source_url: str
    source_title: str
    retrieved_date: str

    estimated_authority: str | None = None
    # A sourced authority signal as free text (e.g. "High", "Domain Authority ~45"); None
    # when no citable estimate exists - never fabricated to fill this field.


@dataclass
class CompetitorResearchResult:
    """Typed competitor overviews plus an explicit outcome, preserving why none were returned."""

    status: ResearchStatus
    competitors: list[CompetitorOverview] = field(default_factory=list)
    error: str | None = None


@dataclass
class CompetitorGap:
    """
    One typed competitive gap/keyword-position row, matching Section 2's
    Keyword Gap Table columns. Only ever derived from already-accepted
    (citation-verified) competitors - never an invented competitor.
    """

    keyword: str
    competitor_position: str
    your_gap: str
    source_url: str
    source_title: str
    retrieved_date: str


@dataclass
class CompetitorGapResult:
    """Typed competitive gaps plus an explicit outcome, preserving why none were returned."""

    status: ResearchStatus
    gaps: list[CompetitorGap] = field(default_factory=list)
    error: str | None = None


@dataclass
class LocationOpportunity:
    """
    One typed local-demand/location row, matching Section 3's Location
    Opportunity Table columns. city_or_region always comes from deterministic
    classify_local_business() evidence, never invented by the LLM.
    """

    city_or_region: str
    primary_keyword: str
    priority: str
    source_url: str
    source_title: str
    retrieved_date: str

    estimated_volume: str | None = None
    # A sourced monthly search volume estimate as text; None when no citable estimate exists.


@dataclass
class LocationResearchResult:
    """Typed location opportunities plus an explicit outcome, preserving why none were returned."""

    status: ResearchStatus
    opportunities: list[LocationOpportunity] = field(default_factory=list)
    error: str | None = None


@dataclass
class ResearchBundle:
    """
    All externally researched claims for one audit, grouped by category.

    Kept as its own top-level type - never merged into SiteEvidence - so
    verified crawl evidence and cited external research stay clearly
    separated everywhere downstream (scoring, section generation, report
    assembly).
    """

    primary_keywords: list[KeywordOpportunity] = field(default_factory=list)
    long_tail_keywords: list[KeywordOpportunity] = field(default_factory=list)
    competitors: list[CompetitorOverview] = field(default_factory=list)
    competitor_analysis: list[CompetitorGap] = field(default_factory=list)
    authority_opportunities: list[ResearchClaim] = field(default_factory=list)
    brand_presence: list[ResearchClaim] = field(default_factory=list)
    # Cited evidence of where the brand is already visible online (directories, social
    # profiles, press) - SEO_RULES.md Section 5 "Authority (Basic MVP)" Brand Presence check.
    # Domain Authority / Backlink Summary are marked optional in that section and are
    # deliberately NOT implemented here: no free, verified data source exists for them, and
    # guessing a number would violate the report's "never invent backlinks" rule.

    local_demand: list[LocationOpportunity] = field(default_factory=list)
    # Empty unless the site was classified as local/service-area with a known region

    audience_expansion: list[ResearchClaim] = field(default_factory=list)
    # Empty for local/service-area sites; populated instead of local_demand otherwise

    research_statuses: dict[str, ResearchStatus] = field(default_factory=dict)
    # Maps each field name above (e.g. "primary_keywords") to its outcome status, so a
    # genuine zero-result search (no_results) can be told apart from a provider/parse/citation
    # failure (provider_failed/parse_failed/citation_failed) even though both leave that field's
    # list empty. A category absent from this map was never run (e.g. local_demand for a
    # non-local business).


# ---------------------------------------------------------------------------
# Report-facing evidence projections
# ---------------------------------------------------------------------------


@dataclass
class PageReportRow:
    """Verified page fields available to deterministic report renderers."""

    url: str
    page_type: PageType
    was_crawled: bool

    http_status: int | None = None
    page_title: str | None = None
    meta_description: str | None = None
    canonical_url: str | None = None
    page_language: str | None = None
    meta_robots: str | None = None

    h1_tags: list[str] = field(default_factory=list)
    h2_tags: list[str] = field(default_factory=list)
    word_count: int | None = None
    schema_types: list[str] = field(default_factory=list)
    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    redirect_chain: list[str] = field(default_factory=list)

    used_playwright_fallback: bool = False
    source_sitemap: str | None = None
    sitemap_lastmod: str | None = None

    attempt_count: int = 1
    # Mirrors PageEvidence.attempt_count for crawled pages (see build_page_seo_notes()'s
    # confirmed-vs-unconfirmed HTTP status rule); stays at the default for sitemap-only rows,
    # which were never fetched.


@dataclass
class InventorySectionData:
    """Deterministic input for the Core Pages and Subpages report tables."""

    core_pages: list[PageReportRow] = field(default_factory=list)
    subpages: list[PageReportRow] = field(default_factory=list)
    sitemap_only_pages: list[PageReportRow] = field(default_factory=list)
    total_discovered: int = 0
    total_analyzed: int = 0


@dataclass
class TechnicalSectionData:
    """Verified technical evidence and deterministic findings for Part 2."""

    findings: list[Finding] = field(default_factory=list)
    robots_txt: RobotsTxtEvidence | None = None
    sitemaps: list[SitemapEvidence] = field(default_factory=list)
    performance: PerformanceEvidence | None = None
    detected_schema_types: list[str] = field(default_factory=list)
    pages: list[PageReportRow] = field(default_factory=list)


@dataclass
class OnPageSectionData:
    """Verified homepage, priority-page, and content evidence for Part 3."""

    homepage: PageReportRow
    priority_pages: list[PageReportRow] = field(default_factory=list)
    on_page_findings: list[Finding] = field(default_factory=list)
    content_findings: list[Finding] = field(default_factory=list)


@dataclass
class ResearchResult:
    """Claims plus an explicit outcome, preserving why no claims were returned."""

    status: ResearchStatus
    claims: list[ResearchClaim] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class AuditContext:
    """
    Complete, immutable input bundle for one audit's report generation.

    Built once per audit (crawl evidence + deterministic score + external
    research + local-business classification) and passed unchanged to
    every section-generation call in the pipeline, so no call can drift
    from what another call saw.
    """

    audit_id: str
    normalized_url: str
    site_evidence: SiteEvidence
    score_breakdown: ScoreBreakdown
    research: ResearchBundle
    is_local_business: bool
    city_or_region: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# In-process job state (Phase 5)
# ---------------------------------------------------------------------------


class AuditJobStatus(str, Enum):
    """Lifecycle phase of one in-process audit job, reported via the status endpoint."""

    PENDING = "pending"
    CRAWLING = "crawling"
    RESEARCHING = "researching"
    GENERATING = "generating"
    ASSEMBLING = "assembling"
    RENDERING_PDF = "rendering_pdf"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class AuditJob:
    """
    Persisted state for one in-process audit job.

    Created immediately when a start-audit request is accepted (before any
    crawling/generation begins) and updated in place as the job progresses,
    so a status endpoint can report real progress instead of the caller
    blocking until the whole pipeline finishes. Mutable (unlike AuditContext)
    because status/error/output fields are set at different pipeline stages.
    """

    audit_id: str
    normalized_url: str
    status: AuditJobStatus
    created_at: datetime
    updated_at: datetime

    markdown_report: str | None = None
    # Set once assembly completes; None while pending/in-progress or on failure

    pdf_path: str | None = None
    # Set once the PDF has been rendered to disk; None until then

    error: str | None = None
    # Human-readable failure reason; set only when status is FAILED
