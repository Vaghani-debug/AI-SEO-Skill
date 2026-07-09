"""
src/api/routes/audit.py

SEO audit API route definitions — Step 10: full pipeline wired.

This module remains thin.  Routes only:
  1. Validate the incoming request (Pydantic handles this automatically)
  2. Delegate to service functions
  3. Return a structured response or raise an HTTPException

No business logic lives here.  All SEO analysis, fetching, LLM calls,
PDF generation, and storage belong to the service layer.

Endpoints:
    POST /api/v1/audits/         — run a full audit and return the report
    GET  /api/v1/audits/{id}     — retrieve a stored audit by ID
    GET  /api/v1/audits/{id}/pdf — download the PDF report
"""

import json  # json.loads/dumps used to persist audit results as JSON alongside the PDF
import logging  # Standard logging — records every request start, completion, and error
from pathlib import Path  # Path used to read/write JSON and PDF files in the reports/ folder

from fastapi import APIRouter, HTTPException, status  # Router, HTTP error helper, status codes
from fastapi.responses import FileResponse  # FileResponse streams the PDF as a binary download

from src.api.models import AuditError, AuditRequest, AuditResult  # Pydantic request/response models
from src.config import get_settings  # Application settings — API key, model name, reports dir
from src.services.extractor_service import extract  # Extracts verified SEO data from fetched HTML
from src.services.fetch_service import fetch_site  # Fetches homepage, robots.txt, and sitemaps
from src.services.pdf_service import generate_pdf  # Converts Markdown report to a PDF file
from src.services.prompt_loader import load_prompt_context  # Loads guidance files from disk
from src.services.report_service import generate_report  # Calls Gemini to write the Markdown report
from src.services.url_service import normalize_and_validate  # Normalises and validates the input URL

# Module-level logger
logger = logging.getLogger(__name__)  # Resolves to "src.api.routes.audit"

# Load settings once at import time — avoids repeated .env file reads per request
_settings = get_settings()

# APIRouter groups all /audits endpoints together for registration in main.py
router = APIRouter(
    prefix="/audits",  # Combined with the /api/v1 prefix in main.py → /api/v1/audits
    tags=["audits"],   # Groups these endpoints under "audits" in the /docs Swagger UI
)


# ---------------------------------------------------------------------------
# POST /api/v1/audits/
# Run a complete SEO audit and return the Markdown report + PDF download URL.
# ---------------------------------------------------------------------------

