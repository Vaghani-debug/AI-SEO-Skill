"""
src/services/audit_job_service.py

In-process audit job store (Phase 5).

Responsibility: create, update, and retrieve AuditJob records so a status
endpoint can report real progress instead of the caller blocking until an
entire audit pipeline finishes. This is deliberately an in-process,
in-memory store — Redis/Celery-backed persistence is an explicitly
deferred future phase (see AGENTS.md / plan.md Phase 5 decisions).

Public interface:
    create_job(normalized_url: str, audit_id: str | None = None) -> AuditJob
    get_job(audit_id: str) -> AuditJob | None
    update_job(audit_id: str, **fields) -> AuditJob
"""

import logging  # Standard logging — records job creation and every status transition
import uuid  # uuid.uuid4 generates each job's unique audit_id
from datetime import datetime, timezone  # datetime.now(timezone.utc) for timezone-aware UTC timestamps

from src.services.audit_models import AuditJob, AuditJobStatus

logger = logging.getLogger(__name__)  # __name__ resolves to "src.services.audit_job_service"

# In-memory store keyed by audit_id. Cleared on process restart — acceptable
# for the current in-process MVP; a future phase may back this with a file
# or database so jobs survive a restart.
_jobs: dict[str, AuditJob] = {}


def create_job(normalized_url: str, audit_id: str | None = None) -> AuditJob:
    """
    Create and store a new AuditJob in PENDING status.

    Args:
        normalized_url: The validated URL this job will audit.
        audit_id: Pre-generated ID to use as the job's key (e.g. one the
            caller will also pass into report generation so the same ID
            identifies both the job and the finished report); a new one
            is generated if not supplied.

    Returns:
        The newly created AuditJob, already stored and retrievable via get_job().
    """
    now = datetime.now(timezone.utc)
    job = AuditJob(
        audit_id=audit_id or str(uuid.uuid4()),
        normalized_url=normalized_url,
        status=AuditJobStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    _jobs[job.audit_id] = job
    logger.info("Created audit job %s for %s", job.audit_id, normalized_url)
    return job


def get_job(audit_id: str) -> AuditJob | None:
    """Return the AuditJob for audit_id, or None if no such job exists."""
    return _jobs.get(audit_id)


def update_job(
    audit_id: str,
    *,
    status: AuditJobStatus | None = None,
    markdown_report: str | None = None,
    pdf_path: str | None = None,
    error: str | None = None,
) -> AuditJob:
    """
    Update an existing AuditJob in place and bump its updated_at timestamp.

    Only the fields explicitly passed are changed; omitted fields keep
    their current value.

    Args:
        audit_id: The job to update.
        status: New lifecycle phase, if transitioning.
        markdown_report: The assembled report, once available.
        pdf_path: The rendered PDF's path, once available.
        error: Human-readable failure reason, once the job has failed.

    Returns:
        The updated AuditJob.

    Raises:
        KeyError: If no job exists for audit_id.
    """
    job = _jobs.get(audit_id)
    if job is None:
        raise KeyError(f"No audit job found for audit_id={audit_id}")

    if status is not None:
        job.status = status
    if markdown_report is not None:
        job.markdown_report = markdown_report
    if pdf_path is not None:
        job.pdf_path = pdf_path
    if error is not None:
        job.error = error

    job.updated_at = datetime.now(timezone.utc)
    logger.info("Updated audit job %s: status=%s", audit_id, job.status.value)
    return job
