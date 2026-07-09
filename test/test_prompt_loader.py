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
