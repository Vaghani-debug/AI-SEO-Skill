"""
test/test_audit_api.py

Integration tests for the SEO audit API endpoints.

Tests cover all three routes:
    POST /api/v1/audits/         — start audit (success + error paths)
    GET  /api/v1/audits/{id}     — retrieve a stored audit
    GET  /api/v1/audits/{id}/pdf — download the PDF report
    GET  /health                 — liveness check

All five services (url_service, fetch_service, extractor_service,
report_service, pdf_service) and prompt_loader are mocked so these tests
run fully offline and never call Gemini or make real HTTP requests.

Run with:
    pytest test/test_audit_api.py -v
"""

import json  # Used to write fixture JSON files for the GET retrieval tests
import re  # Used to extract PART/SECTION headings from LLM user_message text in fake_generate_text
import uuid  # Used to pin start_audit()'s generated job/audit_id in job-tracking tests
from datetime import datetime, timezone  # For constructing fixture ReportResult objects
from pathlib import Path  # Used to create fixture PDF and JSON files in tmp_path
from unittest.mock import AsyncMock, MagicMock, patch  # All mocking tools needed

import pytest  # Test runner
from fastapi.testclient import TestClient  # Synchronous HTTP test client for FastAPI

from src.main import app  # The FastAPI application under test
from src.services.prompt_loader import PromptContext  # Used to build a real (non-mocked) template for the new pipeline
from test.fixtures.frozen_audit_context import build_frozen_audit_context  # Step 18's frozen, anonymized AuditContext


# ---------------------------------------------------------------------------
# TestClient fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    """Return a synchronous FastAPI TestClient backed by the real app."""
    return TestClient(app, raise_server_exceptions=False)
    # raise_server_exceptions=False: 5xx responses are returned as responses,
    # not raised as Python exceptions — lets us assert on error status codes directly


# ---------------------------------------------------------------------------
# Shared mock factories
# ---------------------------------------------------------------------------

def _make_site_fetch_result() -> MagicMock:
    """Return a minimal mock SiteFetchResult representing a successful fetch."""
    from src.services.fetch_service import FetchedResource, SiteFetchResult

    homepage = FetchedResource(
        url="https://example.com",
        label="homepage",
        final_url="https://example.com",
        status_code=200,
        content="<html><head><title>Example</title></head><body><h1>Hello</h1></body></html>",
        is_success=True,
        is_fetched=True,
    )
    robots = FetchedResource(
        url="https://example.com/robots.txt",
        label="robots.txt",
        final_url="https://example.com/robots.txt",
        status_code=200,
        content="User-agent: *\nDisallow:\n",
        is_success=True,
        is_fetched=True,
    )
    sitemap = FetchedResource(
        url="https://example.com/sitemap.xml",
        label="sitemap.xml",
        final_url="https://example.com/sitemap.xml",
        status_code=404,
        content="",
        is_success=False,
        is_fetched=True,
    )
    return SiteFetchResult(
        base_url="https://example.com",
        homepage=homepage,
        robots_txt=robots,
        sitemap_xml=sitemap,
    )


def _make_report_result(audit_id: str = "test-audit-id-001") -> MagicMock:
    """Return a minimal mock ReportResult from the report_service."""
    from src.services.report_service import ReportResult

    return ReportResult(
        audit_id=audit_id,
        normalized_url="https://example.com",
        markdown_report="# SEO Audit Report\n\n## Executive Summary\n\nGood site.",
        created_at=datetime(2026, 7, 9, 14, 0, 0, tzinfo=timezone.utc),
    )


def _mock_pdf_path(tmp_path: Path, audit_id: str) -> Path:
    """Create a minimal placeholder PDF file and return its path."""
    pdf = tmp_path / "reports" / f"{audit_id}.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 minimal test pdf content")  # Valid PDF magic bytes
    return pdf


def _mock_json_path(tmp_path: Path, audit_id: str, url: str = "https://example.com") -> Path:
    """Create a report JSON file and return its path."""
    json_path = tmp_path / "reports" / f"{audit_id}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "audit_id": audit_id,
        "url": url,
        "markdown_report": "# SEO Report\n\nTest content.",
        "created_at": "2026-07-09T14:00:00+00:00",
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")
    return json_path


