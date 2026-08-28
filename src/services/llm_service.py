"""
src/services/llm_service.py

Provider-neutral LLM boundary. Centralizes provider validation, API-key
checks, empty-response checks, retry/backoff, and the Gemini/Perplexity/
OpenAI adapters themselves, so report_service.py can call one dispatcher
instead of re-implementing provider branching and error handling.

Report generation calls are tool-free (the LLM only synthesizes supplied
evidence). Research calls always use each provider's live web search:
Gemini's `google_search` tool (Interactions API), OpenAI's `web_search`
tool (Responses API, forced via tool_choice="required"), and Perplexity's
sonar-pro model (which is always search-grounded).

Public interface:
    LLMProvider
    ResearchCitation
    ResearchResponse
    LLMProviderError
    resolve_provider(settings) -> LLMProvider
    require_api_key(provider, api_key, env_var_name) -> None
    require_nonempty_text(text, provider) -> str
    call_with_retry(make_call, is_transient, retry_attempts, backoff_base_seconds, description) -> T
    generate_text(system_prompt, user_message, settings) -> str
    research_with_web_search(system_prompt, user_message, settings) -> ResearchResponse
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from google import genai
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from src.config import Settings

logger = logging.getLogger(__name__)

_PERPLEXITY_BASE_URL = "https://api.perplexity.ai"
_PERPLEXITY_MAX_TOKENS = 16000  # sonar-pro needs an explicit limit for long reports

_GEMINI_SEARCH_TOOLS = [{"type": "google_search"}]

LLMProvider = Literal["perplexity", "gemini", "openai"]

_VALID_PROVIDERS: frozenset[str] = frozenset({"perplexity", "gemini", "openai"})

T = TypeVar("T")


class LLMProviderError(RuntimeError):
    """Raised when a configured LLM provider fails to return a usable response."""


@dataclass
class TokenUsage:
    """Token consumption and estimated USD cost for one or more LLM requests."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class LLMGenerationResult(str):
    """
    String result that also carries token usage and cost metadata.

    Subclasses str so existing callers and test assertions that expect
    a plain str work seamlessly without any breaking changes.
    """

    usage: TokenUsage

    def __new__(cls, text: str, usage: TokenUsage | None = None) -> "LLMGenerationResult":
        instance = super().__new__(cls, text)
        instance.usage = usage or TokenUsage()
        return instance


@dataclass
class ResearchCitation:
    """One real, resolvable source a provider's live web search actually returned."""

    url: str
    title: str


@dataclass
class ResearchResponse:
    """Normalized live-web-search result: a provider's answer text plus its real citations and usage."""

    text: str
    citations: list[ResearchCitation] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)


def calculate_cost_usd(
    provider: LLMProvider,
    model: str,
    input_tokens: int,
    output_tokens: int,
    search_calls: int = 0,
) -> float:
    """
    Calculate estimated cost in USD based on provider model token rates and search fees.
    """
    input_tokens = max(0, input_tokens)
    output_tokens = max(0, output_tokens)
    search_calls = max(0, search_calls)

    if provider == "perplexity":
        m = model.lower()
        if "sonar-pro" in m:
            in_rate, out_rate, req_fee = 3.0, 15.0, 0.006
        elif "sonar-reasoning" in m:
            in_rate, out_rate, req_fee = 2.0, 8.0, 0.006
        elif "sonar" in m:
            in_rate, out_rate, req_fee = 1.0, 1.0, 0.005
        else:
            in_rate, out_rate, req_fee = 3.0, 15.0, 0.006
        cost = (input_tokens / 1_000_000 * in_rate) + (output_tokens / 1_000_000 * out_rate) + req_fee
        return round(cost, 6)

    if provider == "gemini":
        m = model.lower()
        if "2.5-flash-lite" in m or "3.1-flash-lite" in m:
            in_rate, out_rate = 0.10, 0.40
        elif "2.5-flash" in m:
            in_rate, out_rate = 0.30, 2.50
        elif "3.7-flash" in m or "3.6-flash" in m:
            in_rate, out_rate = 0.75, 3.75
        elif "3.5-flash" in m:
            in_rate, out_rate = 1.50, 9.00
        elif "pro" in m:
            in_rate, out_rate = 2.00, 12.00
        elif "1.5-flash" in m:
            in_rate, out_rate = 0.075, 0.30
        else:
            in_rate, out_rate = 0.30, 2.50
        search_fee = (search_calls * 0.035) if search_calls > 0 else 0.0
        cost = (input_tokens / 1_000_000 * in_rate) + (output_tokens / 1_000_000 * out_rate) + search_fee
        return round(cost, 6)

    if provider == "openai":
        m = model.lower()
        if "gpt-5.6-luna" in m:
            in_rate, out_rate = 0.20, 1.20
        elif "gpt-5.6-terra" in m:
            in_rate, out_rate = 2.00, 12.00
        elif "gpt-5.6" in m or "gpt-5.6-sol" in m:
            in_rate, out_rate = 4.00, 20.00
        elif "gpt-4o-mini" in m:
            in_rate, out_rate = 0.15, 0.60
        elif "gpt-4o" in m:
            in_rate, out_rate = 2.50, 10.00
        elif "o3-mini" in m or "o4-mini" in m:
            in_rate, out_rate = 1.10, 4.40
        elif "o3" in m or "o1" in m:
            in_rate, out_rate = 15.00, 60.00
        else:
            in_rate, out_rate = 4.00, 20.00
        search_fee = search_calls * 0.010
        cost = (input_tokens / 1_000_000 * in_rate) + (output_tokens / 1_000_000 * out_rate) + search_fee
        return round(cost, 6)

    return 0.0


