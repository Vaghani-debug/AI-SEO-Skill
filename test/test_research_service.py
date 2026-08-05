"""
test/test_research_service.py

Unit tests for src/services/research_service.py.

All Perplexity API calls are mocked so these tests run offline without
tokens. Each test exercises one specific behaviour: prompt construction is
not asserted in detail (that's a judgment call for the LLM), but response
parsing, citation enforcement, and graceful-failure behaviour are.

Run with:
    pytest test/test_research_service.py -v
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from src.config import Settings
from src.services.audit_models import PageEvidence, PageType, ResearchClaim, SiteEvidence
from src.services.research_service import (
    _call_perplexity_json,
    _parse_claims,
    classify_local_business,
    research_audience_expansion,
    research_authority_opportunities,
    research_brand_presence,
    research_competitor_analysis,
    research_competitors,
    research_keyword_opportunities,
    research_local_demand,
    research_site,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings() -> Settings:
    """Settings with a fake API key so the "not configured" short-circuit is skipped."""
    s = Settings()
    s.perplexity_api_key = "FAKE_API_KEY_FOR_TESTS"
    s.perplexity_model = "sonar-pro"
    return s


def _mock_response(content: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    return response


_VALID_CLAIMS_JSON = json.dumps([
    {
        "claim": "Estimated monthly search volume",
        "value": "1,000-10,000/mo",
        "source_url": "https://trends.example.com/report",
        "source_title": "Example Trends Report",
    },
])


# ---------------------------------------------------------------------------
# _parse_claims — normalization and citation enforcement
# ---------------------------------------------------------------------------

class TestParseClaims:

    def test_valid_json_array_parsed_into_claims(self) -> None:
        claims = _parse_claims(_VALID_CLAIMS_JSON)
        assert len(claims) == 1
        assert claims[0].claim == "Estimated monthly search volume"
        assert claims[0].value == "1,000-10,000/mo"
        assert claims[0].source_url == "https://trends.example.com/report"
        assert claims[0].source_title == "Example Trends Report"

    def test_retrieved_date_is_set(self) -> None:
        claims = _parse_claims(_VALID_CLAIMS_JSON)
        assert len(claims[0].retrieved_date) == 10  # YYYY-MM-DD

    def test_claim_missing_source_url_is_discarded(self) -> None:
        raw = json.dumps([{"claim": "Made up fact", "value": "123", "source_title": "Nowhere"}])
        assert _parse_claims(raw) == []

    def test_claim_with_non_http_source_url_is_discarded(self) -> None:
        raw = json.dumps([{"claim": "Fact", "value": "123", "source_url": "not-a-url", "source_title": "X"}])
        assert _parse_claims(raw) == []

    def test_claim_missing_claim_text_is_discarded(self) -> None:
        raw = json.dumps([{"value": "123", "source_url": "https://example.com", "source_title": "X"}])
        assert _parse_claims(raw) == []

    def test_claim_missing_value_is_discarded(self) -> None:
        raw = json.dumps([{"claim": "Fact", "source_url": "https://example.com", "source_title": "X"}])
        assert _parse_claims(raw) == []

    def test_empty_array_returns_empty_list(self) -> None:
        assert _parse_claims("[]") == []

    def test_empty_string_returns_empty_list(self) -> None:
        assert _parse_claims("") == []

    def test_malformed_json_returns_empty_list_not_raise(self) -> None:
        assert _parse_claims("{not valid json") == []

    def test_non_list_json_returns_empty_list(self) -> None:
        assert _parse_claims('{"claim": "not a list"}') == []

    def test_markdown_fenced_json_is_unwrapped(self) -> None:
        fenced = f"```json\n{_VALID_CLAIMS_JSON}\n```"
        claims = _parse_claims(fenced)
        assert len(claims) == 1

    def test_claims_capped_at_max_per_query(self) -> None:
        many_items = [
            {"claim": f"Claim {i}", "value": str(i), "source_url": f"https://example.com/{i}", "source_title": "X"}
            for i in range(20)
        ]
        claims = _parse_claims(json.dumps(many_items))
        assert len(claims) == 8

    def test_source_title_defaults_to_url_if_missing(self) -> None:
        raw = json.dumps([{"claim": "Fact", "value": "123", "source_url": "https://example.com"}])
        claims = _parse_claims(raw)
        assert claims[0].source_title == "https://example.com"


# ---------------------------------------------------------------------------
# Public research functions — mocked Perplexity client
# ---------------------------------------------------------------------------

class TestResearchFunctions:

    async def test_research_keyword_opportunities_returns_claims(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(_VALID_CLAIMS_JSON))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client):
            claims = await research_keyword_opportunities("https://example.com", "A local bakery", settings)
        assert len(claims) == 1

    async def test_research_competitors_returns_claims(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(_VALID_CLAIMS_JSON))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client):
            claims = await research_competitors("https://example.com", "A local bakery", settings)
        assert len(claims) == 1

    async def test_research_authority_opportunities_returns_claims(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(_VALID_CLAIMS_JSON))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client):
            claims = await research_authority_opportunities("https://example.com", "A local bakery", settings)
        assert len(claims) == 1

    async def test_research_local_demand_returns_claims(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(_VALID_CLAIMS_JSON))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client):
            claims = await research_local_demand("https://example.com", "A local bakery", "Austin, TX", settings)
        assert len(claims) == 1

    async def test_research_competitor_analysis_returns_claims(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(_VALID_CLAIMS_JSON))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client):
            claims = await research_competitor_analysis("https://example.com", ["Competitor A", "Competitor B"], settings)
        assert len(claims) == 1

    async def test_research_competitor_analysis_skips_call_with_no_competitors(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(_VALID_CLAIMS_JSON))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client):
            claims = await research_competitor_analysis("https://example.com", [], settings)
        assert claims == []
        mock_client.chat.completions.create.assert_not_called()

    async def test_missing_api_key_returns_empty_list_not_raise(self) -> None:
        s = Settings()
        s.perplexity_api_key = ""
        claims = await research_keyword_opportunities("https://example.com", "A local bakery", s)
        assert claims == []

    async def test_client_exception_returns_empty_list_not_raise(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("network down"))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client):
            claims = await research_keyword_opportunities("https://example.com", "A local bakery", settings)
        assert claims == []

    async def test_empty_llm_response_returns_empty_list(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(""))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client):
            claims = await research_keyword_opportunities("https://example.com", "A local bakery", settings)
        assert claims == []


# ---------------------------------------------------------------------------
# _call_perplexity_json() — transient-failure retry/backoff
# ---------------------------------------------------------------------------

def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.perplexity.ai/chat/completions")
    return RateLimitError("rate limited", response=httpx.Response(429, request=request), body=None)


def _timeout_error() -> APITimeoutError:
    return APITimeoutError(request=httpx.Request("POST", "https://api.perplexity.ai/chat/completions"))


def _connection_error() -> APIConnectionError:
    return APIConnectionError(request=httpx.Request("POST", "https://api.perplexity.ai/chat/completions"))


def _internal_server_error() -> InternalServerError:
    request = httpx.Request("POST", "https://api.perplexity.ai/chat/completions")
    return InternalServerError("server error", response=httpx.Response(500, request=request), body=None)


class TestCallPerplexityJsonRetry:

    async def test_retries_transient_failure_then_succeeds(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[_rate_limit_error(), _mock_response(_VALID_CLAIMS_JSON)],
        )
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client), \
             patch("src.services.research_service.asyncio.sleep", AsyncMock()):
            result = await _call_perplexity_json("system", "user", settings)
        assert result == _VALID_CLAIMS_JSON
        assert mock_client.chat.completions.create.call_count == 2

    @pytest.mark.parametrize("make_error", [_rate_limit_error, _timeout_error, _connection_error, _internal_server_error])
    async def test_gives_up_after_max_retry_attempts(self, settings: Settings, make_error) -> None:
        settings.perplexity_retry_attempts = 3
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[make_error(), make_error(), make_error()])
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client), \
             patch("src.services.research_service.asyncio.sleep", AsyncMock()) as mock_sleep:
            result = await _call_perplexity_json("system", "user", settings)
        assert result == ""
        assert mock_client.chat.completions.create.call_count == 3
        assert mock_sleep.await_count == 2  # backoff sleeps happen between attempts, not after the final one

    async def test_non_transient_error_is_not_retried(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("bad request"))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client), \
             patch("src.services.research_service.asyncio.sleep", AsyncMock()) as mock_sleep:
            result = await _call_perplexity_json("system", "user", settings)
        assert result == ""
        assert mock_client.chat.completions.create.call_count == 1
        mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# research_site() orchestrator — assembles the ResearchBundle
# ---------------------------------------------------------------------------

def _claim(label: str, value: str) -> ResearchClaim:
    return ResearchClaim(
        claim=label, value=value, source_url="https://example.com/source",
        source_title="Source", retrieved_date="2026-08-04",
    )


class TestResearchSiteOrchestrator:

    async def test_assembles_bundle_from_all_categories(self, settings: Settings) -> None:
        with (
            patch("src.services.research_service.research_keyword_opportunities",
                  AsyncMock(return_value=[_claim("Keyword", "sourdough bread austin")])),
            patch("src.services.research_service.research_competitors",
                  AsyncMock(return_value=[_claim("Competitor", "Joe's Bakery")])),
            patch("src.services.research_service.research_authority_opportunities",
                  AsyncMock(return_value=[_claim("Authority", "Local food blog")])),
            patch("src.services.research_service.research_brand_presence",
                  AsyncMock(return_value=[_claim("Brand Presence", "Listed on Yelp")])),
            patch("src.services.research_service.research_competitor_analysis",
                  AsyncMock(return_value=[_claim("Gap", "No online ordering")])) as mock_analysis,
            patch("src.services.research_service.research_local_demand",
                  AsyncMock(return_value=[_claim("Demand", "High")])) as mock_local,
            patch("src.services.research_service.research_audience_expansion", AsyncMock(return_value=[])) as mock_audience,
        ):
            bundle = await research_site(
                "https://example.com", "A local bakery", settings,
                is_local_business=True, city_or_region="Austin, TX",
            )

        assert len(bundle.keyword_opportunities) == 1
        assert len(bundle.competitors) == 1
        assert len(bundle.authority_opportunities) == 1
        assert len(bundle.brand_presence) == 1
        assert len(bundle.competitor_analysis) == 1
        assert len(bundle.local_demand) == 1
        assert bundle.audience_expansion == []
        mock_analysis.assert_awaited_once_with("https://example.com", ["Joe's Bakery"], settings)
        mock_local.assert_awaited_once_with("https://example.com", "A local bakery", "Austin, TX", settings)
        mock_audience.assert_not_awaited()

    async def test_audience_expansion_runs_when_not_local_business(self, settings: Settings) -> None:
        with (
            patch("src.services.research_service.research_keyword_opportunities", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_competitors", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_authority_opportunities", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_brand_presence", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_competitor_analysis", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_local_demand", AsyncMock(return_value=[])) as mock_local,
            patch("src.services.research_service.research_audience_expansion",
                  AsyncMock(return_value=[_claim("Segment", "Wholesale bakeries")])) as mock_audience,
        ):
            bundle = await research_site(
                "https://example.com", "A SaaS product", settings, is_local_business=False,
            )

        assert bundle.local_demand == []
        assert len(bundle.audience_expansion) == 1
        mock_local.assert_not_awaited()
        mock_audience.assert_awaited_once_with("https://example.com", "A SaaS product", settings)

    async def test_local_business_without_region_falls_back_to_audience_expansion(self, settings: Settings) -> None:
        with (
            patch("src.services.research_service.research_keyword_opportunities", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_competitors", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_authority_opportunities", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_brand_presence", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_competitor_analysis", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_local_demand", AsyncMock(return_value=[])) as mock_local,
            patch("src.services.research_service.research_audience_expansion", AsyncMock(return_value=[])) as mock_audience,
        ):
            bundle = await research_site(
                "https://example.com", "A local bakery", settings, is_local_business=True, city_or_region=None,
            )

        assert bundle.local_demand == []
        mock_local.assert_not_awaited()
        mock_audience.assert_awaited_once_with("https://example.com", "A local bakery", settings)

    async def test_competitor_analysis_receives_no_names_when_no_competitors_found(self, settings: Settings) -> None:
        with (
            patch("src.services.research_service.research_keyword_opportunities", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_competitors", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_authority_opportunities", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_brand_presence", AsyncMock(return_value=[])),
            patch("src.services.research_service.research_competitor_analysis", AsyncMock(return_value=[])) as mock_analysis,
            patch("src.services.research_service.research_audience_expansion", AsyncMock(return_value=[])),
        ):
            await research_site("https://example.com", "A local bakery", settings)

        mock_analysis.assert_awaited_once_with("https://example.com", [], settings)


# ---------------------------------------------------------------------------
# research_audience_expansion()
# ---------------------------------------------------------------------------

class TestResearchAudienceExpansion:

    async def test_returns_parsed_claims(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(_VALID_CLAIMS_JSON))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client):
            claims = await research_audience_expansion("https://example.com", "A SaaS product", settings)
        assert len(claims) == 1

    async def test_missing_api_key_returns_empty_list(self) -> None:
        empty_settings = Settings(perplexity_api_key="", perplexity_model="sonar-pro")
        claims = await research_audience_expansion("https://example.com", "A SaaS product", empty_settings)
        assert claims == []


# ---------------------------------------------------------------------------
# research_brand_presence()
# ---------------------------------------------------------------------------

class TestResearchBrandPresence:

    async def test_returns_parsed_claims(self, settings: Settings) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=_mock_response(_VALID_CLAIMS_JSON))
        with patch("src.services.research_service.AsyncOpenAI", return_value=mock_client):
            claims = await research_brand_presence("https://example.com", "A local bakery", settings)
        assert len(claims) == 1

    async def test_missing_api_key_returns_empty_list(self) -> None:
        empty_settings = Settings(perplexity_api_key="", perplexity_model="sonar-pro")
        claims = await research_brand_presence("https://example.com", "A local bakery", empty_settings)
        assert claims == []


# ---------------------------------------------------------------------------
# classify_local_business() — deterministic, evidence-only classification
# ---------------------------------------------------------------------------

class TestClassifyLocalBusiness:

    def _make_page(self, **overrides) -> PageEvidence:
        defaults = dict(
            url="https://example.com/",
            page_type=PageType.CORE,
            http_status=200,
            is_https=True,
            used_playwright_fallback=False,
            page_title="Example",
            meta_description="An example page.",
            canonical_url="https://example.com/",
            page_language="en",
        )
        defaults.update(overrides)
        return PageEvidence(**defaults)

    def test_no_local_signals_returns_false_and_no_region(self) -> None:
        homepage = self._make_page()
        evidence = SiteEvidence(base_url="https://example.com", final_url="https://example.com/", homepage=homepage)
        is_local, region = classify_local_business(evidence)
        assert is_local is False
        assert region is None

    def test_local_business_schema_type_on_homepage_marks_local(self) -> None:
        homepage = self._make_page(schema_types=["LocalBusiness"])
        evidence = SiteEvidence(base_url="https://example.com", final_url="https://example.com/", homepage=homepage)
        is_local, region = classify_local_business(evidence)
        assert is_local is True

    def test_location_page_marks_local_and_extracts_region_from_h1(self) -> None:
        homepage = self._make_page()
        location_page = self._make_page(
            url="https://example.com/locations/austin",
            page_type=PageType.LOCATION,
            h1_tags=["Serving Austin, TX"],
        )
        evidence = SiteEvidence(
            base_url="https://example.com", final_url="https://example.com/",
            homepage=homepage, sampled_pages=[location_page],
        )
        is_local, region = classify_local_business(evidence)
        assert is_local is True
        assert region == "Serving Austin, TX"

    def test_location_page_without_h1_falls_back_to_page_title(self) -> None:
        homepage = self._make_page()
        location_page = self._make_page(
            url="https://example.com/locations/austin",
            page_type=PageType.LOCATION,
            page_title="Austin Location",
        )
        evidence = SiteEvidence(
            base_url="https://example.com", final_url="https://example.com/",
            homepage=homepage, sampled_pages=[location_page],
        )
        is_local, region = classify_local_business(evidence)
        assert is_local is True
        assert region == "Austin Location"
