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


class TestEnumsContract:
    """Tests for Phase 1 shared enums: EvidenceProvenance, FindingStatus, SeverityLevel, ImplementationOwner."""

    def test_evidence_provenance_members(self) -> None:
        from src.services.audit_models import EvidenceProvenance

        assert {member.value for member in EvidenceProvenance} == {
            "measured",
            "researched",
            "derived",
            "consultant_assessment",
            "client_input_required",
            "integration_required",
        }

    def test_finding_status_members(self) -> None:
        from src.services.audit_models import FindingStatus

        assert {member.value for member in FindingStatus} == {
            "Pass",
            "Issue",
            "Opportunity",
            "Unverified",
            "Not applicable",
        }

    def test_severity_level_members(self) -> None:
        from src.services.audit_models import SeverityLevel

        assert {member.value for member in SeverityLevel} == {
            "Critical",
            "High",
            "Medium",
            "Low",
            "Informational",
        }

    def test_implementation_owner_members(self) -> None:
        from src.services.audit_models import ImplementationOwner

        assert {member.value for member in ImplementationOwner} == {
            "Developer",
            "Content Writer",
            "SEO Specialist",
            "Site Owner",
            "DevOps",
        }


class TestRecommendationAndScoringModels:
    """Tests for Phase 1 universal recommendation, scoring, and coverage models."""

    def test_recommendation_item_construction_and_defaults(self) -> None:
        from src.services.audit_models import (
            EvidenceProvenance,
            FindingStatus,
            ImplementationOwner,
            RecommendationItem,
            SeverityLevel,
        )

        item = RecommendationItem(
            finding_id="TECH-META-001",
            category="On-Page SEO",
            affected_urls=["https://example.com/about"],
            status=FindingStatus.ISSUE,
            evidence="Meta description is missing in HTML",
            severity=SeverityLevel.HIGH,
            business_impact="Lower CTR from SERP",
            why_it_matters="Search engines display automated snippets",
            recommended_action="Add unique 150-160 character meta description",
            priority=2,
            effort="Easy",
            estimated_time="10 minutes",
            suggested_owner=ImplementationOwner.CONTENT_WRITER,
            dependencies=[],
            validation_method="Inspect meta tag and rerun audit",
            kpi="Snippet CTR",
            confidence=1.0,
            provenance=EvidenceProvenance.MEASURED,
            source_references=["https://developers.google.com/search/docs/appearance/snippet"],
        )

        assert item.finding_id == "TECH-META-001"
        assert item.status == FindingStatus.ISSUE
        assert item.severity == SeverityLevel.HIGH
        assert item.suggested_owner == ImplementationOwner.CONTENT_WRITER
        assert item.provenance == EvidenceProvenance.MEASURED
        assert item.confidence == 1.0

    def test_score_breakdown_and_category_models(self) -> None:
        from src.services.audit_models import CategoryScoreBreakdown, ScoreBreakdown

        tech_cat = CategoryScoreBreakdown(
            category="Technical SEO",
            weight=0.40,
            score=90.0,
            evidence_coverage=1.0,
            passed_checks=9,
            total_applicable_checks=10,
        )
        onpage_cat = CategoryScoreBreakdown(
            category="On-Page SEO",
            weight=0.30,
            score=85.0,
            evidence_coverage=0.9,
            passed_checks=8,
            total_applicable_checks=10,
        )

        score_report = ScoreBreakdown(
            overall_score=87.5,
            overall_coverage=0.95,
            categories={"Technical SEO": tech_cat, "On-Page SEO": onpage_cat},
        )

        assert score_report.overall_score == 87.5
        assert score_report.overall_coverage == 0.95
        assert len(score_report.categories) == 2
        assert score_report.categories["Technical SEO"].weight == 0.40

    def test_audit_coverage_model(self) -> None:
        from src.services.audit_models import AuditCoverage

        coverage = AuditCoverage(
            pages_discovered=25,
            pages_crawled=10,
            pages_failed=0,
            rendered_pages=2,
            research_available=True,
            evidence_coverage_ratio=0.88,
        )
        assert coverage.pages_discovered == 25
        assert coverage.pages_crawled == 10
        assert coverage.rendered_pages == 2
        assert coverage.evidence_coverage_ratio == 0.88