# A real (non-mocked) PromptContext with a simplified template covering every
# _SECTION_GROUPS heading, used only by the real deterministic-block pipeline test below —
# not the full production MASTER_REPORT_STRUCTURE.md, whose SECTION 3 sub-heading wording
# is unrelated to what this test verifies.
_NEW_PIPELINE_PROMPT_CONTEXT = PromptContext(
    audit_prompt="Audit {{website_url}}.",
    seo_skill="Priority: Crawlability, Technical, On-Page, Content.",
    master_report_structure=(
        "# PART 1: FULL WEBSITE AUDIT\n\nBody.\n\n"
        "# PART 2: TECHNICAL SEO AUDIT\n\nBody.\n\n"
        "# PART 3: ON-PAGE & CONTENT AUDIT\n\nBody.\n\n"
        "# SECTION 1: KEYWORD OPPORTUNITY STRATEGY\n\nBody.\n\n"
        "# SECTION 2: COMPETITOR ANALYSIS\n\nBody.\n\n"
        "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n"
        "## 3.1 Applicability Assessment\nBody.\n"
        "## 3.2 Local Location Opportunities\nBody.\n"
        "## 3.3 Audience & Market Expansion Opportunities\nNot applicable.\n\n"
        "# SECTION 4: STRUCTURED DATA RECOMMENDATIONS\n\nBody.\n\n"
        "# SECTION 5: OFF-PAGE SEO & GEO STRATEGY\n\nBody.\n"
    ),
    ai_guidelines="Never invent findings. Use verified evidence only.",
)


# ---------------------------------------------------------------------------
# Helper: patch all five services for a successful full-pipeline run
# ---------------------------------------------------------------------------

def _patch_full_pipeline(tmp_path: Path, audit_id: str = "test-audit-id-001"):
    """
    Return a context manager that patches all five services to simulate
    a complete successful audit without any real network or LLM calls.
    """
    import contextlib

    pdf_path = _mock_pdf_path(tmp_path, audit_id)  # Create the PDF file before the test runs
    report = _make_report_result(audit_id)

    @contextlib.contextmanager
    def _ctx():
        with (
            patch(
                "src.api.routes.audit._settings.reports_dir",
                str(tmp_path / "reports"),
            ),
            patch(
                "src.api.routes.audit.fetch_site",
                new=AsyncMock(return_value=_make_site_fetch_result()),
            ),
            patch(
                "src.api.routes.audit.extract",
                return_value=MagicMock(),  # AuditEvidence mock — extractor output
            ),
            patch(
                "src.api.routes.audit.load_prompt_context",
                return_value=MagicMock(),  # PromptContext mock
            ),
            patch(
                "src.api.routes.audit.generate_report",
                new=AsyncMock(return_value=report),
            ),
            patch(
                "src.api.routes.audit.generate_pdf",
                return_value=pdf_path,  # Return the pre-created PDF path
            ),
        ):
            yield

    return _ctx()


# ---------------------------------------------------------------------------
# Helper: patch all services for a successful new-pipeline run
# ---------------------------------------------------------------------------

def _patch_new_pipeline(tmp_path: Path, audit_id: str = "test-audit-id-002"):
    """
    Return a context manager that patches the new sampled-crawl + section
    pipeline (build_site_evidence/build_audit_context/generate_report_sections/
    assemble_and_validate_report) to simulate a complete successful audit
    without any real network, crawl, or LLM calls.
    """
    import contextlib

    pdf_path = _mock_pdf_path(tmp_path, audit_id)

    context = MagicMock()
    context.audit_id = audit_id
    context.created_at = datetime(2026, 7, 9, 14, 0, 0, tzinfo=timezone.utc)

    assembled = MagicMock()
    assembled.markdown_report = "# SEO Audit Report\n\n## Executive Summary\n\nGood site."
    assembled.issues = []
    assembled.is_valid = True

    @contextlib.contextmanager
    def _ctx():
        with (
            patch(
                "src.api.routes.audit._settings.reports_dir",
                str(tmp_path / "reports"),
            ),
            patch("src.api.routes.audit._settings.use_new_report_pipeline", True),
            patch(
                "src.api.routes.audit.load_prompt_context",
                return_value=MagicMock(),  # PromptContext mock
            ),
            patch(
                "src.api.routes.audit.build_site_evidence",
                new=AsyncMock(return_value=MagicMock()),  # SiteEvidence mock
            ),
            patch(
                "src.api.routes.audit.build_audit_context",
                new=AsyncMock(return_value=context),
            ),
            patch(
                "src.api.routes.audit.generate_report_sections",
                new=AsyncMock(return_value={"site_inventory": "..."}),
            ),
            patch(
                "src.api.routes.audit.assemble_and_validate_report",
                return_value=assembled,
            ),
            patch(
                "src.api.routes.audit.generate_pdf",
                return_value=pdf_path,  # Return the pre-created PDF path
            ),
        ):
            yield

    return _ctx()


