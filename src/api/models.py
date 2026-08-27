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
    and the full Markdown report.
    """

    audit_id: str = Field(
        ...,  # Required — always present in a successful response
        description="Unique identifier for this audit, used to retrieve the report later",
    )

    url: str = Field(
        ...,  # Required — the normalised URL that was actually audited
        description="Normalised URL that was fetched and analysed (with scheme and www as resolved)",
    )

    markdown_report: str = Field(
        ...,  # Required — the full Markdown text of the audit report
        description="Full SEO audit report in Markdown format, ready for display in the UI",
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),  # Timezone-aware UTC timestamp; avoids DeprecationWarning from utcnow()
        description="UTC timestamp when the audit was completed",
    )


class AuditStatusResult(BaseModel):
    """
    Current in-process job status for an audit, returned by the status
    endpoint so the UI can poll progress without waiting for the full
    synchronous POST /audits/ response.
    """

    audit_id: str = Field(
        ...,  # Required — the same ID returned by POST /audits/
        description="Unique identifier for this audit job",
    )

    url: str = Field(
        ...,  # Required — the normalised URL this job is auditing
        description="Normalised URL this audit job is processing",
    )

    status: str = Field(
        ...,  # Required — one of AuditJobStatus's values (pending, crawling, ..., complete, failed)
        description="Current lifecycle status of the audit job",
    )

    created_at: datetime = Field(
        ...,  # Required — when the job was first created
        description="UTC timestamp when the audit job was created",
    )

    updated_at: datetime = Field(
        ...,  # Required — when the job's status/fields last changed
        description="UTC timestamp when the audit job was last updated",
    )

    error: Optional[str] = Field(
        default=None,  # Only present when status is "failed"
        description="Error message if the job failed, otherwise null",
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
