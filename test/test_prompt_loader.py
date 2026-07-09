"""
test/test_prompt_loader.py

Unit tests for src/services/prompt_loader.py.

Tests verify:
- All four required files can be loaded from the real project directory.
- PromptContext fields are populated and non-empty.
- combined_system_prompt assembles all sections correctly.
- Missing files raise FileNotFoundError with useful messages.
- The project root override works correctly.

Run with:
    pytest test/test_prompt_loader.py -v
"""

import pytest  # pytest: test runner and fixture system
from pathlib import Path  # Path: used to construct temporary directories and file paths
from unittest.mock import patch  # patch: intercepts file system operations in isolation tests

from src.services.prompt_loader import (
    PromptContext,       # Result dataclass returned by load_prompt_context()
    load_prompt_context,  # Public function under test
    _load_file,          # Internal helper — tested directly for error path coverage
)


# ---------------------------------------------------------------------------
# Integration tests — use real project files
# ---------------------------------------------------------------------------

class TestLoadPromptContextIntegration:
    """
    Tests that load the real guidance files from the project directory.

    These confirm that all required files exist, are readable, and contain
    meaningful content.  They also serve as a smoke test that the file
    paths in prompt_loader.py match the actual repository layout.
    """

    def test_returns_prompt_context_instance(self) -> None:
        """load_prompt_context() returns a PromptContext dataclass."""
        result = load_prompt_context()  # No override — uses the real project root
        assert isinstance(result, PromptContext)  # Correct return type

    def test_audit_prompt_loaded(self) -> None:
        """audit_prompt field is non-empty after loading."""
        result = load_prompt_context()
        assert len(result.audit_prompt) > 100  # Must be a substantial prompt, not a stub

    def test_seo_skill_loaded(self) -> None:
        """seo_skill field is non-empty after loading."""
        result = load_prompt_context()
        assert len(result.seo_skill) > 100  # SEO skill is a detailed methodology document

    def test_report_specification_loaded(self) -> None:
        """report_specification field is non-empty after loading."""
        result = load_prompt_context()
        assert len(result.report_specification) > 100

    def test_ai_guidelines_loaded(self) -> None:
        """ai_guidelines field is non-empty after loading."""
        result = load_prompt_context()
        assert len(result.ai_guidelines) > 100

    def test_audit_prompt_contains_expected_content(self) -> None:
        """audit_prompt content mentions the audit role or instructions."""
        result = load_prompt_context()
        content_lower = result.audit_prompt.lower()
        # The prompt should reference the role or the report purpose
        assert any(
            keyword in content_lower
            for keyword in ["seo", "audit", "report", "instructions"]
        )

    def test_seo_skill_contains_expected_content(self) -> None:
        """seo_skill content mentions crawlability or technical SEO checks."""
        result = load_prompt_context()
        content_lower = result.seo_skill.lower()
        assert any(
            keyword in content_lower
            for keyword in ["crawl", "robots", "sitemap", "technical", "seo"]
        )

    def test_ai_guidelines_contains_hallucination_prevention(self) -> None:
        """ai_guidelines mentions accuracy or hallucination prevention."""
        result = load_prompt_context()
        content_lower = result.ai_guidelines.lower()
        assert any(
            keyword in content_lower
            for keyword in ["accuracy", "never invent", "hallucin", "evidence"]
        )

    def test_report_specification_contains_section_structure(self) -> None:
        """report_specification references report sections or structure."""
        result = load_prompt_context()
        content_lower = result.report_specification.lower()
        assert any(
            keyword in content_lower
            for keyword in ["executive summary", "section", "report", "structure"]
        )


# ---------------------------------------------------------------------------
# PromptContext.combined_system_prompt property
# ---------------------------------------------------------------------------

