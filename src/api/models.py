"""
src/api/models.py

Pydantic request and response models for the SEO audit API.

These models are shared across routes and services.  Every API endpoint
must use these models for input validation and output serialisation —
never use raw dicts or untyped parameters.

Each model has a docstring describing its purpose and a comment on
every field explaining what it holds and why.
"""

from datetime import datetime, timezone  # datetime.now(timezone.utc) for timezone-aware UTC timestamps
from typing import Optional  # Marks fields that may be absent (None allowed)

from pydantic import BaseModel, Field, HttpUrl  # BaseModel for data classes; Field for defaults/docs; HttpUrl for URL validation


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AuditRequest(BaseModel):
    """
    Payload sent by the UI when the user clicks the Audit button.

    The only required input is the website URL.  The URL may be a bare domain
    (e.g. www.example.com) — the url_service will normalise it to a full URL
    before fetching.
    """

    url: str = Field(
        ...,  # ... means this field is required — the request is invalid without it
        min_length=3,  # Reject empty strings and single-character inputs
        max_length=2048,  # Limit to a sensible URL length
        description="Website URL or bare domain to audit (e.g. https://example.com or www.example.com)",
        examples=["https://www.truelinesolution.com", "www.example.com"],  # Shown in /docs
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class AuditResult(BaseModel):
    """
    Successful audit response returned to the UI after the audit completes.

    Contains the audit identifier, the normalised URL that was actually fetched,
    the full Markdown report, and a URL the UI can use to download the PDF.
    """

    audit_id: str = Field(
        ...,  # Required — always present in a successful response
        description="Unique identifier for this audit, used to retrieve the report and PDF later",
    )

    url: str = Field(
        ...,  # Required — the normalised URL that was actually audited
        description="Normalised URL that was fetched and analysed (with scheme and www as resolved)",
    )

    markdown_report: str = Field(
        ...,  # Required — the full Markdown text of the audit report
        description="Full SEO audit report in Markdown format, ready for display in the UI",
    )

    pdf_download_url: str = Field(
        ...,  # Required — relative path the UI uses to trigger the PDF download
        description="Relative URL to the PDF download endpoint, e.g. /api/v1/audits/{audit_id}/pdf",
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),  # Timezone-aware UTC timestamp; avoids DeprecationWarning from utcnow()
        description="UTC timestamp when the audit was completed",
    )


class AuditError(BaseModel):
    """
    Error response returned when an audit fails for any reason.

    Every error must include a human-readable message suitable for display
    in the UI.  The detail field carries technical context for developers.
    """

    error: str = Field(
        ...,  # Required — short error code or category (e.g. "invalid_url", "fetch_failed")
        description="Short error identifier, suitable for programmatic error handling",
    )

    message: str = Field(
        ...,  # Required — plain-English description of what went wrong
        description="User-friendly error message suitable for display in the UI",
    )

    detail: Optional[str] = Field(
        default=None,  # Optional technical detail; only included when it helps diagnosis
        description="Optional technical detail for developers (stack trace summary, exception type, etc.)",
    )


class ReportDownload(BaseModel):
    """
    Metadata returned alongside a PDF file download.

    FastAPI's FileResponse handles the binary content; this model
    captures the descriptive metadata the UI might display.
    """

    audit_id: str = Field(
        ...,  # Required — links this download to the originating audit
        description="Audit identifier that the PDF was generated from",
    )

    filename: str = Field(
        ...,  # Required — the suggested filename for the downloaded file
        description="Suggested filename for the PDF download, e.g. seo-audit-2026-07-09.pdf",
    )

    url: str = Field(
        ...,  # Required — normalised URL of the site that was audited
        description="Normalised URL of the audited website, included for display purposes",
    )
