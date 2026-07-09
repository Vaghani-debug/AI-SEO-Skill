"""
src/api/routes/audit.py

SEO audit API route definitions.

This module is intentionally thin.  Routes only:
  1. Validate the incoming request (handled by Pydantic models)
  2. Delegate to service functions (not yet implemented — stubs used for now)
  3. Return a structured response or raise an HTTPException

No business logic lives here.  SEO analysis, fetching, LLM calls, and
PDF generation all belong in src/services/.

Endpoints:
    POST /api/v1/audits        — start a new audit
    GET  /api/v1/audits/{id}   — retrieve a completed audit by ID
    GET  /api/v1/audits/{id}/pdf — download the PDF report
"""

import logging  # Standard Python logging; we log the start, success, and failure of every operation

from fastapi import APIRouter, HTTPException, status  # APIRouter groups related routes; HTTPException returns error responses
from fastapi.responses import FileResponse  # FileResponse streams a file from disk as a download

from src.api.models import AuditRequest, AuditResult, AuditError  # Import the shared request/response models

# Module-level logger — every audit operation logs its progress here
logger = logging.getLogger(__name__)  # __name__ gives us "src.api.routes.audit" in log output

# Create the router; all routes defined here will be mounted under /api/v1 in main.py
router = APIRouter(
    prefix="/audits",  # All routes in this file begin with /audits; combined with the v1 prefix → /api/v1/audits
    tags=["audits"],  # Groups all endpoints under "audits" in the /docs page
)


# ---------------------------------------------------------------------------
# POST /api/v1/audits
# Start a new SEO audit for the provided URL.
# ---------------------------------------------------------------------------

@router.post(
    "/",  # Maps to POST /api/v1/audits/
    response_model=AuditResult,  # FastAPI validates the return value against AuditResult before sending
    status_code=status.HTTP_202_ACCEPTED,  # 202 Accepted because the audit is processed synchronously but may take time
    summary="Start an SEO audit",  # Short description shown in /docs
    description=(
        "Accepts a website URL, fetches the site, extracts verified SEO data, "
        "generates a Markdown report using the LLM, and returns the completed report "
        "along with a PDF download URL."
    ),
    responses={
        400: {"model": AuditError, "description": "Invalid URL or unsupported scheme"},  # Documented error shapes
        500: {"model": AuditError, "description": "Unexpected server error during audit"},
    },
)
async def start_audit(request: AuditRequest) -> AuditResult:
    """
    Start a new SEO audit.

    Step 2 stub: The service layer (url_service, fetch_service, extractor_service,
    report_service, pdf_service) is not yet implemented.  This route returns
    a placeholder response so the application shell can be tested end-to-end.
    Full service wiring happens in Steps 4–10 of the MVP plan.
    """
    logger.info("Audit requested for URL: %s", request.url)  # Log the incoming request URL

    # --- Placeholder until service layer is implemented (Steps 4–10) ---
    # In the final implementation this line will call:
    #   url = url_service.normalize(request.url)
    #   evidence = fetch_service.fetch(url)
    #   data = extractor_service.extract(evidence)
    #   report = report_service.generate(data)
    #   pdf_path = pdf_service.render(report)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,  # 501 signals "coming in a future step"
        detail="Audit service not yet implemented. This is the Step 2 shell.",  # Clear message for development
    )


# ---------------------------------------------------------------------------
# GET /api/v1/audits/{audit_id}
# Retrieve a previously completed audit report by its ID.
# ---------------------------------------------------------------------------

@router.get(
    "/{audit_id}",  # Path parameter; audit_id is extracted from the URL
    response_model=AuditResult,  # Returns the same AuditResult model as the POST endpoint
    status_code=status.HTTP_200_OK,
    summary="Get an audit by ID",
    description="Returns the stored Markdown report for the given audit ID.",
    responses={
        404: {"model": AuditError, "description": "Audit not found"},
    },
)
async def get_audit(audit_id: str) -> AuditResult:
    """
    Retrieve a completed audit report by its unique ID.

    Step 2 stub: Report storage (local reports/ folder) is implemented in Step 10.
    """
    logger.info("Audit retrieval requested for ID: %s", audit_id)  # Log the retrieval attempt

    # Placeholder until the reports/ storage layer is implemented
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Report retrieval not yet implemented for audit_id={audit_id}.",
    )


# ---------------------------------------------------------------------------
# GET /api/v1/audits/{audit_id}/pdf
# Download the PDF report for a completed audit.
# ---------------------------------------------------------------------------

@router.get(
    "/{audit_id}/pdf",  # Nested path under the audit resource
    summary="Download the PDF report",
    description="Streams the generated PDF report as a file download.",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF file download"},  # Correct content type for PDF
        404: {"model": AuditError, "description": "Audit or PDF not found"},
    },
)
async def download_pdf(audit_id: str) -> FileResponse:
    """
    Stream the generated PDF report as a file download.

    Step 2 stub: PDF generation is implemented in Steps 9–10.
    FileResponse is the correct FastAPI response type for binary file downloads.
    """
    logger.info("PDF download requested for audit ID: %s", audit_id)  # Log the download attempt

    # Placeholder until PDF generation and storage are implemented
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"PDF download not yet implemented for audit_id={audit_id}.",
    )
