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