def resolve_provider(settings: Settings) -> LLMProvider:
    """
    Validate settings.llm_provider against the three supported providers.

    Raises:
        ValueError: If llm_provider is not "perplexity", "gemini", or "openai".
    """
    provider = settings.llm_provider
    if provider not in _VALID_PROVIDERS:
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{provider}'. Must be one of: {', '.join(sorted(_VALID_PROVIDERS))}."
        )
    return provider  # type: ignore[return-value]


def require_api_key(provider: LLMProvider, api_key: str, env_var_name: str) -> None:
    """
    Fail fast with a clear message when the selected provider's API key is missing.

    Raises:
        ValueError: If api_key is empty.
    """
    if not api_key:
        raise ValueError(
            f"{env_var_name} is not configured for LLM_PROVIDER={provider}. "
            f"Add it to the .env file: {env_var_name}=your_key_here"
        )


def require_nonempty_text(text: str | None, provider: LLMProvider) -> str:
    """
    Raises:
        LLMProviderError: If the provider returned an empty or missing response.
    """
    if not text:
        raise LLMProviderError(
            f"The {provider} LLM returned an empty response. "
            "This may occur if the request was blocked, or the API key/model is invalid."
        )
    return text


async def call_with_retry(
    make_call: Callable[[], Awaitable[T]],
    is_transient: Callable[[Exception], bool],
    retry_attempts: int,
    backoff_base_seconds: float,
    description: str,
) -> T:
    """
    Call make_call(), retrying transient failures with exponential backoff.

    Shared by every provider adapter so retry/backoff behavior lives in
    one place.

    Args:
        make_call: Zero-argument async callable to attempt (and retry).
        is_transient: Predicate deciding whether a raised exception is worth retrying.
        retry_attempts: Maximum attempts before giving up (1 = no retry).
        backoff_base_seconds: Base delay for exponential backoff between attempts.
        description: Human-readable label for log messages (e.g. "Gemini report generation").

    Raises:
        Exception: Re-raises the last exception once retries are exhausted,
            or immediately for any non-transient exception.
    """
    attempt = 1
    while True:
        try:
            return await make_call()
        except Exception as error:
            if attempt >= retry_attempts or not is_transient(error):
                raise
            delay = backoff_base_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Transient failure in %s (attempt %d/%d), retrying in %.1fs: %s",
                description, attempt, retry_attempts, delay, error,
            )
            await asyncio.sleep(delay)
            attempt += 1


async def generate_text(system_prompt: str, user_message: str, settings: Settings) -> LLMGenerationResult:
    """Generate report-writing text from the configured provider (no web search tool)."""
    provider = resolve_provider(settings)
    if provider == "perplexity":
        text, _citations, usage = await _call_perplexity(system_prompt, user_message, settings)
        return LLMGenerationResult(text, usage)
    if provider == "gemini":
        text, _citations, usage = await _call_gemini(system_prompt, user_message, settings, use_search=False)
        return LLMGenerationResult(text, usage)
    text, _citations, usage = await _call_openai(system_prompt, user_message, settings, use_search=False)
    return LLMGenerationResult(text, usage)


