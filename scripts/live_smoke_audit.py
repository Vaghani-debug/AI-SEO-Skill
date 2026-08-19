"""
scripts/live_smoke_audit.py

Opt-in, real-network provider smoke audit (Plan Step 21).

This is NOT part of the pytest suite and must never run automatically: it
makes real, billed LLM/web-search API calls against Gemini, OpenAI, and
Perplexity. Run it manually, only after the full offline `test/` suite and
diagnostics pass.

Crawls one URL exactly once (crawling is provider-independent), then runs the
new report pipeline (build_audit_context -> generate_report_sections ->
assemble_and_validate_report) once per provider, repeated `--repeats` times
per provider so a median duration can be computed. Prints:
  - each provider's median/individual run durations
  - each run's validation verdict (is_valid + issues)
  - a heading/table-structure diff across providers, so factual/layout
    parity can be spot-checked separately from timing

Usage (PowerShell), from the project root, with the venv activated:
    $env:RUN_LIVE_SMOKE_AUDIT = "true"
    python scripts\\live_smoke_audit.py https://example.com --repeats 1

Requires GEMINI_API_KEY, OPENAI_API_KEY, and PERPLEXITY_API_KEY all
configured in .env (or the environment) — every provider is exercised
regardless of LLM_PROVIDER's configured default.
"""

import argparse
import asyncio
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # Allows `python scripts/live_smoke_audit.py` from anywhere

from src.config import Settings
from src.services.audit_models import SiteEvidence
from src.services.crawl_service import build_site_evidence
from src.services.prompt_loader import load_prompt_context, PromptContext
from src.services.report_service import (
    AssembledReportResult,
    assemble_and_validate_report,
    build_audit_context,
    generate_report_sections,
)
from src.services.url_service import normalize_and_validate

_PROVIDERS: tuple[str, ...] = ("gemini", "openai", "perplexity")


@dataclass
class ProviderRunResult:
    """One provider run's timing and assembled-report outcome."""

    provider: str
    duration_seconds: float
    assembled: AssembledReportResult


def _extract_headings(markdown_report: str) -> list[str]:
    """Return every PART/SECTION/subsection/table heading, in document order."""
    return re.findall(r"^#{1,3} .+$", markdown_report, re.MULTILINE)


async def _run_one_provider(
    normalized_url: str,
    provider: str,
    settings: Settings,
    site_evidence: SiteEvidence,
    prompt_context: PromptContext,
) -> ProviderRunResult:
    """Run one full report generation for one provider and time it."""
    run_settings = settings.model_copy(update={"llm_provider": provider})
    started = time.monotonic()
    context = await build_audit_context(normalized_url, site_evidence, run_settings)
    sections = await generate_report_sections(context, prompt_context, run_settings)
    assembled = assemble_and_validate_report(sections, prompt_context.master_report_structure, context)
    duration_seconds = time.monotonic() - started
    return ProviderRunResult(provider=provider, duration_seconds=duration_seconds, assembled=assembled)


async def run_smoke_audit(url: str, repeats: int) -> int:
    """Crawl once, then run every provider `repeats` times; print a comparison summary."""
    if os.environ.get("RUN_LIVE_SMOKE_AUDIT", "").lower() != "true":
        print(
            "Refusing to run: this script makes real, billed LLM API calls "
            "against Gemini, OpenAI, and Perplexity.\n"
            "Set RUN_LIVE_SMOKE_AUDIT=true to confirm you want to proceed.",
            file=sys.stderr,
        )
        return 1

    validation = normalize_and_validate(url)
    if not validation.is_valid:
        print(f"Invalid URL: {validation.error_message}", file=sys.stderr)
        return 1
    normalized_url = validation.normalized_url

    settings = Settings()
    prompt_context = load_prompt_context()

    print(f"Crawling {normalized_url} once (shared across all providers)...")
    site_evidence = await build_site_evidence(normalized_url, settings)

    results: dict[str, list[ProviderRunResult]] = {provider: [] for provider in _PROVIDERS}
    for provider in _PROVIDERS:
        for attempt in range(1, repeats + 1):
            print(f"[{provider}] run {attempt}/{repeats}...")
            result = await _run_one_provider(normalized_url, provider, settings, site_evidence, prompt_context)
            results[provider].append(result)
            print(f"[{provider}]   {result.duration_seconds:.1f}s, is_valid={result.assembled.is_valid}")

    print("\n=== Duration summary (median across repeats) ===")
    for provider in _PROVIDERS:
        durations = [run.duration_seconds for run in results[provider]]
        formatted_runs = ", ".join(f"{d:.1f}s" for d in durations)
        print(f"{provider:>10}: median={statistics.median(durations):.1f}s  runs=[{formatted_runs}]")

    print("\n=== Factual/layout parity (first run per provider) ===")
    headings_by_provider = {
        provider: _extract_headings(results[provider][0].assembled.markdown_report) for provider in _PROVIDERS
    }
    baseline_provider = _PROVIDERS[0]
    baseline_headings = headings_by_provider[baseline_provider]
    for provider in _PROVIDERS[1:]:
        if headings_by_provider[provider] == baseline_headings:
            print(f"{provider}: headings/table structure MATCH {baseline_provider}")
        else:
            print(f"{provider}: headings/table structure DIFFERS from {baseline_provider} — inspect manually:")
            print(f"  {baseline_provider}: {baseline_headings}")
            print(f"  {provider}: {headings_by_provider[provider]}")

    print("\n=== Validation issues per provider (first run) ===")
    for provider in _PROVIDERS:
        issues = results[provider][0].assembled.issues
        print(f"{provider}: {issues if issues else 'no issues'}")

    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="The URL to audit (the same URL is used for all three providers)")
    parser.add_argument("--repeats", type=int, default=1, help="Runs per provider, for a median duration (default 1)")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    sys.exit(asyncio.run(run_smoke_audit(args.url, args.repeats)))
