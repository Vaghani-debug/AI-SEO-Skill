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
from datetime import datetime, timezone  # For constructing fixture ReportResult objects
from pathlib import Path  # Used to create fixture PDF and JSON files in tmp_path
from unittest.mock import AsyncMock, MagicMock, patch  # All mocking tools needed

import pytest  # Test runner
from fastapi.testclient import TestClient  # Synchronous HTTP test client for FastAPI

from src.main import app  # The FastAPI application under test


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
# POST /api/v1/audits/ — success cases
# ---------------------------------------------------------------------------

class TestStartAuditSuccess:
    """Tests for successful audit submissions."""

    def test_returns_202_accepted(self, client: TestClient, tmp_path: Path) -> None:
        """A valid URL returns HTTP 202 Accepted."""
        with _patch_full_pipeline(tmp_path):
            response = client.post("/api/v1/audits/", json={"url": "https://example.com"})
        assert response.status_code == 202  # 202 Accepted for async-style operations

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