async def research_with_web_search(system_prompt: str, user_message: str, settings: Settings) -> ResearchResponse:
    """Run one live-web-search research call against the configured provider."""
    provider = resolve_provider(settings)
    if provider == "perplexity":
        text, citations, usage = await _call_perplexity(system_prompt, user_message, settings)
        return ResearchResponse(text=text, citations=citations, usage=usage)
    if provider == "gemini":
        text, citations, usage = await _call_gemini(system_prompt, user_message, settings, use_search=True)
        return ResearchResponse(text=text, citations=citations, usage=usage)
    text, citations, usage = await _call_openai(system_prompt, user_message, settings, use_search=True)
    return ResearchResponse(text=text, citations=citations, usage=usage)


# ---------------------------------------------------------------------------
# Shared helpers for the provider adapters below
# ---------------------------------------------------------------------------


def _is_transient_openai_error(error: Exception) -> bool:
    """True for rate limits, timeouts, connection errors, and 5xx — all worth retrying."""
    return isinstance(error, (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError))


def _extract_url_citations(annotated_blocks: list) -> list[ResearchCitation]:
    """Collect url_citation annotations from a list of {annotations: [...]} content blocks."""
    citations: list[ResearchCitation] = []
    for content_block in annotated_blocks:
        for annotation in getattr(content_block, "annotations", None) or []:
            if getattr(annotation, "type", None) == "url_citation":
                citations.append(ResearchCitation(url=annotation.url, title=annotation.title))
    return citations


# ---------------------------------------------------------------------------
# Perplexity adapter — sonar-pro is always search-grounded
# ---------------------------------------------------------------------------