@router.post(
    "/",
    response_model=AuditResult,               # FastAPI validates and serialises the return value
    status_code=status.HTTP_202_ACCEPTED,      # 202 because the operation may take 20–40 seconds
    summary="Start an SEO audit",
    description=(
        "Accepts a website URL, fetches the site, extracts verified SEO data, "
        "generates a Markdown report using Gemini, saves a PDF, and returns the "
        "completed report with a PDF download URL."
    ),
    responses={
        400: {"model": AuditError, "description": "Invalid URL or unsupported scheme"},
        500: {"model": AuditError, "description": "Unexpected error during audit"},
    },
)
async def start_audit(request: AuditRequest) -> AuditResult:
    """
    Run a complete SEO audit for the provided URL.

    Pipeline:
      1. Validate and normalise the URL.
      2. Fetch homepage, robots.txt, and sitemaps.
      3. Extract verified SEO evidence.
      4. Load prompt, skill, and report docs from disk.
      5. Generate the Markdown report via Gemini.
      6. Generate the PDF file.
      7. Persist the report JSON for later retrieval.
      8. Return the result to the UI.
    """
    logger.info("Audit requested for URL: %s", request.url)  # Log the raw user input

    # --- Step 1: Validate and normalise the URL ----------------------------

    validation = normalize_and_validate(request.url)
    # url_service checks scheme, domain format, and normalises bare domains to https://

    if not validation.is_valid:
        # Return a 400 with the user-facing error message from url_service
        logger.warning("Invalid URL submitted: %r — %s", request.url, validation.error_message)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=validation.error_message,  # Plain-English message safe for display in the UI
        )

    normalized_url: str = validation.normalized_url  # e.g. "https://www.truelinesolution.com"
    logger.info("URL normalised: %r → %r", request.url, normalized_url)

    # --- Step 2: Fetch homepage, robots.txt, and sitemaps ------------------

    try:
        site = await fetch_site(normalized_url, _settings)
        # fetch_service downloads the page and auxiliary files concurrently
        # Fetch failures are recorded in the result rather than raised as exceptions
    except Exception as fetch_error:
        # Unexpected fetch error — DNS failure, SSL error, etc.
        logger.error("Fetch failed for %s: %s", normalized_url, fetch_error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch the website: {fetch_error}. Please check the URL and try again.",
        )

    if not site.homepage.is_success:
        # Homepage returned 4xx or 5xx — still run the audit with partial data
        logger.warning(
            "Homepage returned HTTP %d for %s — continuing with partial evidence",
            site.homepage.status_code,
            normalized_url,
        )
        # We do not abort here: robots.txt and sitemaps may still be useful

    # --- Step 3: Extract verified SEO evidence -----------------------------

    evidence = extract(site)
    # extractor_service parses the HTML and structured files into AuditEvidence
    # This step is always deterministic and never raises

    logger.info(
        "Extraction complete: title=%r, h1_count=%d",
        evidence.page_title,
        len(evidence.h1_tags),
    )

    # --- Step 4: Load prompt, skill, and report guidance -------------------

    try:
        prompt_context = load_prompt_context()
        # Reads seo_audit.prompt.md, SKILL.md, REPORT_SPECIFICATION.md, AI_REPORT_GUIDELINES.md
    except FileNotFoundError as missing_file:
        # A required guidance file is missing — this is a configuration error
        logger.error("Guidance file missing: %s", missing_file)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Server configuration error: {missing_file}. Contact the administrator.",
        )

    # --- Step 5: Generate the Markdown report via Gemini ------------------

    try:
        report_result = await generate_report(
            normalized_url=normalized_url,
            evidence=evidence,
            prompt_context=prompt_context,
            settings=_settings,
        )
        # report_service substitutes the URL, assembles the system prompt,
        # calls Gemini, and returns a ReportResult with audit_id and markdown_report
    except ValueError as api_key_error:
        # API key not configured — configuration error
        logger.error("Gemini API key error: %s", api_key_error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(api_key_error),  # Message says to add the key to .env
        )
    except RuntimeError as llm_error:
        # LLM call failed — network, quota, safety filter, etc.
        logger.error("LLM generation failed for %s: %s", normalized_url, llm_error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Report generation failed: {llm_error}",
        )

    audit_id: str = report_result.audit_id  # Unique ID for this audit — used as filename

    # --- Step 6: Generate the PDF file ------------------------------------

    try:
        pdf_path = generate_pdf(
            audit_id=audit_id,
            normalized_url=normalized_url,
            markdown_report=report_result.markdown_report,
            created_at=report_result.created_at,
            settings=_settings,
        )
        # pdf_service converts the Markdown to a ReportLab PDF and saves it to reports/
    except Exception as pdf_error:
        # PDF generation failure — log but do not abort the audit
        # The user can still see the Markdown report in the UI
        logger.error("PDF generation failed for audit %s: %s", audit_id, pdf_error)
        pdf_path = None  # PDF unavailable; download URL will be omitted

    # --- Step 7: Persist the report JSON for later GET retrieval ----------

    _save_report_json(
        audit_id=audit_id,
        normalized_url=normalized_url,
        markdown_report=report_result.markdown_report,
        created_at=report_result.created_at.isoformat(),
        # isoformat() converts the datetime to a JSON-serialisable string
    )

    # --- Step 8: Return the response to the UI ----------------------------

    pdf_download_url: str = (
        f"/api/v1/audits/{audit_id}/pdf"  # Relative URL the UI uses for the download button
        if pdf_path is not None
        else ""  # Empty string signals to the UI that the PDF is unavailable
    )

    logger.info(
        "Audit complete: audit_id=%s, url=%s, report_length=%d chars",
        audit_id,
        normalized_url,
        len(report_result.markdown_report),
    )

    return AuditResult(
        audit_id=audit_id,                              # UUID for retrieval and PDF download
        url=normalized_url,                             # Normalised URL shown in the UI meta row
        markdown_report=report_result.markdown_report,  # Full Markdown for the UI preview
        pdf_download_url=pdf_download_url,              # Relative path for the PDF download button
        created_at=report_result.created_at,            # Timestamp shown in the UI meta row
    )


# ---------------------------------------------------------------------------
# GET /api/v1/audits/{audit_id}
# Retrieve a previously completed audit report by its ID.
# ---------------------------------------------------------------------------

