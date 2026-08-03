from functools import lru_cache
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = REPO_ROOT / ".github" / "prompts" / "SEO_Report_Format.md"


@lru_cache
def load_audit_prompt() -> str:
    """Load the Markdown prompt used to instruct the audit response."""
    return PROMPT_PATH.read_text(encoding="utf-8").strip()
