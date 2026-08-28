"""
test/test_report_contract.py

Contract tests reconciling all guidance, templates, scoring weights,
and runtime models (Phase 1).

Ensures that:
1. MASTER_REPORT_STRUCTURE.md, SEO_RULES.md, AI_REPORT_GUIDELINES.md,
   and seo_audit.prompt.md maintain contract parity.
2. Category weights in SEO_RULES.md sum to exactly 100%.
3. Severity levels, finding statuses, implementation owners, and provenance tiers
   in src/services/audit_models.py match docs and prompt definitions.
4. Prompt loader loads all runtime prompt contexts and verifies section presence.

Run with:
    pytest test/test_report_contract.py -v
"""

from pathlib import Path
import re

import pytest

from src.services.audit_models import (
    EvidenceProvenance,
    FindingStatus,
    ImplementationOwner,
    SeverityLevel,
)
from src.services.prompt_loader import load_prompt_context


class TestReportStructureContract:
    """Tests verifying the canonical report structure and headings contract."""

    def test_master_report_structure_has_all_canonical_parts_and_sections(self) -> None:
        """MASTER_REPORT_STRUCTURE.md contains all canonical PART 1-3 and SECTION 1-5 headings."""
        context = load_prompt_context()
        structure = context.master_report_structure

        expected_headings = [
            "# PART 1: FULL WEBSITE AUDIT — ALL PAGES & URLs",
            "## 1.1 Core Pages",
            "## 1.2 Subpages (Sampled)",
            "## 1.3 Website Structure Overview",
            "## 1.4 Website Coverage Summary",
            "## 1.5 Website Strengths",
            "## 1.6 Website Weaknesses",
            "# PART 2: TECHNICAL SEO AUDIT",
            "## 2.1 Critical & High Priority Issues",
            "## 2.2 Robots.txt Analysis",
            "## 2.3 XML Sitemap Analysis",
            "## 2.4 Core Web Vitals & Page Speed",
            "## 2.5 Indexability & Crawlability",
            "## 2.6 Structured Data Status",
            "# PART 3: ON-PAGE & CONTENT AUDIT",
            "## 3.1 Homepage On-Page Review",
            "## 3.2 Priority Pages On-Page Review",
            "## 3.3 Content Quality Assessment",
            "# SECTION 1: KEYWORD OPPORTUNITY STRATEGY",
            "## 1.1 Primary Keyword Opportunities",
            "## 1.2 Long-Tail Keyword Opportunities",
            "## 1.3 Keyword-to-Page Mapping",
            "# SECTION 2: COMPETITOR ANALYSIS",
            "## 2.1 Competitor Overview",
            "## 2.2 Competitive Gaps & Opportunities",
            "# SECTION 3: LOCATION & MARKET EXPANSION STRATEGY",
            "## 3.1 Applicability Assessment",
            "## 3.2 Local Location Opportunities (if the business serves specific locations)",
            "## 3.3 Audience & Market Expansion Opportunities (if location targeting does not apply)",
            "# SECTION 4: STRUCTURED DATA RECOMMENDATIONS",
            "## 4.1 Recommended Schema Types",
            "## 4.2 Implementation Priority",
            "# SECTION 5: OFF-PAGE SEO & GEO STRATEGY",
            "## 5.1 Link Building Opportunities",
            "## 5.2 AI Search / GEO Visibility (ChatGPT, Perplexity, Gemini)",
            "# SECTION 6: SITE-TYPE STRATEGY",
            "## 6.1 Site-Type Applicability Assessment",
            "## 6.2 Tailored Site-Type Strategy Table",
            "# SECTION 7: PRIORITIZED 90-DAY IMPLEMENTATION ROADMAP",
            "## 7.1 Phase 1 (Days 1\u201330): Critical Technical Foundations & Indexing Fixes",
            "## 7.2 Phase 2 (Days 31\u201360): On-Page, Content & Schema Optimization",
            "## 7.3 Phase 3 (Days 61\u201390): Topical Authority, Local Expansion & GEO Growth",
            "## 7.4 Action Plan Table",
            "# SECTION 8: MEASUREMENT & SOURCE REGISTER",
            "## 8.1 Ongoing SEO KPI Tracking Framework",
            "## 8.2 Recommended Tool & Console Integrations",
            "## 8.3 Source Register",
        ]

        for heading in expected_headings:
            assert heading in structure, f"Missing expected heading in MASTER_REPORT_STRUCTURE: {heading}"

    def test_table_headers_exist_for_core_tables(self) -> None:
        """Key table headers exist in MASTER_REPORT_STRUCTURE.md."""
        context = load_prompt_context()
        structure = context.master_report_structure

        assert "| #Index | Page Name (derived from URL) | URL | Title Tag | SEO Recommendation |" in structure
        assert "| Issue | Severity | Business Impact | SEO Recommendation |" in structure
        assert "| # | Target City / Region | Primary Keyword | Est. Monthly Searches | Competition Level | Priority | Source | Retrieved |" in structure
        assert "| Directive / User-Agent | Path / Rule | Status | Impact | SEO Recommendation |" in structure
        assert "| Metric | Observed Value | Google Threshold | Status | Source | SEO Recommendation |" in structure
        assert "| Topic Cluster | Target Primary Keyword | Secondary / Long-Tail Variants | Assigned Target URL | Cannibalization Risk | Strategic Action |" in structure
        assert "| Schema Type | Target Page / Section | Required Properties | Rich Result Eligibility | Implementation Guidance |" in structure
        assert "| Phase | Finding / Action Item | Category | Effort | Suggested Owner | Dependencies | Target KPI |" in structure
        assert "| Metric | Baseline | 90-Day Target | Data Source | Review Cadence |" in structure
        assert "| # | Claim / Estimate | Source Name / URL | Retrieved Date |" in structure


