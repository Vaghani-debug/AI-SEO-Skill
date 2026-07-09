"""
src/services/prompt_loader.py

Runtime context loader for the LLM report generator.

Responsibility: read the project's Copilot customisation files from disk
and package them as a PromptContext that the report_service passes to Gemini.

Why this module exists
----------------------
Copilot skills and prompts are available to GitHub Copilot Chat because VS Code
reads them automatically.  The FastAPI application has no such mechanism — it is
a standard Python process that knows nothing about Copilot.  This module bridges
that gap by explicitly reading the same files at runtime so the LLM report
generator follows the same audit methodology, report structure, and writing
guidelines that you see in Copilot Chat.

Files loaded
------------
1. .github/prompts/seo_audit.prompt.md  — report prompt and output format
2. .agents/skills/seo-audit-skill/SKILL.md — SEO audit methodology and checks
3. docs/REPORT_SPECIFICATION.md         — official report structure
4. docs/AI_REPORT_GUIDELINES.md         — tone, accuracy, and hallucination rules

Public interface
----------------
    load_prompt_context(project_root=None) -> PromptContext
"""

import logging  # Standard logging — records which files are loaded and any failures
from dataclasses import dataclass  # dataclass groups the four loaded file contents
from pathlib import Path  # pathlib.Path provides platform-independent path handling

# Module-level logger
logger = logging.getLogger(__name__)  # Resolves to "src.services.prompt_loader"

# ---------------------------------------------------------------------------
# File path constants
# ---------------------------------------------------------------------------
# Paths are relative to the project root.
# Each constant is a tuple of path segments to stay OS-agnostic.

_AUDIT_PROMPT_PATH: tuple[str, ...] = (
    ".github", "prompts", "seo_audit.prompt.md"
)
# The SEO audit prompt that defines the report structure, role, and output format

_SEO_SKILL_PATH: tuple[str, ...] = (
    ".agents", "skills", "seo-audit-skill", "SKILL.md"
)
# The installed SEO audit skill — audit methodology, checks, priority order

_REPORT_SPEC_PATH: tuple[str, ...] = (
    "docs", "REPORT_SPECIFICATION.md"
)
# The official report structure specification (sections, fields, formats)

_AI_GUIDELINES_PATH: tuple[str, ...] = (
    "docs", "AI_REPORT_GUIDELINES.md"
)
# The AI writing guidelines (tone, hallucination prevention, severity language)


# ---------------------------------------------------------------------------
# Result data model
# ---------------------------------------------------------------------------

@dataclass
class PromptContext:
    """
    All loaded text context that guides the LLM report generator.

    Each field contains the raw Markdown content of one guidance file.
    The report_service assembles these fields into the LLM system prompt.
    """

    audit_prompt: str
    # Content of seo_audit.prompt.md — defines the report role and output format

    seo_skill: str
    # Content of SKILL.md — defines the SEO audit methodology and check framework

    report_specification: str
    # Content of REPORT_SPECIFICATION.md — defines the expected report sections

    ai_guidelines: str
    # Content of AI_REPORT_GUIDELINES.md — defines tone, accuracy, and writing rules

    @property
    def combined_system_prompt(self) -> str:
        """
        Assemble all guidance files into a single LLM system prompt string.

        The combined text is structured so the LLM receives the writing
        guidelines and methodology first (as constraints), followed by the
        report prompt (as the task definition).

        Returns:
            Multi-section Markdown string suitable for an LLM system message.
        """
        sections: list[str] = [
            # Section 1: AI writing rules applied first so they act as hard constraints
            "## AI Report Guidelines\n\n" + self.ai_guidelines,

            # Section 2: SEO audit methodology defines what to check and how to prioritise
            "## SEO Audit Methodology (Skill)\n\n" + self.seo_skill,

            # Section 3: Report structure defines the expected output shape
            "## Report Structure Specification\n\n" + self.report_specification,

            # Section 4: The audit prompt defines the role, objective, and exact output format
            "## Audit Prompt\n\n" + self.audit_prompt,
        ]

        return "\n\n---\n\n".join(sections)
        # Join sections with a horizontal rule so the LLM can distinguish where each file ends


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def load_prompt_context(project_root: Path | None = None) -> PromptContext:
    """
    Load all guidance files from disk and return them as a PromptContext.

    The function resolves the project root automatically by navigating
    three levels up from this source file.  An explicit project_root can
    be provided to override this, which is useful for tests that run from
    a different working directory.

    Args:
        project_root: Optional Path to the repository root directory.
                      Defaults to the directory three levels above this file.

    Returns:
        PromptContext containing the content of all four guidance files.

    Raises:
        FileNotFoundError: If any required guidance file is missing.
        PermissionError: If a file exists but cannot be read.
        OSError: For any other filesystem error.
    """
    # Resolve the project root if not explicitly provided
    if project_root is None:
        # __file__ is the absolute path to this module file
        # .parent gives src/services/
        # .parent.parent gives src/
        # .parent.parent.parent gives the project root (c:\AI SEO Skill)
        project_root = Path(__file__).resolve().parent.parent.parent

    logger.info("Loading prompt context from project root: %s", project_root)

    # Load each file individually so the error message names the specific missing file
    audit_prompt: str = _load_file(project_root, _AUDIT_PROMPT_PATH)
    seo_skill: str = _load_file(project_root, _SEO_SKILL_PATH)
    report_specification: str = _load_file(project_root, _REPORT_SPEC_PATH)
    ai_guidelines: str = _load_file(project_root, _AI_GUIDELINES_PATH)

    logger.info(
        "Prompt context loaded: audit_prompt=%d chars, seo_skill=%d chars, "
        "report_spec=%d chars, ai_guidelines=%d chars",
        len(audit_prompt),
        len(seo_skill),
        len(report_specification),
        len(ai_guidelines),
    )

    return PromptContext(
        audit_prompt=audit_prompt,
        seo_skill=seo_skill,
        report_specification=report_specification,
        ai_guidelines=ai_guidelines,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _load_file(project_root: Path, path_segments: tuple[str, ...]) -> str:
    """
    Read a single file and return its content as a string.

    Args:
        project_root: Absolute path to the repository root directory.
        path_segments: Tuple of path components joined to form the file path
                       relative to project_root.

    Returns:
        The complete text content of the file, decoded as UTF-8.

    Raises:
        FileNotFoundError: If the file does not exist at the expected location.
        PermissionError: If the file exists but cannot be read.
    """
    file_path: Path = project_root.joinpath(*path_segments)
    # joinpath(*path_segments) assembles the segments with the OS path separator

    logger.debug("Loading guidance file: %s", file_path)

    if not file_path.exists():
        # Provide a clear error that names the missing file and its expected location
        raise FileNotFoundError(
            f"Required guidance file not found: {file_path}\n"
            f"Expected at: {Path(*path_segments)}\n"
            "Ensure you are running the application from the project root directory."
        )

    content: str = file_path.read_text(encoding="utf-8")
    # read_text(encoding="utf-8") decodes the file as UTF-8, which is the project standard

    if not content.strip():
        # Warn if the file is empty — this would produce a useless LLM context
        logger.warning("Guidance file is empty: %s", file_path)

    logger.debug("Loaded %d characters from: %s", len(content), file_path.name)

    return content
