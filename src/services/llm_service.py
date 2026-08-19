"""
src/services/llm_service.py

Provider-neutral LLM boundary. Centralizes provider validation, API-key
checks, empty-response checks, retry/backoff, and the Gemini/Perplexity/
OpenAI adapters themselves, so report_service.py and research_service.py
can call one dispatcher instead of each re-implementing provider branching
and error handling.

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
class ResearchCitation:
    """One real, resolvable source a provider's live web search actually returned."""

    url: str
    title: str


@dataclass
class ResearchResponse:
    """Normalized live-web-search result: a provider's answer text plus its real citations."""

    text: str
    citations: list[ResearchCitation] = field(default_factory=list)


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

    Shared by every provider adapter so retry/backoff behavior (previously
    duplicated across fetch_service.py and research_service.py) lives in
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


async def generate_text(system_prompt: str, user_message: str, settings: Settings) -> str:
    """Generate report-writing text from the configured provider (no web search tool)."""
    provider = resolve_provider(settings)
    if provider == "perplexity":
        text, _citations = await _call_perplexity(system_prompt, user_message, settings)
        return text
    if provider == "gemini":
        text, _citations = await _call_gemini(system_prompt, user_message, settings, use_search=False)
        return text
    text, _citations = await _call_openai(system_prompt, user_message, settings, use_search=False)
    return text


async def research_with_web_search(system_prompt: str, user_message: str, settings: Settings) -> ResearchResponse:
    """Run one live-web-search research call against the configured provider."""
    provider = resolve_provider(settings)
    if provider == "perplexity":
        text, citations = await _call_perplexity(system_prompt, user_message, settings)
        return ResearchResponse(text=text, citations=citations)
    if provider == "gemini":
        text, citations = await _call_gemini(system_prompt, user_message, settings, use_search=True)
        return ResearchResponse(text=text, citations=citations)
    text, citations = await _call_openai(system_prompt, user_message, settings, use_search=True)
    return ResearchResponse(text=text, citations=citations)


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
) -> tuple[str, list[ResearchCitation]]:
    """Call Perplexity's chat-completions endpoint. Returns (text, citations)."""
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
    return require_nonempty_text(text, "perplexity"), citations


# ---------------------------------------------------------------------------
# Gemini adapter — google-genai Interactions API
# ---------------------------------------------------------------------------


async def _call_gemini(
    system_prompt: str, user_message: str, settings: Settings, use_search: bool,
) -> tuple[str, list[ResearchCitation]]:
    """Call Gemini via the Interactions API. Returns (text, citations)."""
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
    return require_nonempty_text(text, "gemini"), citations


# ---------------------------------------------------------------------------
# OpenAI adapter — Responses API with the web_search tool
# ---------------------------------------------------------------------------


async def _call_openai(
    system_prompt: str, user_message: str, settings: Settings, use_search: bool,
) -> tuple[str, list[ResearchCitation]]:
    """Call OpenAI's Responses API. Returns (text, citations)."""
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
    return require_nonempty_text(text, "openai"), citations
