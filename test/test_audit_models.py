"""
test/test_audit_models.py

Unit tests for src/services/audit_models.py.

These are plain dataclass/enum contracts, so tests focus on construction
and default values for the in-process job-tracking model.

Run with:
    pytest test/test_audit_models.py -v
"""

from src.services.audit_models import (
    AuditJob,
    AuditJobStatus,
)


class TestAuditJobStatus:
    """Tests for the AuditJobStatus lifecycle enum."""

    def test_has_expected_members(self) -> None:
        assert {member.value for member in AuditJobStatus} == {
            "pending",
            "crawling",
            "researching",
            "generating",
            "assembling",
            "complete",
            "failed",
        }


class TestAuditJob:
    """Tests for the mutable AuditJob dataclass used for in-process job persistence."""

    def test_defaults_on_creation(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        job = AuditJob(
            audit_id="abc-123",
            normalized_url="https://example.com",
            status=AuditJobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        assert job.markdown_report is None
        assert job.error is None

    def test_status_and_fields_are_mutable_in_place(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        job = AuditJob(
            audit_id="abc-123",
            normalized_url="https://example.com",
            status=AuditJobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

        job.status = AuditJobStatus.CRAWLING
        job.updated_at = datetime.now(timezone.utc)
        assert job.status == AuditJobStatus.CRAWLING

        job.status = AuditJobStatus.COMPLETE
        job.markdown_report = "# Report"
        assert job.status == AuditJobStatus.COMPLETE
        assert job.markdown_report == "# Report"

    def test_failed_job_records_error(self) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        job = AuditJob(
            audit_id="abc-123",
            normalized_url="https://example.com",
            status=AuditJobStatus.FAILED,
            created_at=now,
            updated_at=now,
            error="Could not crawl the website",
        )
        assert job.status == AuditJobStatus.FAILED
        assert job.error == "Could not crawl the website"