class TestStartAuditNewPipeline:
    """Tests for the new section pipeline, enabled via settings.use_new_report_pipeline."""

    def test_returns_202_with_assembled_report(self, client: TestClient, tmp_path: Path) -> None:
        """A valid URL returns HTTP 202 with the assembled report's Markdown and audit ID."""
        with _patch_new_pipeline(tmp_path):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        assert response.status_code == 202
        data = response.json()
        assert data["audit_id"] == "test-audit-id-002"
        assert "Executive Summary" in data["markdown_report"]

    def test_legacy_fetch_is_not_called(self, client: TestClient, tmp_path: Path) -> None:
        """The old fetch_site()/extract() flow is skipped entirely when the flag is on."""
        with _patch_new_pipeline(tmp_path), patch("src.api.routes.audit.fetch_site") as fetch_mock:
            client.post("/api/v1/audits/", json={"url": "https://example.com"})
        fetch_mock.assert_not_called()

    def test_invalid_validation_issues_do_not_block_the_response(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """A checkpoint failure (is_valid=False) still returns the assembled report, not an error."""
        with _patch_new_pipeline(tmp_path) as _, patch(
            "src.api.routes.audit.assemble_and_validate_report",
            return_value=MagicMock(
                markdown_report="# SEO Audit Report\n\nPartial content.",
                issues=["Missing required PART headings: PART 9"],
                is_valid=False,
            ),
        ):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        assert response.status_code == 202
        assert "Partial content" in response.json()["markdown_report"]

    def test_new_pipeline_report_is_built_from_real_deterministic_blocks(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """
        End-to-end through the real generate_report_sections()/assemble_and_validate_report(),
        fed Step 18's frozen AuditContext, with only the underlying LLM call mocked. Proves the
        assembled report's factual content (page URLs, robots directive, competitor name,
        keyword text) comes from real deterministic renderers/injection over real evidence —
        not merely whatever text a mocked ReportResult/assembled object happened to contain.
        """
        audit_id = "frozen-fixture-audit-0001"
        pdf_path = _mock_pdf_path(tmp_path, audit_id)
        context = build_frozen_audit_context()

        async def fake_generate_text(system_prompt: str, user_message: str, settings) -> str:
            headings = re.findall(r"# (?:PART|SECTION) \d+:[^\n]*", user_message)
            if any(h.startswith("# SECTION 3:") for h in headings):
                return (
                    "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY\n\n"
                    "## 3.1 Applicability Assessment\n\nLocal business serving Austin, TX.\n\n"
                    "## 3.2 Local Location Opportunities\n\nGenerated narrative.\n\n"
                    "## 3.3 Audience & Market Expansion Opportunities\n\nNot applicable.\n"
                )
            return "\n\n".join(f"{h}\n\nGenerated narrative for this section." for h in headings)

        with (
            patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")),
            patch("src.api.routes.audit._settings.use_new_report_pipeline", True),
            patch("src.api.routes.audit.load_prompt_context", return_value=_NEW_PIPELINE_PROMPT_CONTEXT),
            patch("src.api.routes.audit.build_site_evidence", new=AsyncMock(return_value=MagicMock())),
            patch("src.api.routes.audit.build_audit_context", new=AsyncMock(return_value=context)),
            patch("src.services.report_service.generate_text", side_effect=fake_generate_text),
            patch("src.api.routes.audit.generate_pdf", return_value=pdf_path),
        ):
            response = client.post("/api/v1/audits/", json={"url": "https://sample-bakery-co.test"})

        assert response.status_code == 202
        markdown_report = response.json()["markdown_report"]

        # Deterministic block proof — none of this text is in fake_generate_text's output.
        assert "https://sample-bakery-co.test/services/custom-cakes" in markdown_report  # Core/Subpages Table
        assert "/cart/" in markdown_report  # robots.txt Disallow rule, rendered verbatim
        assert "Anonymized Competitor Bakery" in markdown_report  # Competitor Overview Table
        assert "artisan sourdough bread austin" in markdown_report  # Primary Keywords Table


# ---------------------------------------------------------------------------
# POST /api/v1/audits/ — success cases
# ---------------------------------------------------------------------------

class TestStartAuditSuccess:
    """Tests for successful audit submissions."""

    def test_returns_202_accepted(self, client: TestClient, tmp_path: Path) -> None:
        """A valid URL returns HTTP 202 Accepted."""
        with _patch_full_pipeline(tmp_path):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        assert response.status_code == 202  # 202 Accepted for async-style operations

    def test_legacy_pipeline_never_calls_new_pipeline_functions(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """
        With use_new_report_pipeline explicitly False (the shipped default), the legacy
        one-shot flow runs unchanged and none of the new sampled-crawl/section-generation
        functions are ever touched.
        """
        with (
            _patch_full_pipeline(tmp_path),
            patch("src.api.routes.audit._settings.use_new_report_pipeline", False),
            patch("src.api.routes.audit.build_site_evidence") as build_site_evidence_mock,
            patch("src.api.routes.audit.build_audit_context") as build_audit_context_mock,
            patch("src.api.routes.audit.generate_report_sections") as generate_sections_mock,
            patch("src.api.routes.audit.assemble_and_validate_report") as assemble_mock,
        ):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})

        assert response.status_code == 202
        build_site_evidence_mock.assert_not_called()
        build_audit_context_mock.assert_not_called()
        generate_sections_mock.assert_not_called()
        assemble_mock.assert_not_called()

    def test_response_contains_audit_id(self, client: TestClient, tmp_path: Path) -> None:
        """The response body contains a non-empty audit_id."""
        with _patch_full_pipeline(tmp_path):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        data = response.json()
        assert "audit_id" in data          # Field present
        assert data["audit_id"]            # Non-empty string

    def test_response_contains_markdown_report(self, client: TestClient, tmp_path: Path) -> None:
        """The response body contains the Markdown report text."""
        with _patch_full_pipeline(tmp_path):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        data = response.json()
        assert "markdown_report" in data
        assert len(data["markdown_report"]) > 0  # Report is not empty

    def test_response_contains_pdf_download_url(self, client: TestClient, tmp_path: Path) -> None:
        """The response body includes a PDF download URL."""
        with _patch_full_pipeline(tmp_path):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        data = response.json()
        assert "pdf_download_url" in data
        assert "/pdf" in data["pdf_download_url"]  # URL ends with /pdf

    def test_response_contains_normalised_url(self, client: TestClient, tmp_path: Path) -> None:
        """The response url field contains the normalised website URL."""
        with _patch_full_pipeline(tmp_path):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        data = response.json()
        assert "url" in data
        assert "example.com" in data["url"]

    def test_bare_domain_is_accepted(self, client: TestClient, tmp_path: Path) -> None:
        """A bare domain like 'www.example.com' is accepted and normalised."""
        with _patch_full_pipeline(tmp_path):
            response = client.post("/api/v1/audits/", json={"url": "www.example.com"})
        assert response.status_code == 202  # Bare domain normalised and accepted

    def test_report_json_persisted_to_disk(self, client: TestClient, tmp_path: Path) -> None:
        """After a successful audit, a JSON file is saved in the reports directory."""
        with _patch_full_pipeline(tmp_path):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        audit_id = response.json()["audit_id"]
        json_file = tmp_path / "reports" / f"{audit_id}.json"
        assert json_file.exists()  # Persisted for GET retrieval


# ---------------------------------------------------------------------------
# POST /api/v1/audits/ — validation error cases
# ---------------------------------------------------------------------------

class TestStartAuditValidationErrors:
    """Tests for invalid URL submissions."""

    def test_empty_url_returns_422(self, client: TestClient) -> None:
        """An empty url field returns 422 Unprocessable Entity (Pydantic min_length check)."""
        response = client.post("/api/v1/audits/", json={"url": ""})
        assert response.status_code == 422  # Pydantic validation: min_length=3 fails

    def test_missing_url_field_returns_422(self, client: TestClient) -> None:
        """A request with no url field returns 422."""
        response = client.post("/api/v1/audits/", json={})
        assert response.status_code == 422

    def test_ftp_scheme_returns_400(self, client: TestClient) -> None:
        """An ftp:// URL returns 400 Bad Request from url_service validation."""
        response = client.post("/api/v1/audits/", json={"url": "ftp://example.com"})
        assert response.status_code == 400  # url_service rejects unsupported scheme

    def test_invalid_domain_returns_400(self, client: TestClient) -> None:
        """A URL with no valid domain returns 400."""
        response = client.post("/api/v1/audits/", json={"url": "https://"})
        assert response.status_code == 400

    def test_error_detail_is_user_friendly(self, client: TestClient) -> None:
        """The 400 error detail is a plain-English message, not a traceback."""
        response = client.post("/api/v1/audits/", json={"url": "ftp://example.com"})
        detail = response.json().get("detail", "")
        assert "Traceback" not in detail    # No stack trace leaked
        assert "Exception" not in detail    # No Python exception class names
        assert len(detail) > 5             # A real message, not just a code


# ---------------------------------------------------------------------------
# POST /api/v1/audits/ — service error cases
# ---------------------------------------------------------------------------

class TestStartAuditServiceErrors:
    """Tests for downstream service failures."""

    def test_missing_api_key_returns_500(self, client: TestClient, tmp_path: Path) -> None:
        """A missing Gemini API key results in a 500 Internal Server Error."""
        with (
            patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")),
            patch("src.api.routes.audit.fetch_site", new=AsyncMock(return_value=_make_site_fetch_result())),
            patch("src.api.routes.audit.extract", return_value=MagicMock()),
            patch("src.api.routes.audit.load_prompt_context", return_value=MagicMock()),
            patch(
                "src.api.routes.audit.generate_report",
                new=AsyncMock(side_effect=ValueError("GEMINI_API_KEY is not configured.")),
            ),
        ):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        assert response.status_code == 500  # Configuration error → 500

    def test_llm_failure_returns_502(self, client: TestClient, tmp_path: Path) -> None:
        """An LLM generation failure results in a 502 Bad Gateway."""
        with (
            patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")),
            patch("src.api.routes.audit.fetch_site", new=AsyncMock(return_value=_make_site_fetch_result())),
            patch("src.api.routes.audit.extract", return_value=MagicMock()),
            patch("src.api.routes.audit.load_prompt_context", return_value=MagicMock()),
            patch(
                "src.api.routes.audit.generate_report",
                new=AsyncMock(side_effect=RuntimeError("LLM report generation failed.")),
            ),
        ):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        assert response.status_code == 502  # Upstream LLM error → 502

    def test_fetch_failure_returns_502(self, client: TestClient, tmp_path: Path) -> None:
        """A network-level fetch failure results in a 502 Bad Gateway."""
        with (
            patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")),
            patch(
                "src.api.routes.audit.fetch_site",
                new=AsyncMock(side_effect=Exception("DNS resolution failed")),
            ),
        ):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        assert response.status_code == 502  # Network error → 502

    def test_missing_guidance_file_returns_500(self, client: TestClient, tmp_path: Path) -> None:
        """A missing guidance file (FileNotFoundError) returns 500."""
        with (
            patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")),
            patch("src.api.routes.audit.fetch_site", new=AsyncMock(return_value=_make_site_fetch_result())),
            patch("src.api.routes.audit.extract", return_value=MagicMock()),
            patch(
                "src.api.routes.audit.load_prompt_context",
                side_effect=FileNotFoundError("seo_audit.prompt.md not found"),
            ),
        ):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        assert response.status_code == 500  # Configuration error → 500

    def test_pdf_failure_does_not_abort_audit(self, client: TestClient, tmp_path: Path) -> None:
        """A PDF generation failure does not abort the audit — report is still returned."""
        audit_id = "pdf-fail-test"
        report = _make_report_result(audit_id)
        with (
            patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")),
            patch("src.api.routes.audit.fetch_site", new=AsyncMock(return_value=_make_site_fetch_result())),
            patch("src.api.routes.audit.extract", return_value=MagicMock()),
            patch("src.api.routes.audit.load_prompt_context", return_value=MagicMock()),
            patch("src.api.routes.audit.generate_report", new=AsyncMock(return_value=report)),
            patch(
                "src.api.routes.audit.generate_pdf",
                side_effect=Exception("ReportLab failed"),  # PDF fails
            ),
        ):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        # Report still returns 202 — PDF failure is non-fatal
        assert response.status_code == 202
        assert response.json()["markdown_report"]  # Markdown still present
        assert response.json()["pdf_download_url"] == ""  # Empty URL when PDF unavailable


# ---------------------------------------------------------------------------
# POST /api/v1/audits/ — in-process job tracking (Phase 5)
# ---------------------------------------------------------------------------

class TestStartAuditJobTracking:
    """Tests confirming start_audit() creates and updates an in-process job record."""

    def test_job_reaches_complete_status_after_a_successful_audit(
        self, client: TestClient, tmp_path: Path,
    ) -> None:
        """A successful audit's job record transitions through to COMPLETE."""
        from src.services.audit_job_service import get_job
        from src.services.audit_models import AuditJobStatus

        fixed_id = uuid.uuid4()
        with (
            _patch_full_pipeline(tmp_path),
            patch("src.api.routes.audit.uuid.uuid4", return_value=fixed_id),
        ):
            client.post("/api/v1/audits/", json={"url": "https://example.com"})

        job = get_job(str(fixed_id))
        assert job is not None
        assert job.status == AuditJobStatus.COMPLETE
        assert job.markdown_report  # Non-empty Markdown recorded on the job

    def test_job_reaches_failed_status_after_a_pipeline_error(
        self, client: TestClient, tmp_path: Path,
    ) -> None:
        """A downstream failure still records a job, marked FAILED with the error message."""
        from src.services.audit_job_service import get_job
        from src.services.audit_models import AuditJobStatus

        fixed_id = uuid.uuid4()
        with (
            patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")),
            patch("src.api.routes.audit.fetch_site", new=AsyncMock(return_value=_make_site_fetch_result())),
            patch("src.api.routes.audit.extract", return_value=MagicMock()),
            patch(
                "src.api.routes.audit.load_prompt_context",
                side_effect=FileNotFoundError("seo_audit.prompt.md not found"),
            ),
            patch("src.api.routes.audit.uuid.uuid4", return_value=fixed_id),
        ):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})

        assert response.status_code == 500
        job = get_job(str(fixed_id))
        assert job is not None
        assert job.status == AuditJobStatus.FAILED
        assert job.error  # Non-empty error message recorded