class TestCombinedSystemPrompt:
    """Tests for the combined_system_prompt property that assembles the LLM input."""

    def test_combined_contains_all_four_sections(self) -> None:
        """combined_system_prompt includes all four guidance file sections."""
        context = PromptContext(
            audit_prompt="AUDIT_PROMPT",
            seo_skill="SEO_SKILL",
            report_specification="REPORT_SPEC",
            ai_guidelines="AI_GUIDELINES",
        )
        combined = context.combined_system_prompt

        assert "AUDIT_PROMPT" in combined        # Audit prompt included
        assert "SEO_SKILL" in combined           # SEO skill included
        assert "REPORT_SPEC" in combined         # Report spec included
        assert "AI_GUIDELINES" in combined       # AI guidelines included

    def test_combined_sections_separated_by_rule(self) -> None:
        """Sections in combined_system_prompt are separated by horizontal rules."""
        context = PromptContext(
            audit_prompt="A",
            seo_skill="B",
            report_specification="C",
            ai_guidelines="D",
        )
        combined = context.combined_system_prompt
        assert "---" in combined  # Horizontal rule used as section separator

    def test_combined_non_empty(self) -> None:
        """combined_system_prompt is always non-empty."""
        context = PromptContext(
            audit_prompt="A",
            seo_skill="B",
            report_specification="C",
            ai_guidelines="D",
        )
        assert len(context.combined_system_prompt) > 0

    def test_combined_ai_guidelines_appears_first(self) -> None:
        """AI guidelines appear before the audit prompt in the combined output."""
        context = PromptContext(
            audit_prompt="AUDIT",
            seo_skill="SKILL",
            report_specification="SPEC",
            ai_guidelines="GUIDELINES",
        )
        combined = context.combined_system_prompt
        guidelines_pos = combined.index("GUIDELINES")
        audit_pos = combined.index("AUDIT")
        assert guidelines_pos < audit_pos  # Guidelines come first (act as hard constraints)

    def test_combined_uses_real_content(self) -> None:
        """combined_system_prompt from real files is substantial."""
        context = load_prompt_context()
        combined = context.combined_system_prompt
        assert len(combined) > 1000  # Real guidance files produce a large combined prompt


# ---------------------------------------------------------------------------
# Error path tests — missing and unreadable files
# ---------------------------------------------------------------------------

