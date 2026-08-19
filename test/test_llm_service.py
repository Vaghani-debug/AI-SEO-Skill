"""
test/test_llm_service.py

Unit tests for src/services/llm_service.py.

All provider network calls are mocked (AsyncOpenAI for Perplexity/OpenAI,
genai.Client for Gemini) so these tests run offline without tokens.

Run with:
    pytest test/test_llm_service.py -v
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, AuthenticationError, InternalServerError, RateLimitError

from src.config import Settings
from src.services.llm_service import (
    LLMProviderError,
    ResearchCitation,
    ResearchResponse,
    _call_gemini,
    _call_openai,
    _call_perplexity,
    _extract_url_citations,
    _is_transient_openai_error,
    call_with_retry,
    generate_text,
    require_api_key,
    require_nonempty_text,
    research_with_web_search,
    resolve_provider,
)


@pytest.fixture
def settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# resolve_provider()
# ---------------------------------------------------------------------------

class TestResolveProvider:

    @pytest.mark.parametrize("provider", ["perplexity", "gemini", "openai"])
    def test_valid_providers_are_returned_unchanged(self, settings: Settings, provider: str) -> None:
        settings.llm_provider = provider
        assert resolve_provider(settings) == provider

    def test_invalid_provider_raises_value_error(self, settings: Settings) -> None:
        settings.llm_provider = "claude"
        with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
            resolve_provider(settings)


# ---------------------------------------------------------------------------
# require_api_key()
# ---------------------------------------------------------------------------

class TestRequireApiKey:

    def test_missing_key_raises_value_error_naming_env_var(self) -> None:
        with pytest.raises(ValueError, match="OPENAI_API_KEY is not configured"):
            require_api_key("openai", "", "OPENAI_API_KEY")

    def test_present_key_does_not_raise(self) -> None:
        require_api_key("openai", "sk-real-key", "OPENAI_API_KEY")


# ---------------------------------------------------------------------------
# require_nonempty_text()
# ---------------------------------------------------------------------------

class TestRequireNonemptyText:

    def test_empty_text_raises_llm_provider_error(self) -> None:
        with pytest.raises(LLMProviderError, match="gemini"):
            require_nonempty_text("", "gemini")

    def test_none_text_raises_llm_provider_error(self) -> None:
        with pytest.raises(LLMProviderError):
            require_nonempty_text(None, "gemini")

    def test_nonempty_text_is_returned(self) -> None:
        assert require_nonempty_text("Report body", "gemini") == "Report body"


# ---------------------------------------------------------------------------
# call_with_retry()
# ---------------------------------------------------------------------------

class TestCallWithRetry:

    async def test_succeeds_on_first_attempt(self) -> None:
        make_call = AsyncMock(return_value="ok")
        result = await call_with_retry(make_call, lambda e: True, retry_attempts=3, backoff_base_seconds=1.0, description="test")
        assert result == "ok"
        assert make_call.call_count == 1

    async def test_retries_transient_failure_then_succeeds(self) -> None:
        make_call = AsyncMock(side_effect=[RuntimeError("transient"), "ok"])
        with patch("src.services.llm_service.asyncio.sleep", AsyncMock()):
            result = await call_with_retry(
                make_call, lambda e: True, retry_attempts=3, backoff_base_seconds=1.0, description="test",
            )
        assert result == "ok"
        assert make_call.call_count == 2

    async def test_gives_up_after_max_retry_attempts(self) -> None:
        make_call = AsyncMock(side_effect=RuntimeError("always fails"))
        with patch("src.services.llm_service.asyncio.sleep", AsyncMock()) as mock_sleep:
            with pytest.raises(RuntimeError, match="always fails"):
                await call_with_retry(
                    make_call, lambda e: True, retry_attempts=3, backoff_base_seconds=1.0, description="test",
                )
        assert make_call.call_count == 3
        assert mock_sleep.await_count == 2

    async def test_non_transient_error_is_not_retried(self) -> None:
        make_call = AsyncMock(side_effect=RuntimeError("permanent"))
        with patch("src.services.llm_service.asyncio.sleep", AsyncMock()) as mock_sleep:
            with pytest.raises(RuntimeError, match="permanent"):
                await call_with_retry(
                    make_call, lambda e: False, retry_attempts=3, backoff_base_seconds=1.0, description="test",
                )
        assert make_call.call_count == 1
        mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# ResearchCitation / ResearchResponse
# ---------------------------------------------------------------------------

class TestResearchResponse:

    def test_defaults_to_empty_citations(self) -> None:
        response = ResearchResponse(text="Some findings")
        assert response.text == "Some findings"
        assert response.citations == []

    def test_holds_provided_citations(self) -> None:
        citation = ResearchCitation(url="https://example.com/source", title="Example Source")
        response = ResearchResponse(text="Some findings", citations=[citation])
        assert response.citations[0].url == "https://example.com/source"
        assert response.citations[0].title == "Example Source"


# ---------------------------------------------------------------------------
# Perplexity adapter
# ---------------------------------------------------------------------------


def _fake_perplexity_response(content: str, citation_urls: list[str] | None = None):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], citations=citation_urls or [])


class TestCallPerplexity:

    async def test_missing_api_key_raises_value_error(self, settings: Settings) -> None:
        settings.perplexity_api_key = ""
        with pytest.raises(ValueError, match="PERPLEXITY_API_KEY is not configured"):
            await _call_perplexity("system", "user", settings)

    @patch("src.services.llm_service.AsyncOpenAI")
    async def test_success_returns_text_and_citations(self, mock_async_openai, settings: Settings) -> None:
        settings.perplexity_api_key = "test-key"
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_fake_perplexity_response("Report body", ["https://example.com/a"])
        )
        mock_async_openai.return_value = mock_client

        text, citations = await _call_perplexity("system", "user", settings)

        assert text == "Report body"
        assert citations == [ResearchCitation(url="https://example.com/a", title="")]
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == settings.perplexity_model
        assert call_kwargs["messages"][0] == {"role": "system", "content": "system"}

    @patch("src.services.llm_service.AsyncOpenAI")
    async def test_empty_response_raises_llm_provider_error(self, mock_async_openai, settings: Settings) -> None:
        settings.perplexity_api_key = "test-key"
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_fake_perplexity_response(""))
        mock_async_openai.return_value = mock_client

        with pytest.raises(LLMProviderError, match="perplexity"):
            await _call_perplexity("system", "user", settings)

    @patch("src.services.llm_service.AsyncOpenAI")
    async def test_missing_citations_attribute_yields_empty_list(self, mock_async_openai, settings: Settings) -> None:
        settings.perplexity_api_key = "test-key"
        message = SimpleNamespace(content="Report body")
        choice = SimpleNamespace(message=message)
        response_without_citations = SimpleNamespace(choices=[choice])  # no `citations` attribute at all
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=response_without_citations)
        mock_async_openai.return_value = mock_client

        text, citations = await _call_perplexity("system", "user", settings)

        assert text == "Report body"
        assert citations == []

    @patch("src.services.llm_service.AsyncOpenAI")
    async def test_transient_failure_is_retried_then_succeeds(self, mock_async_openai, settings: Settings) -> None:
        settings.perplexity_api_key = "test-key"
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[
                APIConnectionError(request=httpx.Request("POST", "https://api.perplexity.ai")),
                _fake_perplexity_response("Report body", ["https://example.com/a"]),
            ]
        )
        mock_async_openai.return_value = mock_client

        with patch("src.services.llm_service.asyncio.sleep", AsyncMock()):
            text, citations = await _call_perplexity("system", "user", settings)

        assert text == "Report body"
        assert mock_client.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
# Gemini adapter
# ---------------------------------------------------------------------------


def _fake_gemini_interaction(output_text: str, citations: list[ResearchCitation] | None = None):
    annotations = [SimpleNamespace(type="url_citation", url=c.url, title=c.title) for c in (citations or [])]
    content_block = SimpleNamespace(type="text", text=output_text, annotations=annotations)
    model_output_step = SimpleNamespace(type="model_output", content=[content_block])
    return SimpleNamespace(output_text=output_text, steps=[model_output_step])


class TestCallGemini:

    async def test_missing_api_key_raises_value_error(self, settings: Settings) -> None:
        settings.gemini_api_key = ""
        with pytest.raises(ValueError, match="GEMINI_API_KEY is not configured"):
            await _call_gemini("system", "user", settings, use_search=False)

    @patch("src.services.llm_service.genai.Client")
    async def test_success_without_search_omits_tools(self, mock_client_cls, settings: Settings) -> None:
        settings.gemini_api_key = "test-key"
        mock_client = MagicMock()
        mock_client.interactions.create = MagicMock(return_value=_fake_gemini_interaction("Report body"))
        mock_client_cls.return_value = mock_client

        text, citations = await _call_gemini("system", "user", settings, use_search=False)

        assert text == "Report body"
        assert citations == []
        assert "tools" not in mock_client.interactions.create.call_args.kwargs

    @patch("src.services.llm_service.genai.Client")
    async def test_success_with_search_includes_tools_and_citations(self, mock_client_cls, settings: Settings) -> None:
        settings.gemini_api_key = "test-key"
        citation = ResearchCitation(url="https://example.com/b", title="Example")
        mock_client = MagicMock()
        mock_client.interactions.create = MagicMock(
            return_value=_fake_gemini_interaction("Findings", citations=[citation])
        )
        mock_client_cls.return_value = mock_client

        text, citations = await _call_gemini("system", "user", settings, use_search=True)

        assert text == "Findings"
        assert citations == [citation]
        assert mock_client.interactions.create.call_args.kwargs["tools"] == [{"type": "google_search"}]

    @patch("src.services.llm_service.genai.Client")
    async def test_sdk_exception_wrapped_in_llm_provider_error(self, mock_client_cls, settings: Settings) -> None:
        settings.gemini_api_key = "test-key"
        mock_client = MagicMock()
        mock_client.interactions.create = MagicMock(side_effect=RuntimeError("boom"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(LLMProviderError, match="Gemini call failed"):
            await _call_gemini("system", "user", settings, use_search=False)


# ---------------------------------------------------------------------------
# OpenAI adapter
# ---------------------------------------------------------------------------


def _fake_openai_response(output_text: str, citations: list[ResearchCitation] | None = None):
    annotations = [SimpleNamespace(type="url_citation", url=c.url, title=c.title) for c in (citations or [])]
    content_block = SimpleNamespace(type="output_text", text=output_text, annotations=annotations)
    message_item = SimpleNamespace(type="message", content=[content_block])
    return SimpleNamespace(output_text=output_text, output=[message_item])


class TestCallOpenAI:

    async def test_missing_api_key_raises_value_error(self, settings: Settings) -> None:
        settings.openai_api_key = ""
        with pytest.raises(ValueError, match="OPENAI_API_KEY is not configured"):
            await _call_openai("system", "user", settings, use_search=False)

    @patch("src.services.llm_service.AsyncOpenAI")
    async def test_success_without_search_omits_tools(self, mock_async_openai, settings: Settings) -> None:
        settings.openai_api_key = "test-key"
        mock_client = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=_fake_openai_response("Report body"))
        mock_async_openai.return_value = mock_client

        text, citations = await _call_openai("system", "user", settings, use_search=False)

        assert text == "Report body"
        assert citations == []
        call_kwargs = mock_client.responses.create.call_args.kwargs
        assert "tools" not in call_kwargs
        assert "tool_choice" not in call_kwargs
        assert call_kwargs["instructions"] == "system"

    @patch("src.services.llm_service.AsyncOpenAI")
    async def test_success_with_search_forces_tool_choice(self, mock_async_openai, settings: Settings) -> None:
        settings.openai_api_key = "test-key"
        citation = ResearchCitation(url="https://example.com/c", title="Example C")
        mock_client = MagicMock()
        mock_client.responses.create = AsyncMock(
            return_value=_fake_openai_response("Findings", citations=[citation])
        )
        mock_async_openai.return_value = mock_client

        text, citations = await _call_openai("system", "user", settings, use_search=True)

        assert text == "Findings"
        assert citations == [citation]
        call_kwargs = mock_client.responses.create.call_args.kwargs
        assert call_kwargs["tool_choice"] == "required"
        assert call_kwargs["tools"] == [{"type": "web_search", "search_context_size": "high"}]

    @patch("src.services.llm_service.AsyncOpenAI")
    async def test_empty_response_raises_llm_provider_error(self, mock_async_openai, settings: Settings) -> None:
        settings.openai_api_key = "test-key"
        mock_client = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=_fake_openai_response(""))
        mock_async_openai.return_value = mock_client

        with pytest.raises(LLMProviderError, match="openai"):
            await _call_openai("system", "user", settings, use_search=False)

    @patch("src.services.llm_service.AsyncOpenAI")
    async def test_transient_failure_is_retried_then_succeeds(self, mock_async_openai, settings: Settings) -> None:
        settings.openai_api_key = "test-key"
        response = httpx.Response(503, request=httpx.Request("POST", "https://api.openai.com"))
        mock_client = MagicMock()
        mock_client.responses.create = AsyncMock(
            side_effect=[
                InternalServerError("service unavailable", response=response, body=None),
                _fake_openai_response("Report body"),
            ]
        )
        mock_async_openai.return_value = mock_client

        with patch("src.services.llm_service.asyncio.sleep", AsyncMock()):
            text, citations = await _call_openai("system", "user", settings, use_search=False)

        assert text == "Report body"
        assert mock_client.responses.create.call_count == 2


# ---------------------------------------------------------------------------
# generate_text() / research_with_web_search() — provider dispatch
# ---------------------------------------------------------------------------

class TestDispatch:

    async def test_generate_text_validates_provider_before_dispatch(self, settings: Settings) -> None:
        settings.llm_provider = "claude"
        with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
            await generate_text("system", "user", settings)

    async def test_research_with_web_search_validates_provider_before_dispatch(self, settings: Settings) -> None:
        settings.llm_provider = "claude"
        with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
            await research_with_web_search("system", "user", settings)

    @patch("src.services.llm_service._call_openai", new_callable=AsyncMock)
    async def test_generate_text_calls_openai_without_search(self, mock_call_openai, settings: Settings) -> None:
        settings.llm_provider = "openai"
        mock_call_openai.return_value = ("Report body", [])

        text = await generate_text("system", "user", settings)

        assert text == "Report body"
        mock_call_openai.assert_awaited_once_with("system", "user", settings, use_search=False)

    @patch("src.services.llm_service._call_gemini", new_callable=AsyncMock)
    async def test_research_with_web_search_calls_gemini_with_search(self, mock_call_gemini, settings: Settings) -> None:
        settings.llm_provider = "gemini"
        citation = ResearchCitation(url="https://example.com/d", title="Example D")
        mock_call_gemini.return_value = ("Findings", [citation])

        result = await research_with_web_search("system", "user", settings)

        assert result == ResearchResponse(text="Findings", citations=[citation])
        mock_call_gemini.assert_awaited_once_with("system", "user", settings, use_search=True)

    @patch("src.services.llm_service._call_perplexity", new_callable=AsyncMock)
    async def test_research_with_web_search_calls_perplexity(self, mock_call_perplexity, settings: Settings) -> None:
        settings.llm_provider = "perplexity"
        mock_call_perplexity.return_value = ("Findings", [])

        result = await research_with_web_search("system", "user", settings)

        assert result == ResearchResponse(text="Findings", citations=[])
        mock_call_perplexity.assert_awaited_once_with("system", "user", settings)


# ---------------------------------------------------------------------------
# _is_transient_openai_error() — shared retry predicate for OpenAI/Perplexity
# ---------------------------------------------------------------------------

class TestIsTransientOpenAIError:

    @pytest.mark.parametrize(
        "make_error",
        [
            lambda: RateLimitError(
                "rate limited",
                response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com")),
                body=None,
            ),
            lambda: APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")),
            lambda: APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")),
            lambda: InternalServerError(
                "server error",
                response=httpx.Response(500, request=httpx.Request("POST", "https://api.openai.com")),
                body=None,
            ),
        ],
    )
    def test_transient_error_types_return_true(self, make_error) -> None:
        assert _is_transient_openai_error(make_error()) is True

    def test_authentication_error_is_not_transient(self) -> None:
        error = AuthenticationError(
            "invalid key",
            response=httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com")),
            body=None,
        )
        assert _is_transient_openai_error(error) is False

    def test_unrelated_exception_is_not_transient(self) -> None:
        assert _is_transient_openai_error(ValueError("not an openai error")) is False


# ---------------------------------------------------------------------------
# _extract_url_citations() — shared citation parsing for Gemini/OpenAI blocks
# ---------------------------------------------------------------------------

class TestExtractUrlCitations:

    def test_empty_block_list_returns_empty_citations(self) -> None:
        assert _extract_url_citations([]) == []

    def test_block_missing_annotations_attribute_is_skipped(self) -> None:
        block = SimpleNamespace(type="text")  # no `annotations` attribute at all
        assert _extract_url_citations([block]) == []

    def test_non_url_citation_annotations_are_ignored(self) -> None:
        block = SimpleNamespace(annotations=[SimpleNamespace(type="file_citation", url="https://example.com/x", title="")])
        assert _extract_url_citations([block]) == []

    def test_multiple_blocks_are_aggregated_in_order(self) -> None:
        block_one = SimpleNamespace(
            annotations=[SimpleNamespace(type="url_citation", url="https://example.com/1", title="One")]
        )
        block_two = SimpleNamespace(
            annotations=[SimpleNamespace(type="url_citation", url="https://example.com/2", title="Two")]
        )

        citations = _extract_url_citations([block_one, block_two])

        assert citations == [
            ResearchCitation(url="https://example.com/1", title="One"),
            ResearchCitation(url="https://example.com/2", title="Two"),
        ]

