# Access environment variables for provider/model/runtime configuration.
import os

# OpenAI client is used for OpenAI-compatible endpoints (OpenAI, Gemini, Perplexity).
from openai import OpenAI, OpenAIError


# Default provider when LLM_PROVIDER is not explicitly set.
DEFAULT_PROVIDER = "gemini"
# Default model for OpenAI provider.
DEFAULT_OPENAI_MODEL = "gpt-5.6"
# Default model for Gemini provider.
DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"
# Default model for Perplexity provider.
DEFAULT_PERPLEXITY_MODEL = "sonar-pro"
# Default sampling temperature for generation requests.
DEFAULT_TEMPERATURE = 0.2
# Default retry attempts per model before failing.
DEFAULT_MAX_RETRY_COUNT = 1


class AuditGenerationError(RuntimeError):
    # Domain-specific exception so API layer can map failures cleanly.
    """Raised when the audit report cannot be generated."""


def _env_float(name: str, default: float) -> float:
    # Read raw environment variable value.
    raw = os.getenv(name)
    # Use fallback default when variable is not present.
    if raw is None:
        return default

    try:
        # Convert configured value to float.
        return float(raw)
    except ValueError as exc:
        # Raise a clear validation error for misconfigured numeric fields.
        raise AuditGenerationError(f"{name} must be a valid float.") from exc


def _env_int(name: str, default: int) -> int:
    # Read raw environment variable value.
    raw = os.getenv(name)
    # Use fallback default when variable is not present.
    if raw is None:
        return default

    try:
        # Convert configured value to integer.
        return int(raw)
    except ValueError as exc:
        # Raise a clear validation error for misconfigured numeric fields.
        raise AuditGenerationError(f"{name} must be a valid integer.") from exc


def _resolve_provider() -> str:
    # Resolve provider from environment and normalize format.
    provider = (os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    # Enforce supported provider values.
    if provider not in {"gemini", "openai", "perplexity"}:
        raise AuditGenerationError(
            "LLM_PROVIDER must be one of: gemini, openai, perplexity"
        )
    # Return validated provider name.
    return provider


def _build_client(provider: str) -> tuple[OpenAI, str, str | None]:
    # Build OpenAI-compatible client and model settings for Gemini.
    if provider == "gemini":
        # Read Gemini API key.
        api_key = os.getenv("GEMINI_API_KEY")
        # Fail early when required key is missing.
        if not api_key:
            raise AuditGenerationError("GEMINI_API_KEY is not configured.")
        # Resolve primary Gemini model.
        model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        # Optional fallback model used if primary model fails.
        fallback_model = os.getenv("GEMINI_FALLBACK_MODEL")
        # Gemini OpenAI-compatible endpoint.
        client = OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        # Return client, primary model, and optional fallback model.
        return client, model, fallback_model

    # Build OpenAI client and model settings.
    if provider == "openai":
        # Read OpenAI key.
        api_key = os.getenv("OPENAI_API_KEY")
        # Fail early when required key is missing.
        if not api_key:
            raise AuditGenerationError("OPENAI_API_KEY is not configured.")
        # Guard against accidentally using a Perplexity key in OpenAI slot.
        if api_key.startswith("pplx-"):
            raise AuditGenerationError(
                "OPENAI_API_KEY appears to be a Perplexity key."
            )
        # Resolve primary OpenAI model.
        model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        # Optional fallback model used if primary model fails.
        fallback_model = os.getenv("OPENAI_FALLBACK_MODEL")
        # OpenAI endpoint client.
        client = OpenAI(api_key=api_key)
        # Return client, primary model, and optional fallback model.
        return client, model, fallback_model

    # Build OpenAI-compatible client and model settings for Perplexity.
    api_key = os.getenv("PERPLEXITY_API_KEY")
    # Fail early when required key is missing.
    if not api_key:
        raise AuditGenerationError("PERPLEXITY_API_KEY is not configured.")
    # Guard against accidentally using an OpenAI key in Perplexity slot.
    if api_key.startswith("sk-proj-"):
        raise AuditGenerationError(
            "PERPLEXITY_API_KEY appears to be an OpenAI key."
        )
    # Resolve primary Perplexity model.
    model = os.getenv("PERPLEXITY_MODEL", DEFAULT_PERPLEXITY_MODEL)
    # Optional fallback model used if primary model fails.
    fallback_model = os.getenv("PERPLEXITY_FALLBACK_MODEL")
    # Perplexity OpenAI-compatible endpoint.
    client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")
    # Return client, primary model, and optional fallback model.
    return client, model, fallback_model


def _openai_reasoning_effort() -> str | None:
    # Normalize optional OpenAI reasoning setting.
    raw = (os.getenv("OPENAI_REASONING_EFFORT") or "").strip().lower()
    # No value means do not send reasoning_effort parameter.
    if not raw:
        return None

    # Map custom max keyword to supported API value.
    if raw == "max":
        return "high"

    # Pass through allowed OpenAI reasoning effort values.
    if raw in {"minimal", "low", "medium", "high"}:
        return raw

    # Reject invalid values with clear guidance.
    raise AuditGenerationError(
        "OPENAI_REASONING_EFFORT must be one of: minimal, low, medium, high, max"
    )


def generate_audit_report(url: str, prompt_instruction: str) -> str:
    # Main orchestration entrypoint for LLM report generation.
    """Generate an SEO audit report in Markdown using the configured LLM provider."""
    # Determine which provider to use.
    provider = _resolve_provider()
    # Build client plus primary/fallback model settings for selected provider.
    client, primary_model, fallback_model = _build_client(provider)
    # Read temperature from environment with default fallback.
    temperature = _env_float("TEMPERATURE", DEFAULT_TEMPERATURE)
    # Read retries and enforce minimum of one attempt.
    max_retry_count = max(_env_int("MAX_RETRY_COUNT", DEFAULT_MAX_RETRY_COUNT), 1)

    # Start with primary model.
    models = [primary_model]
    # Add fallback model only if configured and different.
    if fallback_model and fallback_model != primary_model:
        models.append(fallback_model)

    # Track last provider exception for proper error chaining.
    last_error: OpenAIError | None = None

    # Try each model (primary first, then fallback if provided).
    for model in models:
        # Retry each model according to configured retry count.
        for _ in range(max_retry_count):
            try:
                # Build request payload shared across providers.
                request_kwargs: dict[str, object] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": prompt_instruction},
                        {"role": "user", "content": url},
                    ],
                    "temperature": temperature,
                }

                # Add OpenAI-specific reasoning setting when provider is OpenAI.
                if provider == "openai":
                    effort = _openai_reasoning_effort()
                    if effort:
                        request_kwargs["reasoning_effort"] = effort

                # Execute completion request against selected provider endpoint.
                response = client.chat.completions.create(**request_kwargs)
                # Extract and normalize generated markdown output.
                report = (response.choices[0].message.content or "").strip()
                # Return immediately on first non-empty report.
                if report:
                    return report
            except OpenAIError as exc:
                # Remember last provider error and continue retry/model fallback flow.
                last_error = exc
                continue

    # If provider returned an error at least once, raise provider failure error.
    if last_error is not None:
        raise AuditGenerationError(
            f"{provider.capitalize()} audit generation failed."
        ) from last_error

    # Otherwise, all attempts completed but produced empty content.
    raise AuditGenerationError(
        f"{provider.capitalize()} returned an empty audit report."
    )