class TestMissingFiles:
    """Tests that missing files raise clear, informative errors."""

    def test_missing_audit_prompt_raises_file_not_found(self, tmp_path: Path) -> None:
        """FileNotFoundError is raised when seo_audit.prompt.md does not exist."""
        # tmp_path is an empty directory — none of the guidance files exist there
        with pytest.raises(FileNotFoundError) as exc_info:
            load_prompt_context(project_root=tmp_path)

        # The error message should mention the missing file
        assert "seo_audit.prompt.md" in str(exc_info.value)

    def test_missing_seo_skill_raises_file_not_found(self, tmp_path: Path) -> None:
        """FileNotFoundError is raised when SKILL.md does not exist."""
        # Create only the audit prompt so we get past the first load
        prompt_dir = tmp_path / ".github" / "prompts"
        prompt_dir.mkdir(parents=True)  # Create intermediate directories
        (prompt_dir / "seo_audit.prompt.md").write_text("Test prompt", encoding="utf-8")

        with pytest.raises(FileNotFoundError) as exc_info:
            load_prompt_context(project_root=tmp_path)

        assert "SKILL.md" in str(exc_info.value)  # Missing file named in error

    def test_missing_report_spec_raises_file_not_found(self, tmp_path: Path) -> None:
        """FileNotFoundError is raised when REPORT_SPECIFICATION.md does not exist."""
        # Create the first two files so we reach the third
        _write_stub(tmp_path, ".github", "prompts", "seo_audit.prompt.md")
        _write_stub(tmp_path, ".agents", "skills", "seo-audit-skill", "SKILL.md")

        with pytest.raises(FileNotFoundError) as exc_info:
            load_prompt_context(project_root=tmp_path)

        assert "REPORT_SPECIFICATION.md" in str(exc_info.value)

    def test_missing_ai_guidelines_raises_file_not_found(self, tmp_path: Path) -> None:
        """FileNotFoundError is raised when AI_REPORT_GUIDELINES.md does not exist."""
        # Create first three files so we reach the fourth
        _write_stub(tmp_path, ".github", "prompts", "seo_audit.prompt.md")
        _write_stub(tmp_path, ".agents", "skills", "seo-audit-skill", "SKILL.md")
        _write_stub(tmp_path, "docs", "REPORT_SPECIFICATION.md")

        with pytest.raises(FileNotFoundError) as exc_info:
            load_prompt_context(project_root=tmp_path)

        assert "AI_REPORT_GUIDELINES.md" in str(exc_info.value)

    def test_error_message_contains_expected_path(self, tmp_path: Path) -> None:
        """The FileNotFoundError message includes the expected file path."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_prompt_context(project_root=tmp_path)

        error_text = str(exc_info.value)
        assert "seo_audit.prompt.md" in error_text  # File name mentioned
        # The error should suggest what the user can do
        assert len(error_text) > 20  # More than just the file name — a useful message


# ---------------------------------------------------------------------------
# Project root override tests
# ---------------------------------------------------------------------------

class TestProjectRootOverride:
    """Tests that the project_root parameter correctly redirects file loading."""

    def test_custom_project_root_used(self, tmp_path: Path) -> None:
        """Files are loaded from a custom project_root when provided."""
        # Create all four files in tmp_path with custom content
        _write_stub(tmp_path, ".github", "prompts", "seo_audit.prompt.md", content="CUSTOM_PROMPT")
        _write_stub(tmp_path, ".agents", "skills", "seo-audit-skill", "SKILL.md", content="CUSTOM_SKILL")
        _write_stub(tmp_path, "docs", "REPORT_SPECIFICATION.md", content="CUSTOM_SPEC")
        _write_stub(tmp_path, "docs", "AI_REPORT_GUIDELINES.md", content="CUSTOM_GUIDE")

        result = load_prompt_context(project_root=tmp_path)

        assert result.audit_prompt == "CUSTOM_PROMPT"      # Custom content loaded
        assert result.seo_skill == "CUSTOM_SKILL"
        assert result.report_specification == "CUSTOM_SPEC"
        assert result.ai_guidelines == "CUSTOM_GUIDE"

    def test_path_object_accepted(self) -> None:
        """load_prompt_context() accepts a pathlib.Path object as project_root."""
        project_root = Path(__file__).resolve().parent.parent
        # Two levels up from test/ is the project root
        result = load_prompt_context(project_root=project_root)
        assert isinstance(result, PromptContext)  # No exception raised


# ---------------------------------------------------------------------------
# _load_file() helper tests
# ---------------------------------------------------------------------------

class TestLoadFileHelper:
    """Tests for the internal _load_file() function."""

    def test_reads_file_content(self, tmp_path: Path) -> None:
        """_load_file returns the exact text content of a file."""
        test_file = tmp_path / "test.md"
        test_file.write_text("Hello, world!", encoding="utf-8")

        content = _load_file(tmp_path, ("test.md",))

        assert content == "Hello, world!"  # Exact content returned

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        """_load_file raises FileNotFoundError for a non-existent file."""
        with pytest.raises(FileNotFoundError):
            _load_file(tmp_path, ("does_not_exist.md",))

    def test_unicode_content_read_correctly(self, tmp_path: Path) -> None:
        """Files with non-ASCII characters are decoded correctly as UTF-8."""
        test_file = tmp_path / "unicode.md"
        unicode_content = "Héllo wörld — SEO öptimisierung"
        test_file.write_text(unicode_content, encoding="utf-8")

        content = _load_file(tmp_path, ("unicode.md",))

        assert content == unicode_content  # Unicode characters preserved

    def test_nested_path_segments_resolved(self, tmp_path: Path) -> None:
        """_load_file correctly joins multiple path segments."""
        nested_dir = tmp_path / "a" / "b" / "c"
        nested_dir.mkdir(parents=True)  # Create the nested directory
        nested_file = nested_dir / "file.md"
        nested_file.write_text("Nested content", encoding="utf-8")

        content = _load_file(tmp_path, ("a", "b", "c", "file.md"))

        assert content == "Nested content"  # Nested path resolved correctly


# ---------------------------------------------------------------------------
# Real file content specifics
# ---------------------------------------------------------------------------

class TestRealFileContent:
    """
    Verify that the real project guidance files contain specific expected markers.

    These tests act as a contract between the guidance files and the application.
    If someone accidentally clears or renames a section, these tests will fail
    before the LLM report generator produces incorrect output.
    """

    def test_audit_prompt_contains_website_url_placeholder(self) -> None:
        """seo_audit.prompt.md must contain the {{website_url}} template variable."""
        result = load_prompt_context()
        assert "{{website_url}}" in result.audit_prompt
        # This placeholder is replaced with the user's URL when the LLM prompt is assembled

    def test_audit_prompt_instructs_seo_audit_skill_usage(self) -> None:
        """seo_audit.prompt.md must reference the seo-audit skill."""
        result = load_prompt_context()
        assert "seo-audit" in result.audit_prompt.lower()
        # The prompt explicitly tells the LLM to apply the installed skill

    def test_audit_prompt_references_report_specification(self) -> None:
        """seo_audit.prompt.md must reference REPORT_SPECIFICATION.md."""
        result = load_prompt_context()
        assert "REPORT_SPECIFICATION" in result.audit_prompt or "report_specification" in result.audit_prompt.lower()
        # The prompt instructs the LLM to follow the report spec

    def test_audit_prompt_references_ai_guidelines(self) -> None:
        """seo_audit.prompt.md must reference AI_REPORT_GUIDELINES.md."""
        result = load_prompt_context()
        assert "AI_REPORT_GUIDELINES" in result.audit_prompt or "ai_report_guidelines" in result.audit_prompt.lower()
        # The prompt instructs the LLM to follow the AI writing guidelines

    def test_seo_skill_contains_priority_order(self) -> None:
        """SKILL.md must describe the audit priority order."""
        result = load_prompt_context()
        content_lower = result.seo_skill.lower()
        # The skill defines a priority order — crawlability is always first
        assert "crawl" in content_lower

    def test_seo_skill_references_robots_txt(self) -> None:
        """SKILL.md must mention robots.txt as an audit check."""
        result = load_prompt_context()
        assert "robots" in result.seo_skill.lower()

    def test_ai_guidelines_mentions_senior_consultant(self) -> None:
        """AI_REPORT_GUIDELINES.md must define the Senior Technical SEO Consultant persona."""
        result = load_prompt_context()
        assert "senior" in result.ai_guidelines.lower()
        # The persona definition constrains the LLM's tone

    def test_ai_guidelines_defines_severity_language(self) -> None:
        """AI_REPORT_GUIDELINES.md must define severity levels (Critical, High, etc.)."""
        result = load_prompt_context()
        content_lower = result.ai_guidelines.lower()
        assert "critical" in content_lower or "severity" in content_lower

    def test_report_specification_mentions_pdf(self) -> None:
        """REPORT_SPECIFICATION.md must reference PDF as the primary output format."""
        result = load_prompt_context()
        assert "pdf" in result.report_specification.lower()

    def test_report_specification_mentions_executive_summary(self) -> None:
        """REPORT_SPECIFICATION.md must describe an Executive Summary section."""
        result = load_prompt_context()
        assert "executive summary" in result.report_specification.lower()


# ---------------------------------------------------------------------------
# PromptContext dataclass behaviour
# ---------------------------------------------------------------------------

class TestPromptContextDataclass:
    """Tests that PromptContext stores and returns field values correctly."""

    def test_field_values_preserved_exactly(self) -> None:
        """All four field values are stored and returned without modification."""
        ctx = PromptContext(
            audit_prompt="AP_CONTENT",
            seo_skill="SK_CONTENT",
            report_specification="RS_CONTENT",
            ai_guidelines="AG_CONTENT",
        )
        assert ctx.audit_prompt == "AP_CONTENT"
        assert ctx.seo_skill == "SK_CONTENT"
        assert ctx.report_specification == "RS_CONTENT"
        assert ctx.ai_guidelines == "AG_CONTENT"

    def test_combined_with_empty_fields_does_not_raise(self) -> None:
        """combined_system_prompt is produced even when all fields are empty strings."""
        ctx = PromptContext(
            audit_prompt="",
            seo_skill="",
            report_specification="",
            ai_guidelines="",
        )
        combined = ctx.combined_system_prompt  # Must not raise
        assert isinstance(combined, str)        # Always returns a string

    def test_combined_no_duplication(self) -> None:
        """Each field value appears exactly once in combined_system_prompt."""
        unique_value = "UNIQUE_MARKER_XYZ"
        ctx = PromptContext(
            audit_prompt=unique_value,
            seo_skill="B",
            report_specification="C",
            ai_guidelines="D",
        )
        combined = ctx.combined_system_prompt
        assert combined.count(unique_value) == 1  # Appears exactly once — not duplicated

    def test_combined_contains_section_header_ai_guidelines(self) -> None:
        """combined_system_prompt includes the 'AI Report Guidelines' section header."""
        ctx = PromptContext(
            audit_prompt="A", seo_skill="B", report_specification="C", ai_guidelines="D"
        )
        assert "AI Report Guidelines" in ctx.combined_system_prompt

    def test_combined_contains_section_header_seo_skill(self) -> None:
        """combined_system_prompt includes the 'SEO Audit Methodology' section header."""
        ctx = PromptContext(
            audit_prompt="A", seo_skill="B", report_specification="C", ai_guidelines="D"
        )
        assert "SEO Audit Methodology" in ctx.combined_system_prompt

    def test_combined_contains_section_header_report_spec(self) -> None:
        """combined_system_prompt includes the 'Report Structure Specification' section header."""
        ctx = PromptContext(
            audit_prompt="A", seo_skill="B", report_specification="C", ai_guidelines="D"
        )
        assert "Report Structure Specification" in ctx.combined_system_prompt

    def test_combined_contains_section_header_audit_prompt(self) -> None:
        """combined_system_prompt includes the 'Audit Prompt' section header."""
        ctx = PromptContext(
            audit_prompt="A", seo_skill="B", report_specification="C", ai_guidelines="D"
        )
        assert "Audit Prompt" in ctx.combined_system_prompt

    def test_combined_audit_prompt_appears_last(self) -> None:
        """The audit prompt section is the last section in combined_system_prompt."""
        ctx = PromptContext(
            audit_prompt="AUDIT_LAST",
            seo_skill="SKILL",
            report_specification="SPEC",
            ai_guidelines="GUIDE",
        )
        combined = ctx.combined_system_prompt
        audit_pos = combined.rfind("AUDIT_LAST")       # Last occurrence
        skill_pos = combined.find("SKILL")
        spec_pos = combined.find("SPEC")
        guide_pos = combined.find("GUIDE")
        # Audit prompt must appear after all other sections
        assert audit_pos > skill_pos
        assert audit_pos > spec_pos
        assert audit_pos > guide_pos

    def test_combined_seo_skill_between_guidelines_and_spec(self) -> None:
        """SEO skill section appears after guidelines but before report spec."""
        ctx = PromptContext(
            audit_prompt="AP", seo_skill="SK", report_specification="RS", ai_guidelines="AG"
        )
        combined = ctx.combined_system_prompt
        assert combined.find("AG") < combined.find("SK") < combined.find("RS")


# ---------------------------------------------------------------------------
# load_prompt_context() determinism
# ---------------------------------------------------------------------------

class TestLoadPromptContextDeterminism:
    """Tests that loading is stable and idempotent."""

    def test_loading_twice_returns_identical_content(self) -> None:
        """Calling load_prompt_context() twice produces identical field values."""
        first = load_prompt_context()
        second = load_prompt_context()

        assert first.audit_prompt == second.audit_prompt            # Identical
        assert first.seo_skill == second.seo_skill
        assert first.report_specification == second.report_specification
        assert first.ai_guidelines == second.ai_guidelines

    def test_combined_is_stable_across_calls(self) -> None:
        """combined_system_prompt produces the same string on every call."""
        first = load_prompt_context().combined_system_prompt
        second = load_prompt_context().combined_system_prompt
        assert first == second   # Deterministic output

    def test_none_root_same_as_omitting_root(self) -> None:
        """Passing project_root=None explicitly is the same as not passing it."""
        with_none = load_prompt_context(project_root=None)
        without_arg = load_prompt_context()
        assert with_none.audit_prompt == without_arg.audit_prompt


# ---------------------------------------------------------------------------
# Additional _load_file() edge cases
# ---------------------------------------------------------------------------

class TestLoadFileAdditionalEdgeCases:
    """Additional edge cases for the internal _load_file() helper."""

    def test_content_not_stripped(self, tmp_path: Path) -> None:
        """File content is returned exactly as stored — leading/trailing whitespace preserved."""
        test_file = tmp_path / "padded.md"
        padded_content = "\n\n  Hello World  \n\n"  # Intentional whitespace
        test_file.write_text(padded_content, encoding="utf-8")

        content = _load_file(tmp_path, ("padded.md",))

        assert content == padded_content  # No stripping — returned verbatim

    def test_whitespace_only_file_returns_content(self, tmp_path: Path) -> None:
        """A file containing only whitespace is returned (triggers a warning but no error)."""
        test_file = tmp_path / "empty.md"
        test_file.write_text("   \n   ", encoding="utf-8")

        content = _load_file(tmp_path, ("empty.md",))

        assert isinstance(content, str)    # Returns a string, not raises
        assert content == "   \n   "       # Exact whitespace-only content returned

    def test_multiline_content_preserved(self, tmp_path: Path) -> None:
        """Multi-line file content is returned with all newlines intact."""
        test_file = tmp_path / "multi.md"
        multiline = "Line 1\nLine 2\nLine 3\n"
        test_file.write_text(multiline, encoding="utf-8")

        content = _load_file(tmp_path, ("multi.md",))

        assert content == multiline         # Newlines preserved
        assert content.count("\n") == 3    # Exact newline count

    def test_markdown_content_with_special_chars(self, tmp_path: Path) -> None:
        """Markdown with special characters (dashes, asterisks, backticks) is read correctly."""
        test_file = tmp_path / "markdown.md"
        markdown = "# Heading\n\n- **Bold** item\n- `code` item\n\n> Blockquote\n"
        test_file.write_text(markdown, encoding="utf-8")

        content = _load_file(tmp_path, ("markdown.md",))

        assert "**Bold**" in content       # Markdown syntax preserved
        assert "`code`" in content
        assert "> Blockquote" in content


# ---------------------------------------------------------------------------
# Test utility
# ---------------------------------------------------------------------------

def _write_stub(
    root: Path,
    *path_parts: str,
    content: str = "stub content",
) -> None:
    """
    Write a stub text file at root / *path_parts.

    Creates intermediate directories as needed.
    Used by tests that need some but not all guidance files present.
    """
    file_path = root.joinpath(*path_parts)          # Build the full path
    file_path.parent.mkdir(parents=True, exist_ok=True)  # Create directories
    file_path.write_text(content, encoding="utf-8")  # Write the stub content
