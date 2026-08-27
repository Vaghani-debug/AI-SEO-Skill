"""
test/test_audit_job_service.py

Unit tests for src/services/audit_job_service.py.

Since _jobs is a module-level in-memory dict, each test uses a unique
audit_id (via create_job's generated uuid) so tests never collide.

Run with:
    pytest test/test_audit_job_service.py -v
"""

import pytest

from src.services.audit_job_service import create_job, get_job, update_job
from src.services.audit_models import AuditJobStatus


class TestCreateJob:
    """Tests for create_job()."""

    def test_returns_a_pending_job_with_a_unique_id(self) -> None:
        job_one = create_job("https://example.com")
        job_two = create_job("https://example.com")

        assert job_one.audit_id != job_two.audit_id
        assert job_one.status == AuditJobStatus.PENDING
        assert job_one.normalized_url == "https://example.com"
        assert job_one.markdown_report is None
        assert job_one.error is None

    def test_created_job_is_immediately_retrievable(self) -> None:
        job = create_job("https://example.com")
        assert get_job(job.audit_id) is job

    def test_supplied_audit_id_is_reused_verbatim(self) -> None:
        """A caller-supplied audit_id (e.g. one shared with report generation) is used as-is."""
        job = create_job("https://example.com", audit_id="fixed-job-id-789")
        assert job.audit_id == "fixed-job-id-789"
        assert get_job("fixed-job-id-789") is job


class TestGetJob:
    """Tests for get_job()."""

    def test_returns_none_for_unknown_audit_id(self) -> None:
        assert get_job("does-not-exist") is None


class TestUpdateJob:
    """Tests for update_job()."""

    def test_updates_only_the_provided_fields(self) -> None:
        job = create_job("https://example.com")
        original_created_at = job.created_at

        updated = update_job(job.audit_id, status=AuditJobStatus.CRAWLING)

        assert updated.status == AuditJobStatus.CRAWLING
        assert updated.created_at == original_created_at
        assert updated.markdown_report is None

    def test_bumps_updated_at_on_every_call(self) -> None:
        job = create_job("https://example.com")
        original_updated_at = job.updated_at

        updated = update_job(job.audit_id, status=AuditJobStatus.CRAWLING)

        assert updated.updated_at >= original_updated_at

    def test_records_markdown_report_on_completion(self) -> None:
        job = create_job("https://example.com")

        updated = update_job(
            job.audit_id,
            status=AuditJobStatus.COMPLETE,
            markdown_report="# Report",
        )

        assert updated.status == AuditJobStatus.COMPLETE
        assert updated.markdown_report == "# Report"

    def test_records_error_on_failure(self) -> None:
        job = create_job("https://example.com")

        updated = update_job(job.audit_id, status=AuditJobStatus.FAILED, error="Could not crawl the website")

        assert updated.status == AuditJobStatus.FAILED
        assert updated.error == "Could not crawl the website"

    def test_raises_key_error_for_unknown_audit_id(self) -> None:
        with pytest.raises(KeyError):
            update_job("does-not-exist", status=AuditJobStatus.CRAWLING)

    def test_updates_are_visible_via_get_job(self) -> None:
        job = create_job("https://example.com")
        update_job(job.audit_id, status=AuditJobStatus.GENERATING)

        assert get_job(job.audit_id).status == AuditJobStatus.GENERATING