# ---------------------------------------------------------------------------
# GET /api/v1/audits/{audit_id}/status — in-process job status
# ---------------------------------------------------------------------------

class TestGetAuditStatus:
    """Tests for the job status endpoint."""

    def test_known_job_returns_200_with_status(self, client: TestClient) -> None:
        """A job created via create_job() is retrievable through the status endpoint."""
        from src.services.audit_job_service import create_job

        job = create_job("https://example.com")
        response = client.get(f"/api/v1/audits/{job.audit_id}/status")
        assert response.status_code == 200
        data = response.json()
        assert data["audit_id"] == job.audit_id
        assert data["status"] == "pending"
        assert data["url"] == "https://example.com"
        assert data["error"] is None
        assert data["pdf_download_url"] is None

    def test_completed_job_includes_pdf_download_url(self, client: TestClient) -> None:
        """A COMPLETE job with a pdf_path exposes a pdf_download_url."""
        from src.services.audit_job_service import create_job, update_job
        from src.services.audit_models import AuditJobStatus

        job = create_job("https://example.com")
        update_job(job.audit_id, status=AuditJobStatus.COMPLETE, pdf_path="/reports/x.pdf")
        response = client.get(f"/api/v1/audits/{job.audit_id}/status")
        data = response.json()
        assert data["status"] == "complete"
        assert data["pdf_download_url"] == f"/api/v1/audits/{job.audit_id}/pdf"

    def test_failed_job_includes_error_message(self, client: TestClient) -> None:
        """A FAILED job's error message is exposed in the status response."""
        from src.services.audit_job_service import create_job, update_job
        from src.services.audit_models import AuditJobStatus

        job = create_job("https://example.com")
        update_job(job.audit_id, status=AuditJobStatus.FAILED, error="Something went wrong")
        response = client.get(f"/api/v1/audits/{job.audit_id}/status")
        data = response.json()
        assert data["status"] == "failed"
        assert data["error"] == "Something went wrong"

    def test_unknown_audit_id_returns_404(self, client: TestClient) -> None:
        """An audit_id with no in-process job record returns 404 Not Found."""
        response = client.get("/api/v1/audits/does-not-exist/status")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/audits/{audit_id} — retrieval
