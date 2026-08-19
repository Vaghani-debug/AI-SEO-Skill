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
    POST /api/v1/audits/                — run a full audit and return the report
    GET  /api/v1/audits/{id}             — retrieve a stored audit by ID
    GET  /api/v1/audits/{id}/status      — get the in-process job's current status
    GET  /api/v1/audits/{id}/pdf         — download the PDF report
"""

import json  # json.loads/dumps used to persist audit results as JSON alongside the PDF
import logging  # Standard logging — records every request start, completion, and error
import uuid  # uuid.uuid4 generates one audit_id shared by the job record and the report
from pathlib import Path  # Path used to read/write JSON and PDF files in the reports/ folder

from fastapi import APIRouter, HTTPException, status  # Router, HTTP error helper, status codes
from fastapi.responses import FileResponse  # FileResponse streams the PDF as a binary download

from src.api.models import AuditError, AuditRequest, AuditResult, AuditStatusResult  # Pydantic request/response models
from src.config import get_settings  # Application settings — API key, model name, reports dir
from src.services.audit_job_service import create_job, get_job, update_job  # In-process job tracking (Phase 5)
from src.services.audit_models import AuditJobStatus  # Job lifecycle enum
from src.services.crawl_service import build_site_evidence  # New pipeline: orchestrated multi-page crawl
from src.services.extractor_service import extract  # Extracts verified SEO data from fetched HTML
from src.services.fetch_service import fetch_site  # Fetches homepage, robots.txt, and sitemaps
from src.services.pdf_service import generate_pdf  # Converts Markdown report to a PDF file
from src.services.prompt_loader import PromptContext, load_prompt_context  # Loads guidance files from disk
from src.services.report_service import (  # Report generation — old one-shot flow and the new section pipeline
    ReportIntegrityError,
    ReportResult,
    assemble_and_validate_report,
    build_audit_context,
    generate_report,
    generate_report_sections,
)
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
        "generates a Markdown report using the configured LLM provider, saves a PDF, and returns the "
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
      2. Load prompt, skill, and report docs from disk.
      3-5. Fetch/crawl, extract evidence, and generate the Markdown report
           (legacy one-shot flow or new section pipeline — see
           settings.use_new_report_pipeline and the two _generate_report_*
           helpers below).
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

    # job_audit_id is generated once, here, and threaded into report generation so the
    # same ID identifies both the in-process job record and the finished report (the
    # pipelines below fall back to generating their own ID only if none is supplied,
    # which keeps every existing caller/test that doesn't pass one unaffected).
    job_audit_id: str = str(uuid.uuid4())
    create_job(normalized_url, audit_id=job_audit_id)

    try:
        # --- Step 2: Load prompt, skill, and report guidance -------------------

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

        # --- Steps 3-5: Fetch/crawl, extract evidence, and generate the report --
        #
        # settings.use_new_report_pipeline selects which flow runs. Both flows end
        # in the same ReportResult shape so PDF generation/persistence below never
        # needs to know which pipeline produced it.

        update_job(job_audit_id, status=AuditJobStatus.GENERATING)
        if _settings.use_new_report_pipeline:
            report_result = await _generate_report_new_pipeline(normalized_url, prompt_context, job_audit_id)
        else:
            report_result = await _generate_report_legacy_pipeline(normalized_url, prompt_context, job_audit_id)

        audit_id: str = report_result.audit_id  # Unique ID for this audit — used as filename

        # --- Step 6: Generate the PDF file ------------------------------------

        update_job(job_audit_id, status=AuditJobStatus.RENDERING_PDF)
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

        update_job(
            job_audit_id,
            status=AuditJobStatus.COMPLETE,
            markdown_report=report_result.markdown_report,
            pdf_path=pdf_path,
        )
    except HTTPException as http_error:
        update_job(job_audit_id, status=AuditJobStatus.FAILED, error=str(http_error.detail))
        raise

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
# Private helpers — report generation pipelines
# ---------------------------------------------------------------------------

async def _generate_report_legacy_pipeline(
    normalized_url: str, prompt_context: PromptContext, audit_id: str,
) -> ReportResult:
    """
    Original one-shot flow: fetch the homepage only, extract AuditEvidence,
    and generate the whole report in a single LLM call.
    """
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

    evidence = extract(site)
    # extractor_service parses the HTML and structured files into AuditEvidence
    # This step is always deterministic and never raises

    logger.info(
        "Extraction complete: title=%r, h1_count=%d",
        evidence.page_title,
        len(evidence.h1_tags),
    )

    try:
        return await generate_report(
            normalized_url=normalized_url,
            evidence=evidence,
            prompt_context=prompt_context,
            settings=_settings,
            audit_id=audit_id,
        )
        # report_service substitutes the URL, assembles the system prompt,
        # calls the configured LLM provider, and returns a ReportResult with audit_id and markdown_report
    except ValueError as api_key_error:
        # API key not configured — configuration error
        logger.error("LLM API key error: %s", api_key_error)
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


async def _generate_report_new_pipeline(
    normalized_url: str, prompt_context: PromptContext, audit_id: str,
) -> ReportResult:
    """
    New sampled-crawl + section pipeline: build a full SiteEvidence, score/
    research it into one AuditContext, generate each report section, then
    assemble and validate the whole document.

    A narrative validation failure does not raise — generate_report_sections()
    already retried each section once and substitutes a safe deterministic
    fallback narrative if it still fails, so this just logs the checkpoint's
    remaining issues and returns the assembled Markdown rather than blocking
    the audit on imperfect prose. A deterministic-block integrity failure
    (see ReportIntegrityError) is different: it can only be a pipeline bug,
    so it does raise and this route turns it into a clean 500.
    """
    try:
        site_evidence = await build_site_evidence(normalized_url, _settings)
    except Exception as crawl_error:
        # Unexpected fetch/crawl error — DNS failure, SSL error, etc.
        logger.error("Site crawl failed for %s: %s", normalized_url, crawl_error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not crawl the website: {crawl_error}. Please check the URL and try again.",
        )

    try:
        context = await build_audit_context(normalized_url, site_evidence, _settings, audit_id=audit_id)
        sections = await generate_report_sections(context, prompt_context, _settings)
    except ValueError as api_key_error:
        # API key not configured — configuration error
        logger.error("LLM API key error: %s", api_key_error)
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

    try:
        assembled = assemble_and_validate_report(sections, prompt_context.master_report_structure, context)
    except ReportIntegrityError as integrity_error:
        # A deterministic block failed to survive assembly intact — always a
        # pipeline bug, never an LLM content issue, so this fails the request
        # rather than returning a silently corrupt report.
        logger.error("Report integrity check failed for audit %s: %s", context.audit_id, integrity_error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Report generation failed an internal consistency check. Please try again or contact support.",
        )
    if not assembled.is_valid:
        # Checkpoint status only — the report is still returned to the user
        logger.warning(
            "Assembled report for audit %s has %d validation issue(s): %s",
            context.audit_id, len(assembled.issues), assembled.issues,
        )

    return ReportResult(
        audit_id=context.audit_id,
        normalized_url=normalized_url,
        markdown_report=assembled.markdown_report,
        created_at=context.created_at,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/audits/{audit_id}/status
# Report the in-process job's current lifecycle status (Phase 5 job tracking).
# ---------------------------------------------------------------------------

@router.get(
    "/{audit_id}/status",
    response_model=AuditStatusResult,
    status_code=status.HTTP_200_OK,
    summary="Get an audit job's current status",
    description="Returns the in-process job's current lifecycle status for the given audit ID.",
    responses={
        404: {"model": AuditError, "description": "Audit job not found"},
    },
)
async def get_audit_status(audit_id: str) -> AuditStatusResult:
    """
    Retrieve the in-process job status for an audit.

    Reads the in-memory job record created by start_audit() via
    audit_job_service.create_job()/update_job() — cleared on process restart,
    so a 404 here does not necessarily mean the audit never happened (the
    persisted report may still be retrievable via GET /{audit_id}).
    """
    logger.info("Audit status requested for ID: %s", audit_id)

    job = get_job(audit_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit job not found: {audit_id}. It may not have started yet or the server was restarted.",
        )

    return AuditStatusResult(
        audit_id=job.audit_id,
        url=job.normalized_url,
        status=job.status.value,
        created_at=job.created_at,
        updated_at=job.updated_at,
        error=job.error,
        pdf_download_url=f"/api/v1/audits/{audit_id}/pdf" if job.pdf_path else None,
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