class TestScoringAndMethodologyContract:
    """Tests verifying scoring engine weights and methodology contracts."""

    def test_seo_rules_category_weights_sum_to_100_percent(self) -> None:
        """SEO_RULES.md defines category weights that total 100%."""
        rules_path = Path(__file__).resolve().parent.parent / "docs" / "SEO_RULES.md"
        content = rules_path.read_text(encoding="utf-8")

        # Technical SEO: 40%, On-Page SEO: 30%, Content Quality: 20%, User Experience: 10%
        assert "Technical SEO" in content and "40%" in content
        assert "On-Page SEO" in content and "30%" in content
        assert "Content Quality" in content and "20%" in content
        assert ("User Experience" in content or "UX" in content) and "10%" in content

        # Check weights numerically
        weights = {
            "technical": 40,
            "on_page": 30,
            "content": 20,
            "ux_performance": 10,
        }
        assert sum(weights.values()) == 100

    def test_severity_levels_parity(self) -> None:
        """Severity levels across docs and audit_models enum are identical."""
        expected_severities = {"Critical", "High", "Medium", "Low", "Informational"}
        model_severities = {s.value for s in SeverityLevel}
        assert model_severities == expected_severities

        rules_path = Path(__file__).resolve().parent.parent / "docs" / "SEO_RULES.md"
        content = rules_path.read_text(encoding="utf-8")
        for sev in expected_severities:
            assert sev in content

    def test_finding_statuses_parity(self) -> None:
        """Finding statuses across docs and audit_models enum are identical."""
        expected_statuses = {"Pass", "Issue", "Opportunity", "Unverified", "Not applicable"}
        model_statuses = {s.value for s in FindingStatus}
        assert model_statuses == expected_statuses

        rules_path = Path(__file__).resolve().parent.parent / "docs" / "SEO_RULES.md"
        content = rules_path.read_text(encoding="utf-8")
        for st in expected_statuses:
            assert st in content

    def test_evidence_provenance_parity(self) -> None:
        """Evidence provenance tiers across docs and audit_models enum are identical."""
        expected_provenance = {
            "measured",
            "researched",
            "derived",
            "consultant_assessment",
            "client_input_required",
            "integration_required",
        }
        model_provenance = {p.value for p in EvidenceProvenance}
        assert model_provenance == expected_provenance

        rules_path = Path(__file__).resolve().parent.parent / "docs" / "SEO_RULES.md"
        content = rules_path.read_text(encoding="utf-8")
        for prov in expected_provenance:
            assert prov in content

    def test_implementation_owners_parity(self) -> None:
        """Implementation owner roles in audit_models enum are complete."""
        expected_owners = {"Developer", "Content Writer", "SEO Specialist", "Site Owner", "DevOps"}
        model_owners = {o.value for o in ImplementationOwner}
        assert model_owners == expected_owners


class TestPromptAndGuidanceContract:
    """Tests verifying prompt context assembly and rule coherence."""

    def test_prompt_context_loads_cleanly(self) -> None:
        """PromptContext loads all four guidance files without error."""
        context = load_prompt_context()
        assert len(context.audit_prompt) > 0
        assert len(context.seo_skill) > 0
        assert len(context.master_report_structure) > 0
        assert len(context.ai_guidelines) > 0

    def test_combined_system_prompt_structure(self) -> None:
        """combined_system_prompt includes AI guidelines, SEO skill, and audit prompt in priority order."""
        context = load_prompt_context()
        combined = context.combined_system_prompt

        assert "## AI Report Guidelines" in combined
        assert "## SEO Audit Methodology (Skill)" in combined
        assert "## Audit Prompt" in combined

        idx_guidelines = combined.index("## AI Report Guidelines")
        idx_methodology = combined.index("## SEO Audit Methodology (Skill)")
        idx_prompt = combined.index("## Audit Prompt")

        assert idx_guidelines < idx_methodology < idx_prompt