# ---------------------------------------------------------------------------

class TestGetAudit:
    """Tests for the audit retrieval endpoint."""

    def test_known_id_returns_200(self, client: TestClient, tmp_path: Path) -> None:
        """A valid audit_id with a persisted JSON file returns 200 OK."""
        audit_id = "known-audit-id"
        _mock_json_path(tmp_path, audit_id)
        with patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")):
            response = client.get(f"/api/v1/audits/{audit_id}")
        assert response.status_code == 200

    def test_known_id_returns_correct_report(self, client: TestClient, tmp_path: Path) -> None:
        """The retrieved report matches the persisted JSON content."""
        audit_id = "retrieve-test"
        _mock_json_path(tmp_path, audit_id, url="https://example.com")
        with patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")):
            response = client.get(f"/api/v1/audits/{audit_id}")
        data = response.json()
        assert data["audit_id"] == audit_id            # Correct ID
        assert "example.com" in data["url"]            # Correct URL
        assert data["markdown_report"]                 # Non-empty report

    def test_unknown_id_returns_404(self, client: TestClient, tmp_path: Path) -> None:
        """An audit_id with no persisted file returns 404 Not Found."""
        with patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")):
            response = client.get("/api/v1/audits/does-not-exist")
        assert response.status_code == 404

    def test_404_detail_is_user_friendly(self, client: TestClient, tmp_path: Path) -> None:
        """The 404 error detail message is plain English."""
        with patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")):
            response = client.get("/api/v1/audits/missing-id")
        detail = response.json().get("detail", "")
        assert len(detail) > 10     # A real message
        assert "Traceback" not in detail


