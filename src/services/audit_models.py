"""
src/services/audit_models.py

Shared typed contracts used by the audit pipeline.

extractor_service builds these from static HTML/robots.txt/sitemap content,
and report_service formats them into the LLM's evidence prompt. AuditJob/
AuditJobStatus track in-process job state for the API layer.

This module intentionally contains no business logic — it only defines
data.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# ---------------------------------------------------------------------------
# Shared per-page evidence primitives
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# In-process job state
# ---------------------------------------------------------------------------


class AuditJobStatus(str, Enum):
    """Lifecycle phase of one in-process audit job, reported via the status endpoint."""

    PENDING = "pending"
    CRAWLING = "crawling"
    RESEARCHING = "researching"
    GENERATING = "generating"
    ASSEMBLING = "assembling"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class AuditJob:
    """
    Persisted state for one in-process audit job.

    Created immediately when a start-audit request is accepted (before any
    fetching/generation begins) and updated in place as the job progresses,
    so a status endpoint can report real progress instead of the caller
    blocking until the whole pipeline finishes. Mutable because status/
    error/output fields are set at different pipeline stages.
    """

    audit_id: str
    normalized_url: str
    status: AuditJobStatus
    created_at: datetime
    updated_at: datetime

    markdown_report: str | None = None
    # Set once generation completes; None while pending/in-progress or on failure

    error: str | None = None
    # Human-readable failure reason; set only when status is FAILED


# ---------------------------------------------------------------------------
# Universal Recommendation & Audit Scoring Contracts (Phase 1)
# ---------------------------------------------------------------------------


class EvidenceProvenance(str, Enum):
    """Data provenance classification for findings, claims, and metrics."""

    MEASURED = "measured"
    # Directly observed in crawler, DOM, or HTTP fetch
    RESEARCHED = "researched"
    # Returned with a verified source citation by live web search
    DERIVED = "derived"
    # Deterministically calculated from measured inputs (e.g., ratios, counts)
    CONSULTANT_ASSESSMENT = "consultant_assessment"
    # Bounded LLM qualitative evaluation grounded strictly in evidence
    CLIENT_INPUT_REQUIRED = "client_input_required"
    # Business context requiring confirmation from the client/site owner
    INTEGRATION_REQUIRED = "integration_required"
    # Metric requiring an authenticated 3rd-party API (GSC, GA4, backlink API)


class FindingStatus(str, Enum):
    """Execution status for an individual audit check or finding."""

    PASS = "Pass"
    ISSUE = "Issue"
    OPPORTUNITY = "Opportunity"
    UNVERIFIED = "Unverified"
    NOT_APPLICABLE = "Not applicable"


class SeverityLevel(str, Enum):
    """Severity classification for audit issues and opportunities."""

    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INFORMATIONAL = "Informational"


class ImplementationOwner(str, Enum):
    """Suggested responsible role for an SEO recommendation."""

    DEVELOPER = "Developer"
    CONTENT_WRITER = "Content Writer"
    SEO_SPECIALIST = "SEO Specialist"
    SITE_OWNER = "Site Owner"
    DEVOPS = "DevOps"


@dataclass
class RecommendationItem:
    """
    Universal recommendation data structure used across all audit sections.

    Encapsulates both the technical finding and the actionable business/implementation
    guidance so every recommendation is structured, prioritized, and verifiable.
    """

    finding_id: str
    # Stable unique identifier, e.g. "TECH-CANONICAL-001"

    category: str
    # Audit category: "Technical SEO", "On-Page SEO", "Content Quality", "UX & Performance", "Strategy"

    affected_urls: list[str] = field(default_factory=list)
    # List of specific URLs exhibiting the issue or target of the opportunity

    status: FindingStatus = FindingStatus.ISSUE
    # Current finding status

    evidence: str = ""
    # Concrete, measurable evidence description

    severity: SeverityLevel = SeverityLevel.MEDIUM
    # Impact severity

    business_impact: str = ""
    # Plain-English explanation of business/traffic/conversion impact

    why_it_matters: str = ""
    # Technical explanation of how search engines evaluate this issue

    recommended_action: str = ""
    # Clear, step-by-step actionable remediation instructions

    priority: int = 3
    # Numeric priority (1 = highest / critical fix, 5 = lowest / minor polish)

    effort: str = "Medium"
    # Implementation effort: "Easy", "Medium", "Hard"

    estimated_time: str = ""
    # Estimated time to implement (e.g. "15 minutes", "2 hours", "1 day")

    suggested_owner: ImplementationOwner = ImplementationOwner.DEVELOPER
    # Suggested role responsible for executing the fix

    dependencies: list[str] = field(default_factory=list)
    # Pre-requisite fixes or external approvals required

    validation_method: str = ""
    # Exact method/tool to verify the fix after deployment

    kpi: str = ""
    # Target measurement metric (e.g. "Snippet CTR", "Index coverage", "LCP < 2.5s")

    confidence: float = 1.0
    # Confidence score from 0.0 to 1.0 (1.0 for deterministic measured findings)

    provenance: EvidenceProvenance = EvidenceProvenance.MEASURED
    # Source provenance tier

    source_references: list[str] = field(default_factory=list)
    # URLs or documentation references supporting the recommendation


@dataclass
class CategoryScoreBreakdown:
    """Deterministic score and evidence coverage for one audit category."""

    category: str
    # Category name, matching SEO_RULES.md (e.g. "Technical SEO", "On-Page SEO")

    weight: float
    # Category weight in overall score calculation (e.g. 0.40 for Technical SEO)

    score: float
    # Deterministic score from 0.0 to 100.0

    evidence_coverage: float
    # Fraction of applicable checks that had verified evidence (0.0 to 1.0)

    passed_checks: int = 0
    # Number of checks passed

    total_applicable_checks: int = 0
    # Total number of applicable checks evaluated


@dataclass
class ScoreBreakdown:
    """Overall deterministic SEO score and aggregated evidence coverage."""

    overall_score: float
    # Weighted overall score from 0.0 to 100.0

    overall_coverage: float
    # Weighted evidence coverage from 0.0 to 1.0

    categories: dict[str, CategoryScoreBreakdown] = field(default_factory=dict)
    # Per-category score breakdown dictionary


@dataclass
class AuditCoverage:
    """Audit evidence coverage summary tracking crawl scope and data completeness."""

    pages_discovered: int = 0
    # Total URLs found in sitemaps and crawl

    pages_crawled: int = 0
    # Total pages successfully fetched and analyzed

    pages_failed: int = 0
    # Total pages that returned errors during fetch

    rendered_pages: int = 0
    # Total pages rendered via browser automation (if any)

    research_available: bool = False
    # Whether live market research citations were successfully obtained

    evidence_coverage_ratio: float = 0.0
    # Aggregated ratio of applicable checks verified (0.0 to 1.0)
