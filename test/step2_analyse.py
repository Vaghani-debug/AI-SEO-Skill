"""Step 2 — Send fetched page data to Gemini (or OpenAI) using a chosen SEO skill.

Usage:
    python test/step2_analyse.py --skill seo-audit
    python test/step2_analyse.py --skill seo
    python test/step2_analyse.py --skill seo-audit --provider openai --model gpt-4o

Requires:
    pip install openai          (works for both Gemini and OpenAI)
    .env file at repo root with GEMINI_API_KEY= (or OPENAI_API_KEY=)

Reads:
    test/last_fetch.json        structured metadata from step 1
    test/last_fetch.html        raw HTML from step 1

Writes:
    test/last_result_<skill>.md full response
"""
import argparse
import json
import os
import re
import sys

# ── resolve paths relative to repo root, not cwd ─────────────────────────────

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    """Parse .env at repo root and inject missing keys into os.environ."""
    env_path = os.path.join(REPO_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:  # never override a real env var
                os.environ[key] = value


_load_dotenv()
SKILLS_DIR = os.path.join(REPO_ROOT, ".agents", "skills")
TEST_DIR   = os.path.join(REPO_ROOT, "test")

SKILL_PATHS = {
    "seo-audit": os.path.join(SKILLS_DIR, "seo-audit-skill", "SKILL.md"),
    "seo":       os.path.join(SKILLS_DIR, "seo-audit-skill", "SKILL.md"),  # alias
}

# How many bytes of raw HTML to include in the prompt (covers <head> + hero).
# Increase if you want deeper body analysis; decrease to save tokens.
HTML_EXCERPT_BYTES = 12_000

# Provider configuration
PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key":  "GEMINI_API_KEY",
        "default_model": "gemini-2.5-flash",
    },
    "openai": {
        "base_url": None,          # use openai SDK default
        "env_key":  "OPENAI_API_KEY",
        "default_model": "gpt-4o",
    },
}


# ── helpers ───────────────────────────────────────────────────────────────────

def load_skill_prompt(skill_name: str) -> str:
    path = SKILL_PATHS.get(skill_name)
    if not path or not os.path.exists(path):
        print(f"ERROR: skill '{skill_name}' not found. Available: {list(SKILL_PATHS)}")
        sys.exit(1)
    text = open(path, encoding="utf-8").read()
    # Strip YAML frontmatter (--- ... ---)
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL)
    return text.strip()


def build_user_message(meta: dict, html: str) -> str:
    excerpt = html[:HTML_EXCERPT_BYTES]
    meta_display = {k: v for k, v in meta.items()}
    return (
        "Please analyse the following page for SEO issues and provide "
        "prioritised, actionable recommendations.\n\n"
        "## Page metadata\n"
        f"```json\n{json.dumps(meta_display, indent=2, ensure_ascii=False)}\n```\n\n"
        "## HTML excerpt (first ~12 KB — covers <head> and above-the-fold)\n"
        f"```html\n{excerpt}\n```"
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Step 2: SEO analysis via Gemini / OpenAI")
    parser.add_argument("--skill", required=True,
                        choices=list(SKILL_PATHS), help="Which SEO skill to use")
    parser.add_argument("--provider", default="gemini",
                        choices=list(PROVIDERS), help="API provider (default: gemini)")
    parser.add_argument("--model", default=None,
                        help="Model override (default: gemini-2.5-flash for Gemini, gpt-4o for OpenAI)")
    args = parser.parse_args()

    provider_cfg = PROVIDERS[args.provider]
    model = args.model or provider_cfg["default_model"]
    env_key = provider_cfg["env_key"]

    # Validate API key early
    api_key = os.environ.get(env_key, "").strip()
    if not api_key:
        print(f"ERROR: {env_key} is not set.")
        print(f"  Add it to .env at the repo root:  {env_key}=your-key-here")
        print(f"  Or set it in the shell:           $env:{env_key} = 'your-key-here'")
        sys.exit(1)

    # Load step-1 outputs
    meta_path = os.path.join(TEST_DIR, "last_fetch.json")
    html_path = os.path.join(TEST_DIR, "last_fetch.html")
    if not os.path.exists(meta_path) or not os.path.exists(html_path):
        print("ERROR: Run step1_fetch.py first — last_fetch.json / last_fetch.html not found.")
        sys.exit(1)

    meta = json.load(open(meta_path, encoding="utf-8"))
    html = open(html_path, encoding="utf-8").read()

    system_prompt = load_skill_prompt(args.skill)
    user_message  = build_user_message(meta, html)

    print(f"\nSkill    : {args.skill}")
    print(f"Provider : {args.provider}")
    print(f"Model    : {model}")
    print(f"URL      : {meta.get('final_url', '?')}")
    print(f"HTML sent: {HTML_EXCERPT_BYTES:,} bytes")
    print("─" * 60)

    # Import openai — give a friendly error if missing
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: openai package not installed.")
        print("  Install it with:  pip install openai")
        sys.exit(1)

    base_url = provider_cfg["base_url"]
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)

    # Stream the response so output appears incrementally
    result_text = []
    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        temperature=0.3,
        stream=True,
    )
    for chunk in stream:
        content = chunk.choices[0].delta.content
        if content:
            print(content, end="", flush=True)
            result_text.append(content)

    print("\n" + "─" * 60)

    # Save result
    out_path = os.path.join(TEST_DIR, f"last_result_{args.skill}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# SEO Analysis — {meta.get('final_url', '')}\n")
        f.write(f"**Skill:** {args.skill}  |  **Provider:** {args.provider}  |  **Model:** {model}\n\n")
        f.write("".join(result_text))

    print(f"Result saved → {out_path}")


if __name__ == "__main__":
    main()
