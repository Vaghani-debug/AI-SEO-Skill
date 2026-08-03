from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / ".github" / "prompts"
GLOBAL_RULES_PATH = PROMPTS_DIR / "SEO_Global_Rules_Layer.md"
CORE_SERVICE_SECTION_PATH = (
    PROMPTS_DIR / "SEO_Core_And_Service_Subpages_Section.md"
)


def load_audit_prompt() -> str:
    """Load and compose prompt layers used to instruct the audit response."""
    global_rules = GLOBAL_RULES_PATH.read_text(encoding="utf-8").strip()
    core_service_section = CORE_SERVICE_SECTION_PATH.read_text(encoding="utf-8").strip()
    return f"{global_rules}\n\n{core_service_section}"