async def _call_perplexity(
    system_prompt: str, user_message: str, settings: Settings,
) -> tuple[str, list[ResearchCitation], TokenUsage]:
    """Call Perplexity's chat-completions endpoint. Returns (text, citations, usage)."""
    require_api_key("perplexity", settings.perplexity_api_key, "PERPLEXITY_API_KEY")
    client = AsyncOpenAI(api_key=settings.perplexity_api_key, base_url=_PERPLEXITY_BASE_URL)

    async def make_call():
        return await client.chat.completions.create(
            model=settings.perplexity_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=_PERPLEXITY_MAX_TOKENS,
        )

    response = await call_with_retry(
        make_call, _is_transient_openai_error,
        settings.perplexity_retry_attempts, settings.perplexity_retry_backoff_base_seconds,
        "Perplexity call",
    )

    text = response.choices[0].message.content if response and response.choices else ""
    citation_urls = getattr(response, "citations", None) or []
    citations = [ResearchCitation(url=url, title="") for url in citation_urls]

    clean_text = require_nonempty_text(text, "perplexity")
    usage_obj = getattr(response, "usage", None)
    in_tok = getattr(usage_obj, "prompt_tokens", 0) or 0
    out_tok = getattr(usage_obj, "completion_tokens", 0) or 0
    if in_tok == 0 and out_tok == 0:
        in_tok = max(1, len(system_prompt + user_message) // 4)
        out_tok = max(1, len(clean_text) // 4)
    tot_tok = getattr(usage_obj, "total_tokens", in_tok + out_tok) or (in_tok + out_tok)
    cost = calculate_cost_usd("perplexity", settings.perplexity_model, in_tok, out_tok)
    usage = TokenUsage(input_tokens=in_tok, output_tokens=out_tok, total_tokens=tot_tok, estimated_cost_usd=cost)
    return clean_text, citations, usage


# ---------------------------------------------------------------------------
# Gemini adapter — google-genai Interactions API
# ---------------------------------------------------------------------------


async def _call_gemini(
    system_prompt: str, user_message: str, settings: Settings, use_search: bool,
) -> tuple[str, list[ResearchCitation], TokenUsage]:
    """Call Gemini via the Interactions API. Returns (text, citations, usage)."""
    require_api_key("gemini", settings.gemini_api_key, "GEMINI_API_KEY")
    client = genai.Client(api_key=settings.gemini_api_key)

    create_kwargs: dict = {
        "model": settings.gemini_model,
        "input": user_message,
        "system_instruction": system_prompt,
        "generation_config": {"thinking_level": settings.gemini_thinking_level},
    }
    if use_search:
        create_kwargs["tools"] = _GEMINI_SEARCH_TOOLS

    try:
        interaction = await asyncio.to_thread(client.interactions.create, **create_kwargs)
    except Exception as gemini_error:
        raise LLMProviderError(
            f"Gemini call failed: {gemini_error}. Check GEMINI_API_KEY in .env and verify the API is reachable."
        ) from gemini_error

    text = getattr(interaction, "output_text", "") or ""
    model_output_blocks = [
        content_block
        for step in getattr(interaction, "steps", None) or []
        if getattr(step, "type", None) == "model_output"
        for content_block in getattr(step, "content", None) or []
    ]
    citations = _extract_url_citations(model_output_blocks)
    clean_text = require_nonempty_text(text, "gemini")

    usage_metadata = getattr(interaction, "usage_metadata", None) or getattr(interaction, "usage", None)
    in_tok = getattr(usage_metadata, "prompt_token_count", 0) or getattr(usage_metadata, "input_token_count", 0) or 0
    out_tok = getattr(usage_metadata, "candidates_token_count", 0) or getattr(usage_metadata, "output_token_count", 0) or 0
    if in_tok == 0 and out_tok == 0:
        in_tok = max(1, len(system_prompt + user_message) // 4)
        out_tok = max(1, len(clean_text) // 4)
    tot_tok = getattr(usage_metadata, "total_token_count", in_tok + out_tok) or (in_tok + out_tok)
    cost = calculate_cost_usd("gemini", settings.gemini_model, in_tok, out_tok, search_calls=1 if use_search else 0)
    usage = TokenUsage(input_tokens=in_tok, output_tokens=out_tok, total_tokens=tot_tok, estimated_cost_usd=cost)
    return clean_text, citations, usage


# ---------------------------------------------------------------------------
# OpenAI adapter — Responses API with the web_search tool
# ---------------------------------------------------------------------------


async def _call_openai(
    system_prompt: str, user_message: str, settings: Settings, use_search: bool,
) -> tuple[str, list[ResearchCitation], TokenUsage]:
    """Call OpenAI's Responses API. Returns (text, citations, usage)."""
    require_api_key("openai", settings.openai_api_key, "OPENAI_API_KEY")
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    create_kwargs: dict = {
        "model": settings.openai_model,
        "instructions": system_prompt,
        "input": user_message,
        "reasoning": {"effort": settings.openai_reasoning_effort},
    }
    if use_search:
        create_kwargs["tools"] = [
            {"type": "web_search", "search_context_size": settings.openai_search_context_size}
        ]
        create_kwargs["tool_choice"] = "required"  # "auto" would make search optional; research requires it

    async def make_call():
        return await client.responses.create(**create_kwargs)

    response = await call_with_retry(
        make_call, _is_transient_openai_error,
        settings.llm_retry_attempts, settings.llm_retry_backoff_base_seconds,
        "OpenAI Responses call",
    )

    text = getattr(response, "output_text", "") or ""
    message_blocks = [
        content_block
        for item in getattr(response, "output", None) or []
        if getattr(item, "type", None) == "message"
        for content_block in getattr(item, "content", None) or []
    ]
    citations = _extract_url_citations(message_blocks)
    clean_text = require_nonempty_text(text, "openai")

    usage_obj = getattr(response, "usage", None)
    in_tok = getattr(usage_obj, "input_tokens", 0) or getattr(usage_obj, "prompt_tokens", 0) or 0
    out_tok = getattr(usage_obj, "output_tokens", 0) or getattr(usage_obj, "completion_tokens", 0) or 0
    if in_tok == 0 and out_tok == 0:
        in_tok = max(1, len(system_prompt + user_message) // 4)
        out_tok = max(1, len(clean_text) // 4)
    tot_tok = getattr(usage_obj, "total_tokens", in_tok + out_tok) or (in_tok + out_tok)
    cost = calculate_cost_usd("openai", settings.openai_model, in_tok, out_tok, search_calls=1 if use_search else 0)
    usage = TokenUsage(input_tokens=in_tok, output_tokens=out_tok, total_tokens=tot_tok, estimated_cost_usd=cost)
    return clean_text, citations, usage