@router.get(
    "/{audit_id}",
    response_model=AuditResult,
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

    Reads the JSON file persisted by start_audit() from the reports/ folder.
    """
    logger.info("Audit retrieval requested for ID: %s", audit_id)

    data = _load_report_json(audit_id)  # Returns None if the file does not exist

    if data is None:
        # Audit not found — either it never ran or the server was restarted
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit not found: {audit_id}. Run a new audit to generate a report.",
        )

    # Reconstruct the AuditResult from the persisted JSON data
    from datetime import datetime  # Local import to keep module-level imports clean
    return AuditResult(
        audit_id=data["audit_id"],
        url=data["url"],
        markdown_report=data["markdown_report"],
        pdf_download_url=f"/api/v1/audits/{audit_id}/pdf",  # PDF download URL is always derived from the ID
        created_at=datetime.fromisoformat(data["created_at"]),  # Deserialise the ISO timestamp string
    )


# ---------------------------------------------------------------------------
# GET /api/v1/audits/{audit_id}/pdf
# Download the PDF report for a completed audit.
# ---------------------------------------------------------------------------

@router.get(
    "/{audit_id}/pdf",
    summary="Download the PDF report",
    description="Streams the generated PDF report as a file download.",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF file download"},
        404: {"model": AuditError, "description": "Audit or PDF not found"},
    },
)
async def download_pdf(audit_id: str) -> FileResponse:
    """
    Stream the generated PDF report as a file download.

    Reads the PDF file saved by generate_pdf() in the reports/ directory.
    """
    logger.info("PDF download requested for audit ID: %s", audit_id)

    pdf_path = Path(_settings.reports_dir) / f"{audit_id}.pdf"
    # Construct the expected file path from the audit ID

    if not pdf_path.exists():
        # PDF file missing — either generation failed or audit ID is wrong
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"PDF not found for audit: {audit_id}. Run the audit again to regenerate.",
        )

    return FileResponse(
        path=str(pdf_path),           # Path to the PDF file on disk
        media_type="application/pdf", # Correct MIME type for PDF — triggers browser download
        filename=f"seo-audit-{audit_id[:8]}.pdf",  # Suggested download filename shown in the browser dialog
    )


# ---------------------------------------------------------------------------
# Private helpers — report persistence
# ---------------------------------------------------------------------------

def _report_json_path(audit_id: str) -> Path:
    """Return the path to the JSON persistence file for the given audit ID."""
    return Path(_settings.reports_dir) / f"{audit_id}.json"
    # Stored alongside the PDF: reports/{audit_id}.json and reports/{audit_id}.pdf


def _save_report_json(
    audit_id: str,
    normalized_url: str,
    markdown_report: str,
    created_at: str,
) -> None:
    """
    Persist the audit result to a JSON file for later GET retrieval.

    This lightweight local storage avoids a database dependency while
    still allowing reports to be fetched by ID after the POST response.

    Args:
        audit_id: Unique audit identifier.
        normalized_url: The audited URL.
        markdown_report: Full Markdown text of the report.
        created_at: ISO-format timestamp string.
    """
    import os  # Local import — only needed in this helper
    os.makedirs(_settings.reports_dir, exist_ok=True)  # Ensure the directory exists

    data = {
        "audit_id": audit_id,
        "url": normalized_url,
        "markdown_report": markdown_report,
        "created_at": created_at,
    }

    path = _report_json_path(audit_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # ensure_ascii=False preserves non-ASCII characters (e.g. accented letters in the report)
    # indent=2 makes the JSON file human-readable for debugging

    logger.debug("Report JSON persisted: %s", path)


def _load_report_json(audit_id: str) -> dict | None:
    """
    Load a persisted audit result from disk.

    Args:
        audit_id: The audit identifier to retrieve.

    Returns:
        The parsed JSON dict, or None if the file does not exist.
    """
    path = _report_json_path(audit_id)

    if not path.exists():
        return None  # File not found — audit was never run or server was restarted

    try:
        return json.loads(path.read_text(encoding="utf-8"))
        # json.loads parses the JSON string back into a Python dict
    except (json.JSONDecodeError, OSError) as read_error:
        logger.error("Failed to read report JSON for %s: %s", audit_id, read_error)
        return None  # Treat corrupted files the same as missing files



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