# ---------------------------------------------------------------------------
# GET /api/v1/audits/{audit_id}/pdf — download
# ---------------------------------------------------------------------------

class TestDownloadPdf:
    """Tests for the PDF download endpoint."""

    def test_known_id_with_pdf_returns_200(self, client: TestClient, tmp_path: Path) -> None:
        """A valid audit_id with an existing PDF file returns 200 OK."""
        audit_id = "pdf-download-test"
        _mock_pdf_path(tmp_path, audit_id)
        with patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")):
            response = client.get(f"/api/v1/audits/{audit_id}/pdf")
        assert response.status_code == 200

    def test_pdf_response_has_correct_content_type(self, client: TestClient, tmp_path: Path) -> None:
        """The PDF download response has Content-Type: application/pdf."""
        audit_id = "pdf-content-type-test"
        _mock_pdf_path(tmp_path, audit_id)
        with patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")):
            response = client.get(f"/api/v1/audits/{audit_id}/pdf")
        assert "application/pdf" in response.headers.get("content-type", "")

    def test_pdf_response_has_content_disposition_header(self, client: TestClient, tmp_path: Path) -> None:
        """The PDF response includes a Content-Disposition header for the download dialog."""
        audit_id = "pdf-header-test"
        _mock_pdf_path(tmp_path, audit_id)
        with patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")):
            response = client.get(f"/api/v1/audits/{audit_id}/pdf")
        # Content-Disposition header tells the browser to save, not display
        assert "content-disposition" in response.headers

    def test_unknown_id_returns_404(self, client: TestClient, tmp_path: Path) -> None:
        """An audit_id with no PDF file returns 404 Not Found."""
        with patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")):
            response = client.get("/api/v1/audits/no-such-pdf/pdf")
        assert response.status_code == 404

    def test_pdf_body_starts_with_pdf_magic_bytes(self, client: TestClient, tmp_path: Path) -> None:
        """The downloaded file starts with the PDF magic bytes %%PDF-."""
        audit_id = "pdf-bytes-test"
        _mock_pdf_path(tmp_path, audit_id)
        with patch("src.api.routes.audit._settings.reports_dir", str(tmp_path / "reports")):
            response = client.get(f"/api/v1/audits/{audit_id}/pdf")
        assert response.content[:5] == b"%PDF-"  # Valid PDF signature


# ---------------------------------------------------------------------------
# GET /health — liveness check
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """Tests for the /health liveness endpoint."""

    def test_health_returns_200(self, client: TestClient) -> None:
        """GET /health returns HTTP 200 OK."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok_status(self, client: TestClient) -> None:
        """The health response body contains status: ok."""
        response = client.get("/health")
        assert response.json()["status"] == "ok"

    def test_health_returns_version(self, client: TestClient) -> None:
        """The health response includes the application version."""
        response = client.get("/health")
        assert "version" in response.json()

    def test_health_returns_app_name(self, client: TestClient) -> None:
        """The health response includes the application name."""
        response = client.get("/health")
        assert "app" in response.json()
