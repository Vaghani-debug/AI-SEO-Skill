"""
test/test_research_service.py

Unit tests for src/services/research_service.py.

research_with_web_search() (src/services/llm_service.py) is mocked so these
tests run offline without tokens and independent of which LLM_PROVIDER is
configured — provider-specific behaviour is covered in test_llm_service.py.
Each test exercises one specific behaviour: prompt construction is not
asserted in detail (that's a judgment call for the LLM), but response
parsing, citation enforcement, and graceful-failure behaviour are.

Run with:
    pytest test/test_research_service.py -v
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.config import Settings
from src.services.audit_models import (
    CompetitorGap,
    CompetitorGapResult,
    CompetitorOverview,
    CompetitorResearchResult,
    KeywordOpportunity,
    KeywordResearchResult,
    LocationOpportunity,
    LocationResearchResult,
    PageEvidence,
    PageType,
    ResearchClaim,
    ResearchResult,
    ResearchStatus,
    SiteEvidence,
)
from src.services.llm_service import LLMProviderError, ResearchCitation, ResearchResponse
from src.services.research_service import (
    _normalize_url,
    _parse_claims,
    _parse_competitor_gaps,
    _parse_competitors,
    _parse_keyword_opportunities,
    _parse_location_opportunities,
    _run_competitor_gap_query,
    _run_competitor_query,
    _run_keyword_query,
    _run_location_query,
    _run_research_query,
    classify_local_business,
    research_audience_expansion,
    research_authority_opportunities,
    research_brand_presence,
    research_competitor_analysis,
    research_competitors,
    research_local_demand,
    research_long_tail_keywords,
    research_primary_keywords,
    research_site,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings() -> Settings:
    """Plain settings; provider selection is irrelevant since research_with_web_search() is mocked directly."""
    return Settings()


_VALID_CLAIMS_JSON = json.dumps([
    {
        "claim": "Estimated monthly search volume",
        "value": "1,000-10,000/mo",
        "source_url": "https://trends.example.com/report",
        "source_title": "Example Trends Report",
    },
])

# The citation that makes _VALID_CLAIMS_JSON's one claim pass citation enforcement
_MATCHING_CITATION = ResearchCitation(url="https://trends.example.com/report", title="Example Trends Report")

_VALID_KEYWORD_JSON = json.dumps([
    {
        "keyword": "sourdough bread austin",
        "search_intent": "commercial",
        "estimated_volume": "1,000-10,000/mo",
        "target_page": "/bread",
        "source_url": "https://trends.example.com/report",
        "source_title": "Example Trends Report",
    },
])

_VALID_COMPETITOR_JSON = json.dumps([
    {
        "competitor_name": "Joe's Bakery",
        "website": "https://joesbakery.com",
        "focus": "Wholesale bread",
        "estimated_authority": "High",
        "source_url": "https://joesbakery.com",
        "source_title": "Joe's Bakery",
    },
])

# The citation that makes _VALID_COMPETITOR_JSON's competitor pass citation enforcement on its website field
_COMPETITOR_CITATION = ResearchCitation(url="https://joesbakery.com", title="Joe's Bakery")

_VALID_COMPETITOR_GAP_JSON = json.dumps([
    {
        "keyword": "artisan bread austin",
        "competitor_position": "Joe's Bakery ranks #2 for this term",
        "your_gap": "No dedicated landing page",
        "source_url": "https://trends.example.com/report",
        "source_title": "Example Trends Report",
    },
])

_VALID_LOCATION_JSON = json.dumps([
    {
        "primary_keyword": "bakery near me",
        "estimated_volume": "1,000-10,000/mo",
        "priority": "High",
        "source_url": "https://trends.example.com/report",
        "source_title": "Example Trends Report",
    },
])


def _research_response(text: str, citations: list[ResearchCitation] | None = None) -> ResearchResponse:
    """Build a ResearchResponse; defaults to a citation matching _VALID_CLAIMS_JSON so happy-path tests are terse."""
    return ResearchResponse(text=text, citations=citations if citations is not None else [_MATCHING_CITATION])


# ---------------------------------------------------------------------------
# _parse_claims — normalization and citation enforcement
# ---------------------------------------------------------------------------

class TestParseClaims:

    def test_valid_json_array_parsed_into_claims(self) -> None:
        result = _parse_claims(_VALID_CLAIMS_JSON, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.claims) == 1
        assert result.claims[0].claim == "Estimated monthly search volume"
        assert result.claims[0].value == "1,000-10,000/mo"
        assert result.claims[0].source_url == "https://trends.example.com/report"
        assert result.claims[0].source_title == "Example Trends Report"

    def test_retrieved_date_is_set(self) -> None:
        result = _parse_claims(_VALID_CLAIMS_JSON, [_MATCHING_CITATION])
        assert len(result.claims[0].retrieved_date) == 10  # YYYY-MM-DD

    def test_claim_missing_source_url_is_discarded(self) -> None:
        raw = json.dumps([{"claim": "Made up fact", "value": "123", "source_title": "Nowhere"}])
        result = _parse_claims(raw, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.CITATION_FAILED
        assert result.claims == []

    def test_claim_with_non_http_source_url_is_discarded(self) -> None:
        raw = json.dumps([{"claim": "Fact", "value": "123", "source_url": "not-a-url", "source_title": "X"}])
        result = _parse_claims(raw, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.CITATION_FAILED
        assert result.claims == []

    def test_claim_missing_claim_text_is_discarded(self) -> None:
        raw = json.dumps([{"value": "123", "source_url": "https://example.com", "source_title": "X"}])
        result = _parse_claims(raw, [ResearchCitation(url="https://example.com", title="X")])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.claims == []

    def test_claim_missing_value_is_discarded(self) -> None:
        raw = json.dumps([{"claim": "Fact", "source_url": "https://example.com", "source_title": "X"}])
        result = _parse_claims(raw, [ResearchCitation(url="https://example.com", title="X")])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.claims == []

    def test_empty_array_returns_no_results_status(self) -> None:
        result = _parse_claims("[]", [_MATCHING_CITATION])
        assert result.status == ResearchStatus.NO_RESULTS
        assert result.claims == []

    def test_empty_string_returns_no_results_status(self) -> None:
        result = _parse_claims("", [_MATCHING_CITATION])
        assert result.status == ResearchStatus.NO_RESULTS
        assert result.claims == []

    def test_malformed_json_returns_parse_failed_not_raise(self) -> None:
        result = _parse_claims("{not valid json", [_MATCHING_CITATION])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.claims == []
        assert result.error is not None

    def test_non_list_json_returns_parse_failed(self) -> None:
        result = _parse_claims('{"claim": "not a list"}', [_MATCHING_CITATION])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.claims == []

    def test_markdown_fenced_json_is_unwrapped(self) -> None:
        fenced = f"```json\n{_VALID_CLAIMS_JSON}\n```"
        result = _parse_claims(fenced, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.claims) == 1

    def test_claims_capped_at_max_per_query(self) -> None:
        many_items = [
            {"claim": f"Claim {i}", "value": str(i), "source_url": f"https://example.com/{i}", "source_title": "X"}
            for i in range(20)
        ]
        citations = [ResearchCitation(url=f"https://example.com/{i}", title="X") for i in range(20)]
        result = _parse_claims(json.dumps(many_items), citations)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.claims) == 8

    def test_source_title_defaults_to_url_if_missing(self) -> None:
        raw = json.dumps([{"claim": "Fact", "value": "123", "source_url": "https://example.com"}])
        result = _parse_claims(raw, [ResearchCitation(url="https://example.com", title="X")])
        assert result.claims[0].source_title == "https://example.com"

    # --- Citation enforcement (Step 4): self-reported source_url alone is never trusted ---

    def test_claim_source_url_not_among_citations_is_discarded(self) -> None:
        result = _parse_claims(_VALID_CLAIMS_JSON, citations=[])
        assert result.status == ResearchStatus.CITATION_FAILED
        assert result.claims == []

    def test_claim_source_url_matching_a_different_citation_is_discarded(self) -> None:
        result = _parse_claims(_VALID_CLAIMS_JSON, citations=[ResearchCitation(url="https://other.example.com", title="X")])
        assert result.status == ResearchStatus.CITATION_FAILED
        assert result.claims == []

    def test_claim_source_url_with_trailing_slash_still_matches_citation(self) -> None:
        raw = json.dumps([{"claim": "Fact", "value": "123", "source_url": "https://example.com/page/", "source_title": "X"}])
        result = _parse_claims(raw, citations=[ResearchCitation(url="https://example.com/page", title="X")])
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.claims) == 1

    def test_claim_source_url_differing_only_by_scheme_host_case_still_matches(self) -> None:
        raw = json.dumps([{"claim": "Fact", "value": "123", "source_url": "HTTPS://Example.com/page", "source_title": "X"}])
        result = _parse_claims(raw, citations=[ResearchCitation(url="https://example.com/page", title="X")])
        assert result.status == ResearchStatus.SUCCESS

    def test_claim_source_url_differing_only_by_default_port_still_matches(self) -> None:
        raw = json.dumps([{"claim": "Fact", "value": "123", "source_url": "https://example.com:443/page", "source_title": "X"}])
        result = _parse_claims(raw, citations=[ResearchCitation(url="https://example.com/page", title="X")])
        assert result.status == ResearchStatus.SUCCESS

    def test_claim_source_url_differing_only_by_fragment_still_matches(self) -> None:
        raw = json.dumps([{"claim": "Fact", "value": "123", "source_url": "https://example.com/page#section", "source_title": "X"}])
        result = _parse_claims(raw, citations=[ResearchCitation(url="https://example.com/page", title="X")])
        assert result.status == ResearchStatus.SUCCESS

    def test_claim_source_url_differing_only_by_tracking_params_still_matches(self) -> None:
        raw = json.dumps([{
            "claim": "Fact", "value": "123",
            "source_url": "https://example.com/page?utm_source=newsletter&utm_medium=email",
            "source_title": "X",
        }])
        result = _parse_claims(raw, citations=[ResearchCitation(url="https://example.com/page", title="X")])
        assert result.status == ResearchStatus.SUCCESS

    def test_claim_source_url_with_a_genuinely_different_path_still_fails_citation(self) -> None:
        raw = json.dumps([{"claim": "Fact", "value": "123", "source_url": "https://example.com/other-page", "source_title": "X"}])
        result = _parse_claims(raw, citations=[ResearchCitation(url="https://example.com/page", title="X")])
        assert result.status == ResearchStatus.CITATION_FAILED


# ---------------------------------------------------------------------------
# _parse_keyword_opportunities — typed-keyword counterpart to _parse_claims
# ---------------------------------------------------------------------------

class TestParseKeywordOpportunities:

    def test_valid_json_array_parsed_into_opportunities(self) -> None:
        result = _parse_keyword_opportunities(_VALID_KEYWORD_JSON, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.opportunities) == 1
        opportunity = result.opportunities[0]
        assert opportunity.keyword == "sourdough bread austin"
        assert opportunity.search_intent == "commercial"
        assert opportunity.estimated_volume == "1,000-10,000/mo"
        assert opportunity.target_page == "/bread"
        assert opportunity.source_url == "https://trends.example.com/report"

    def test_retrieved_date_is_set(self) -> None:
        result = _parse_keyword_opportunities(_VALID_KEYWORD_JSON, [_MATCHING_CITATION])
        assert len(result.opportunities[0].retrieved_date) == 10  # YYYY-MM-DD

    def test_null_estimated_volume_and_target_page_become_none_not_fabricated(self) -> None:
        raw = json.dumps([{
            "keyword": "artisan bread",
            "search_intent": "informational",
            "estimated_volume": None,
            "target_page": None,
            "source_url": "https://trends.example.com/report",
            "source_title": "Example Trends Report",
        }])
        result = _parse_keyword_opportunities(raw, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.SUCCESS
        assert result.opportunities[0].estimated_volume is None
        assert result.opportunities[0].target_page is None

    def test_missing_estimated_volume_and_target_page_default_to_none(self) -> None:
        raw = json.dumps([{
            "keyword": "artisan bread",
            "search_intent": "informational",
            "source_url": "https://trends.example.com/report",
            "source_title": "Example Trends Report",
        }])
        result = _parse_keyword_opportunities(raw, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.SUCCESS
        assert result.opportunities[0].estimated_volume is None
        assert result.opportunities[0].target_page is None

    def test_opportunity_missing_source_url_is_discarded(self) -> None:
        raw = json.dumps([{"keyword": "Made up", "search_intent": "commercial", "source_title": "Nowhere"}])
        result = _parse_keyword_opportunities(raw, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.CITATION_FAILED
        assert result.opportunities == []

    def test_opportunity_missing_keyword_text_is_discarded(self) -> None:
        raw = json.dumps([{
            "search_intent": "commercial", "source_url": "https://example.com", "source_title": "X",
        }])
        result = _parse_keyword_opportunities(raw, [ResearchCitation(url="https://example.com", title="X")])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.opportunities == []

    def test_empty_array_returns_no_results_status(self) -> None:
        result = _parse_keyword_opportunities("[]", [_MATCHING_CITATION])
        assert result.status == ResearchStatus.NO_RESULTS
        assert result.opportunities == []

    def test_empty_string_returns_no_results_status(self) -> None:
        result = _parse_keyword_opportunities("", [_MATCHING_CITATION])
        assert result.status == ResearchStatus.NO_RESULTS

    def test_malformed_json_returns_parse_failed_not_raise(self) -> None:
        result = _parse_keyword_opportunities("{not valid json", [_MATCHING_CITATION])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.error is not None


# ---------------------------------------------------------------------------
# _parse_competitors — citation enforcement on the competitor's website field
# ---------------------------------------------------------------------------

class TestParseCompetitors:

    def test_valid_json_array_parsed_into_competitors(self) -> None:
        result = _parse_competitors(_VALID_COMPETITOR_JSON, [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.competitors) == 1
        competitor = result.competitors[0]
        assert competitor.competitor_name == "Joe's Bakery"
        assert competitor.website == "https://joesbakery.com"
        assert competitor.focus == "Wholesale bread"
        assert competitor.estimated_authority == "High"

    def test_retrieved_date_is_set(self) -> None:
        result = _parse_competitors(_VALID_COMPETITOR_JSON, [_COMPETITOR_CITATION])
        assert len(result.competitors[0].retrieved_date) == 10  # YYYY-MM-DD

    def test_source_url_defaults_to_website_when_omitted(self) -> None:
        raw = json.dumps([{
            "competitor_name": "Joe's Bakery", "website": "https://joesbakery.com", "focus": "Wholesale bread",
        }])
        result = _parse_competitors(raw, [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.SUCCESS
        assert result.competitors[0].source_url == "https://joesbakery.com"

    def test_null_estimated_authority_becomes_none_not_fabricated(self) -> None:
        raw = json.dumps([{
            "competitor_name": "Joe's Bakery", "website": "https://joesbakery.com", "focus": "Wholesale bread",
            "estimated_authority": None,
        }])
        result = _parse_competitors(raw, [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.SUCCESS
        assert result.competitors[0].estimated_authority is None

    def test_competitor_missing_website_is_discarded(self) -> None:
        raw = json.dumps([{"competitor_name": "Made up", "focus": "Wholesale bread"}])
        result = _parse_competitors(raw, [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.CITATION_FAILED
        assert result.competitors == []

    def test_competitor_with_non_http_website_is_discarded(self) -> None:
        raw = json.dumps([{"competitor_name": "Made up", "website": "not-a-url", "focus": "Wholesale bread"}])
        result = _parse_competitors(raw, [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.CITATION_FAILED
        assert result.competitors == []

    def test_competitor_website_not_matching_a_citation_is_discarded(self) -> None:
        raw = json.dumps([{
            "competitor_name": "Made up", "website": "https://not-cited.com", "focus": "Wholesale bread",
        }])
        result = _parse_competitors(raw, [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.CITATION_FAILED
        assert result.competitors == []

    def test_competitor_missing_competitor_name_is_discarded(self) -> None:
        raw = json.dumps([{"website": "https://joesbakery.com", "focus": "Wholesale bread"}])
        result = _parse_competitors(raw, [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.competitors == []

    def test_competitor_missing_focus_is_discarded(self) -> None:
        raw = json.dumps([{"competitor_name": "Joe's Bakery", "website": "https://joesbakery.com"}])
        result = _parse_competitors(raw, [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.competitors == []

    def test_empty_array_returns_no_results_status(self) -> None:
        result = _parse_competitors("[]", [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.NO_RESULTS
        assert result.competitors == []

    def test_empty_string_returns_no_results_status(self) -> None:
        result = _parse_competitors("", [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.NO_RESULTS

    def test_malformed_json_returns_parse_failed_not_raise(self) -> None:
        result = _parse_competitors("{not valid json", [_COMPETITOR_CITATION])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.error is not None


# ---------------------------------------------------------------------------
# _parse_competitor_gaps
# ---------------------------------------------------------------------------

class TestParseCompetitorGaps:

    def test_valid_json_array_parsed_into_gaps(self) -> None:
        result = _parse_competitor_gaps(_VALID_COMPETITOR_GAP_JSON, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.gaps) == 1
        gap = result.gaps[0]
        assert gap.keyword == "artisan bread austin"
        assert gap.competitor_position == "Joe's Bakery ranks #2 for this term"
        assert gap.your_gap == "No dedicated landing page"

    def test_retrieved_date_is_set(self) -> None:
        result = _parse_competitor_gaps(_VALID_COMPETITOR_GAP_JSON, [_MATCHING_CITATION])
        assert len(result.gaps[0].retrieved_date) == 10  # YYYY-MM-DD

    def test_gap_missing_source_url_is_discarded(self) -> None:
        raw = json.dumps([{"keyword": "Made up", "competitor_position": "X", "your_gap": "Y"}])
        result = _parse_competitor_gaps(raw, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.CITATION_FAILED
        assert result.gaps == []

    def test_gap_missing_keyword_is_discarded(self) -> None:
        raw = json.dumps([{
            "competitor_position": "X", "your_gap": "Y",
            "source_url": "https://trends.example.com/report", "source_title": "Example Trends Report",
        }])
        result = _parse_competitor_gaps(raw, [_MATCHING_CITATION])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.gaps == []

    def test_empty_array_returns_no_results_status(self) -> None:
        result = _parse_competitor_gaps("[]", [_MATCHING_CITATION])
        assert result.status == ResearchStatus.NO_RESULTS
        assert result.gaps == []

    def test_malformed_json_returns_parse_failed_not_raise(self) -> None:
        result = _parse_competitor_gaps("{not valid json", [_MATCHING_CITATION])
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.error is not None


# ---------------------------------------------------------------------------
# _parse_location_opportunities — city_or_region always comes from the caller
# ---------------------------------------------------------------------------

class TestParseLocationOpportunities:

    def test_valid_json_array_parsed_into_opportunities(self) -> None:
        result = _parse_location_opportunities(_VALID_LOCATION_JSON, [_MATCHING_CITATION], "Austin, TX")
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.opportunities) == 1
        opportunity = result.opportunities[0]
        assert opportunity.city_or_region == "Austin, TX"
        assert opportunity.primary_keyword == "bakery near me"
        assert opportunity.priority == "High"
        assert opportunity.estimated_volume == "1,000-10,000/mo"

    def test_retrieved_date_is_set(self) -> None:
        result = _parse_location_opportunities(_VALID_LOCATION_JSON, [_MATCHING_CITATION], "Austin, TX")
        assert len(result.opportunities[0].retrieved_date) == 10  # YYYY-MM-DD

    def test_city_or_region_is_never_read_from_the_response(self) -> None:
        raw = json.dumps([{
            "primary_keyword": "bakery near me", "priority": "High", "city_or_region": "Made up city",
            "source_url": "https://trends.example.com/report", "source_title": "Example Trends Report",
        }])
        result = _parse_location_opportunities(raw, [_MATCHING_CITATION], "Austin, TX")
        assert result.opportunities[0].city_or_region == "Austin, TX"

    def test_null_estimated_volume_becomes_none_not_fabricated(self) -> None:
        raw = json.dumps([{
            "primary_keyword": "bakery near me", "priority": "High", "estimated_volume": None,
            "source_url": "https://trends.example.com/report", "source_title": "Example Trends Report",
        }])
        result = _parse_location_opportunities(raw, [_MATCHING_CITATION], "Austin, TX")
        assert result.status == ResearchStatus.SUCCESS
        assert result.opportunities[0].estimated_volume is None

    def test_opportunity_missing_source_url_is_discarded(self) -> None:
        raw = json.dumps([{"primary_keyword": "Made up", "priority": "High"}])
        result = _parse_location_opportunities(raw, [_MATCHING_CITATION], "Austin, TX")
        assert result.status == ResearchStatus.CITATION_FAILED
        assert result.opportunities == []

    def test_opportunity_missing_primary_keyword_is_discarded(self) -> None:
        raw = json.dumps([{
            "priority": "High", "source_url": "https://trends.example.com/report",
            "source_title": "Example Trends Report",
        }])
        result = _parse_location_opportunities(raw, [_MATCHING_CITATION], "Austin, TX")
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.opportunities == []

    def test_empty_array_returns_no_results_status(self) -> None:
        result = _parse_location_opportunities("[]", [_MATCHING_CITATION], "Austin, TX")
        assert result.status == ResearchStatus.NO_RESULTS
        assert result.opportunities == []

    def test_malformed_json_returns_parse_failed_not_raise(self) -> None:
        result = _parse_location_opportunities("{not valid json", [_MATCHING_CITATION], "Austin, TX")
        assert result.status == ResearchStatus.PARSE_FAILED
        assert result.error is not None


# ---------------------------------------------------------------------------
# _normalize_url — citation-matching URL normalization (Step 11)
# ---------------------------------------------------------------------------

class TestNormalizeUrl:

    def test_lowercases_scheme_and_host(self) -> None:
        assert _normalize_url("HTTPS://Example.COM/Path") == _normalize_url("https://example.com/Path")

    def test_strips_default_https_port(self) -> None:
        assert _normalize_url("https://example.com:443/page") == _normalize_url("https://example.com/page")

    def test_strips_default_http_port(self) -> None:
        assert _normalize_url("http://example.com:80/page") == _normalize_url("http://example.com/page")

    def test_keeps_non_default_port(self) -> None:
        assert _normalize_url("https://example.com:8443/page") != _normalize_url("https://example.com/page")

    def test_strips_trailing_slash(self) -> None:
        assert _normalize_url("https://example.com/page/") == _normalize_url("https://example.com/page")

    def test_strips_fragment(self) -> None:
        assert _normalize_url("https://example.com/page#section") == _normalize_url("https://example.com/page")

    def test_strips_known_tracking_params(self) -> None:
        tracked = "https://example.com/page?utm_source=x&utm_campaign=y&gclid=z"
        assert _normalize_url(tracked) == _normalize_url("https://example.com/page")

    def test_preserves_non_tracking_query_params(self) -> None:
        assert _normalize_url("https://example.com/page?id=42") != _normalize_url("https://example.com/page")

    def test_preserves_path_case(self) -> None:
        assert _normalize_url("https://example.com/Page") != _normalize_url("https://example.com/page")


# ---------------------------------------------------------------------------
# _run_research_query — one targeted correction retry (Step 11)
# ---------------------------------------------------------------------------

class TestResearchQueryCorrectionRetry:

    async def test_malformed_json_is_corrected_by_one_retry(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response("{not valid json"),
            _research_response(_VALID_CLAIMS_JSON),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_research_query("system", "user", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.claims) == 1
        assert mock_research.call_count == 2

    async def test_citation_mismatch_is_corrected_by_one_retry(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response(_VALID_CLAIMS_JSON, citations=[]),
            _research_response(_VALID_CLAIMS_JSON),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_research_query("system", "user", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert mock_research.call_count == 2

    async def test_retry_only_happens_once_when_correction_still_fails(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response("{not valid json"))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_research_query("system", "user", settings)
        assert result.status == ResearchStatus.PARSE_FAILED
        assert mock_research.call_count == 2

    async def test_genuine_no_results_is_not_retried(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response("[]"))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_research_query("system", "user", settings)
        assert result.status == ResearchStatus.NO_RESULTS
        mock_research.assert_awaited_once()

    async def test_successful_first_attempt_is_not_retried(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response(_VALID_CLAIMS_JSON))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_research_query("system", "user", settings)
        assert result.status == ResearchStatus.SUCCESS
        mock_research.assert_awaited_once()

    async def test_exception_during_correction_retry_keeps_the_original_failure(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response("{not valid json"),
            RuntimeError("network down"),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_research_query("system", "user", settings)
        assert result.status == ResearchStatus.PARSE_FAILED
        assert mock_research.call_count == 2


# ---------------------------------------------------------------------------
# _run_keyword_query — typed-keyword counterpart to _run_research_query
# ---------------------------------------------------------------------------

class TestKeywordQueryCorrectionRetry:

    async def test_malformed_json_is_corrected_by_one_retry(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response("{not valid json"),
            _research_response(_VALID_KEYWORD_JSON),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_keyword_query("system", "user", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.opportunities) == 1
        assert mock_research.call_count == 2

    async def test_retry_only_happens_once_when_correction_still_fails(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response("{not valid json"))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_keyword_query("system", "user", settings)
        assert result.status == ResearchStatus.PARSE_FAILED
        assert mock_research.call_count == 2

    async def test_genuine_no_results_is_not_retried(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response("[]"))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_keyword_query("system", "user", settings)
        assert result.status == ResearchStatus.NO_RESULTS
        mock_research.assert_awaited_once()

    async def test_successful_first_attempt_is_not_retried(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response(_VALID_KEYWORD_JSON))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_keyword_query("system", "user", settings)
        assert result.status == ResearchStatus.SUCCESS
        mock_research.assert_awaited_once()

    async def test_exception_during_correction_retry_keeps_the_original_failure(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response("{not valid json"),
            RuntimeError("network down"),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_keyword_query("system", "user", settings)
        assert result.status == ResearchStatus.PARSE_FAILED
        assert mock_research.call_count == 2


# ---------------------------------------------------------------------------
# _run_competitor_query — typed-competitor counterpart to _run_research_query
# ---------------------------------------------------------------------------

class TestCompetitorQueryCorrectionRetry:

    async def test_malformed_json_is_corrected_by_one_retry(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response("{not valid json", citations=[_COMPETITOR_CITATION]),
            _research_response(_VALID_COMPETITOR_JSON, citations=[_COMPETITOR_CITATION]),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_competitor_query("system", "user", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.competitors) == 1
        assert mock_research.call_count == 2

    async def test_retry_only_happens_once_when_correction_still_fails(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response("{not valid json"))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_competitor_query("system", "user", settings)
        assert result.status == ResearchStatus.PARSE_FAILED
        assert mock_research.call_count == 2

    async def test_genuine_no_results_is_not_retried(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response("[]"))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_competitor_query("system", "user", settings)
        assert result.status == ResearchStatus.NO_RESULTS
        mock_research.assert_awaited_once()

    async def test_successful_first_attempt_is_not_retried(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response(_VALID_COMPETITOR_JSON, citations=[_COMPETITOR_CITATION]))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_competitor_query("system", "user", settings)
        assert result.status == ResearchStatus.SUCCESS
        mock_research.assert_awaited_once()

    async def test_exception_during_correction_retry_keeps_the_original_failure(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response("{not valid json"),
            RuntimeError("network down"),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_competitor_query("system", "user", settings)
        assert result.status == ResearchStatus.PARSE_FAILED
        assert mock_research.call_count == 2


# ---------------------------------------------------------------------------
# _run_competitor_gap_query — typed-competitor-gap counterpart to _run_research_query
# ---------------------------------------------------------------------------

class TestCompetitorGapQueryCorrectionRetry:

    async def test_malformed_json_is_corrected_by_one_retry(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response("{not valid json"),
            _research_response(_VALID_COMPETITOR_GAP_JSON),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_competitor_gap_query("system", "user", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.gaps) == 1
        assert mock_research.call_count == 2

    async def test_retry_only_happens_once_when_correction_still_fails(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response("{not valid json"))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_competitor_gap_query("system", "user", settings)
        assert result.status == ResearchStatus.PARSE_FAILED
        assert mock_research.call_count == 2

    async def test_genuine_no_results_is_not_retried(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response("[]"))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_competitor_gap_query("system", "user", settings)
        assert result.status == ResearchStatus.NO_RESULTS
        mock_research.assert_awaited_once()

    async def test_successful_first_attempt_is_not_retried(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response(_VALID_COMPETITOR_GAP_JSON))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_competitor_gap_query("system", "user", settings)
        assert result.status == ResearchStatus.SUCCESS
        mock_research.assert_awaited_once()

    async def test_exception_during_correction_retry_keeps_the_original_failure(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response("{not valid json"),
            RuntimeError("network down"),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_competitor_gap_query("system", "user", settings)
        assert result.status == ResearchStatus.PARSE_FAILED
        assert mock_research.call_count == 2


# ---------------------------------------------------------------------------
# _run_location_query — typed-location counterpart to _run_research_query
# ---------------------------------------------------------------------------

class TestLocationQueryCorrectionRetry:

    async def test_malformed_json_is_corrected_by_one_retry(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response("{not valid json"),
            _research_response(_VALID_LOCATION_JSON),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_location_query("system", "user", "Austin, TX", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.opportunities) == 1
        assert result.opportunities[0].city_or_region == "Austin, TX"
        assert mock_research.call_count == 2

    async def test_retry_only_happens_once_when_correction_still_fails(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response("{not valid json"))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_location_query("system", "user", "Austin, TX", settings)
        assert result.status == ResearchStatus.PARSE_FAILED
        assert mock_research.call_count == 2

    async def test_genuine_no_results_is_not_retried(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response("[]"))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_location_query("system", "user", "Austin, TX", settings)
        assert result.status == ResearchStatus.NO_RESULTS
        mock_research.assert_awaited_once()

    async def test_successful_first_attempt_is_not_retried(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response(_VALID_LOCATION_JSON))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_location_query("system", "user", "Austin, TX", settings)
        assert result.status == ResearchStatus.SUCCESS
        mock_research.assert_awaited_once()

    async def test_exception_during_correction_retry_keeps_the_original_failure(self, settings: Settings) -> None:
        mock_research = AsyncMock(side_effect=[
            _research_response("{not valid json"),
            RuntimeError("network down"),
        ])
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await _run_location_query("system", "user", "Austin, TX", settings)
        assert result.status == ResearchStatus.PARSE_FAILED
        assert mock_research.call_count == 2


# ---------------------------------------------------------------------------
# Public research functions — mocked Perplexity client
# ---------------------------------------------------------------------------

class TestResearchFunctions:

    async def test_research_primary_keywords_returns_opportunities(self, settings: Settings) -> None:
        with patch("src.services.research_service.research_with_web_search",
                   AsyncMock(return_value=_research_response(_VALID_KEYWORD_JSON))):
            result = await research_primary_keywords("https://example.com", "A local bakery", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.opportunities) == 1

    async def test_research_long_tail_keywords_returns_opportunities(self, settings: Settings) -> None:
        with patch("src.services.research_service.research_with_web_search",
                   AsyncMock(return_value=_research_response(_VALID_KEYWORD_JSON))):
            result = await research_long_tail_keywords("https://example.com", "A local bakery", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.opportunities) == 1

    async def test_research_competitors_returns_competitors(self, settings: Settings) -> None:
        with patch("src.services.research_service.research_with_web_search",
                   AsyncMock(return_value=_research_response(_VALID_COMPETITOR_JSON, citations=[_COMPETITOR_CITATION]))):
            result = await research_competitors("https://example.com", "A local bakery", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.competitors) == 1

    async def test_research_authority_opportunities_returns_claims(self, settings: Settings) -> None:
        with patch("src.services.research_service.research_with_web_search",
                   AsyncMock(return_value=_research_response(_VALID_CLAIMS_JSON))):
            result = await research_authority_opportunities("https://example.com", "A local bakery", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.claims) == 1

    async def test_research_local_demand_returns_opportunities(self, settings: Settings) -> None:
        with patch("src.services.research_service.research_with_web_search",
                   AsyncMock(return_value=_research_response(_VALID_LOCATION_JSON))):
            result = await research_local_demand("https://example.com", "A local bakery", "Austin, TX", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.opportunities) == 1
        assert result.opportunities[0].city_or_region == "Austin, TX"

    async def test_research_competitor_analysis_returns_gaps(self, settings: Settings) -> None:
        with patch("src.services.research_service.research_with_web_search",
                   AsyncMock(return_value=_research_response(_VALID_COMPETITOR_GAP_JSON))):
            result = await research_competitor_analysis("https://example.com", ["Competitor A", "Competitor B"], settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.gaps) == 1

    async def test_research_competitor_analysis_skips_call_with_no_competitors(self, settings: Settings) -> None:
        mock_research = AsyncMock(return_value=_research_response(_VALID_COMPETITOR_GAP_JSON))
        with patch("src.services.research_service.research_with_web_search", mock_research):
            result = await research_competitor_analysis("https://example.com", [], settings)
        assert result.status == ResearchStatus.NO_RESULTS
        assert result.gaps == []
        mock_research.assert_not_called()

    async def test_missing_api_key_returns_provider_failed_not_raise(self) -> None:
        # _env_file=None guarantees no key is loaded regardless of the local .env; the real
        # (unmocked) adapter raises ValueError before any network call, which _run_research_query() catches.
        s = Settings(_env_file=None)
        result = await research_authority_opportunities("https://example.com", "A local bakery", s)
        assert result.status == ResearchStatus.PROVIDER_FAILED
        assert result.claims == []
        assert result.error is not None

    async def test_client_exception_returns_provider_failed_not_raise(self, settings: Settings) -> None:
        with patch("src.services.research_service.research_with_web_search",
                   AsyncMock(side_effect=RuntimeError("network down"))):
            result = await research_authority_opportunities("https://example.com", "A local bakery", settings)
        assert result.status == ResearchStatus.PROVIDER_FAILED
        assert result.claims == []

    async def test_empty_llm_response_returns_provider_failed(self, settings: Settings) -> None:
        with patch("src.services.research_service.research_with_web_search",
                   AsyncMock(side_effect=LLMProviderError("empty response"))):
            result = await research_authority_opportunities("https://example.com", "A local bakery", settings)
        assert result.status == ResearchStatus.PROVIDER_FAILED
        assert result.claims == []



# ---------------------------------------------------------------------------
# research_site() orchestrator — assembles the ResearchBundle
# ---------------------------------------------------------------------------

def _claim(label: str, value: str) -> ResearchClaim:
    return ResearchClaim(
        claim=label, value=value, source_url="https://example.com/source",
        source_title="Source", retrieved_date="2026-08-04",
    )


def _success(*claims: ResearchClaim) -> ResearchResult:
    return ResearchResult(status=ResearchStatus.SUCCESS, claims=list(claims))


def _no_results() -> ResearchResult:
    return ResearchResult(status=ResearchStatus.NO_RESULTS)


def _keyword_opportunity(keyword: str) -> KeywordOpportunity:
    return KeywordOpportunity(
        keyword=keyword, search_intent="commercial", source_url="https://example.com/source",
        source_title="Source", retrieved_date="2026-08-04",
    )


def _keyword_success(*opportunities: KeywordOpportunity) -> KeywordResearchResult:
    return KeywordResearchResult(status=ResearchStatus.SUCCESS, opportunities=list(opportunities))


def _keyword_no_results() -> KeywordResearchResult:
    return KeywordResearchResult(status=ResearchStatus.NO_RESULTS)


def _competitor(competitor_name: str, website: str) -> CompetitorOverview:
    return CompetitorOverview(
        competitor_name=competitor_name, website=website, focus="Wholesale bread",
        source_url=website, source_title="Source", retrieved_date="2026-08-04",
    )


def _competitor_success(*competitors: CompetitorOverview) -> CompetitorResearchResult:
    return CompetitorResearchResult(status=ResearchStatus.SUCCESS, competitors=list(competitors))


def _competitor_no_results() -> CompetitorResearchResult:
    return CompetitorResearchResult(status=ResearchStatus.NO_RESULTS)


def _gap(keyword: str) -> CompetitorGap:
    return CompetitorGap(
        keyword=keyword, competitor_position="Ranks #2", your_gap="No landing page",
        source_url="https://example.com/source", source_title="Source", retrieved_date="2026-08-04",
    )


def _gap_success(*gaps: CompetitorGap) -> CompetitorGapResult:
    return CompetitorGapResult(status=ResearchStatus.SUCCESS, gaps=list(gaps))


def _gap_no_results() -> CompetitorGapResult:
    return CompetitorGapResult(status=ResearchStatus.NO_RESULTS)


def _location_opportunity(city_or_region: str) -> LocationOpportunity:
    return LocationOpportunity(
        city_or_region=city_or_region, primary_keyword="bakery near me", priority="High",
        source_url="https://example.com/source", source_title="Source", retrieved_date="2026-08-04",
    )


def _location_success(*opportunities: LocationOpportunity) -> LocationResearchResult:
    return LocationResearchResult(status=ResearchStatus.SUCCESS, opportunities=list(opportunities))


def _location_no_results() -> LocationResearchResult:
    return LocationResearchResult(status=ResearchStatus.NO_RESULTS)


class TestResearchSiteOrchestrator:

    async def test_assembles_bundle_from_all_categories(self, settings: Settings) -> None:
        with (
            patch("src.services.research_service.research_primary_keywords",
                  AsyncMock(return_value=_keyword_success(_keyword_opportunity("sourdough bread austin")))),
            patch("src.services.research_service.research_long_tail_keywords",
                  AsyncMock(return_value=_keyword_success(_keyword_opportunity("best sourdough bread near austin tx")))),
            patch("src.services.research_service.research_competitors",
                  AsyncMock(return_value=_competitor_success(_competitor("Joe's Bakery", "https://joesbakery.com")))),
            patch("src.services.research_service.research_authority_opportunities",
                  AsyncMock(return_value=_success(_claim("Authority", "Local food blog")))),
            patch("src.services.research_service.research_brand_presence",
                  AsyncMock(return_value=_success(_claim("Brand Presence", "Listed on Yelp")))),
            patch("src.services.research_service.research_competitor_analysis",
                  AsyncMock(return_value=_gap_success(_gap("no online ordering")))) as mock_analysis,
            patch("src.services.research_service.research_local_demand",
                  AsyncMock(return_value=_location_success(_location_opportunity("Austin, TX")))) as mock_local,
            patch("src.services.research_service.research_audience_expansion",
                  AsyncMock(return_value=_no_results())) as mock_audience,
        ):
            bundle = await research_site(
                "https://example.com", "A local bakery", settings,
                is_local_business=True, city_or_region="Austin, TX",
            )

        assert len(bundle.primary_keywords) == 1
        assert len(bundle.long_tail_keywords) == 1
        assert len(bundle.competitors) == 1
        assert len(bundle.authority_opportunities) == 1
        assert len(bundle.brand_presence) == 1
        assert len(bundle.competitor_analysis) == 1
        assert len(bundle.local_demand) == 1
        assert bundle.audience_expansion == []
        mock_analysis.assert_awaited_once_with("https://example.com", ["Joe's Bakery"], settings)
        mock_local.assert_awaited_once_with("https://example.com", "A local bakery", "Austin, TX", settings)
        mock_audience.assert_not_awaited()

        # research_statuses records every category actually run (audience_expansion was skipped,
        # since this audit ran local_demand instead).
        assert bundle.research_statuses["primary_keywords"] == ResearchStatus.SUCCESS
        assert bundle.research_statuses["long_tail_keywords"] == ResearchStatus.SUCCESS
        assert bundle.research_statuses["local_demand"] == ResearchStatus.SUCCESS
        assert "audience_expansion" not in bundle.research_statuses

    async def test_audience_expansion_runs_when_not_local_business(self, settings: Settings) -> None:
        with (
            patch("src.services.research_service.research_primary_keywords", AsyncMock(return_value=_keyword_no_results())),
            patch("src.services.research_service.research_long_tail_keywords", AsyncMock(return_value=_keyword_no_results())),
            patch("src.services.research_service.research_competitors", AsyncMock(return_value=_competitor_no_results())),
            patch("src.services.research_service.research_authority_opportunities", AsyncMock(return_value=_no_results())),
            patch("src.services.research_service.research_brand_presence", AsyncMock(return_value=_no_results())),
            patch("src.services.research_service.research_competitor_analysis", AsyncMock(return_value=_gap_no_results())),
            patch("src.services.research_service.research_local_demand",
                  AsyncMock(return_value=_location_no_results())) as mock_local,
            patch("src.services.research_service.research_audience_expansion",
                  AsyncMock(return_value=_success(_claim("Segment", "Wholesale bakeries")))) as mock_audience,
        ):
            bundle = await research_site(
                "https://example.com", "A SaaS product", settings, is_local_business=False,
            )

        assert bundle.local_demand == []
        assert len(bundle.audience_expansion) == 1
        mock_local.assert_not_awaited()
        mock_audience.assert_awaited_once_with("https://example.com", "A SaaS product", settings)
        assert "local_demand" not in bundle.research_statuses
        assert bundle.research_statuses["audience_expansion"] == ResearchStatus.SUCCESS

    async def test_local_business_without_region_reports_insufficient_location_evidence_with_no_network_calls(
        self, settings: Settings,
    ) -> None:
        """
        A local/service-area business with no detectable region must never fall back to
        audience_expansion (that would silently reuse non-local messaging) and must never
        fake a placeholder region - it gets a deterministic INSUFFICIENT_LOCATION_EVIDENCE
        status with zero network calls for either category.
        """
        with (
            patch("src.services.research_service.research_primary_keywords", AsyncMock(return_value=_keyword_no_results())),
            patch("src.services.research_service.research_long_tail_keywords", AsyncMock(return_value=_keyword_no_results())),
            patch("src.services.research_service.research_competitors", AsyncMock(return_value=_competitor_no_results())),
            patch("src.services.research_service.research_authority_opportunities", AsyncMock(return_value=_no_results())),
            patch("src.services.research_service.research_brand_presence", AsyncMock(return_value=_no_results())),
            patch("src.services.research_service.research_competitor_analysis", AsyncMock(return_value=_gap_no_results())),
            patch("src.services.research_service.research_local_demand",
                  AsyncMock(return_value=_location_no_results())) as mock_local,
            patch("src.services.research_service.research_audience_expansion",
                  AsyncMock(return_value=_no_results())) as mock_audience,
        ):
            bundle = await research_site(
                "https://example.com", "A local bakery", settings, is_local_business=True, city_or_region=None,
            )

        assert bundle.local_demand == []
        assert bundle.audience_expansion == []
        mock_local.assert_not_awaited()
        mock_audience.assert_not_awaited()
        assert bundle.research_statuses["local_demand"] == ResearchStatus.INSUFFICIENT_LOCATION_EVIDENCE
        assert "audience_expansion" not in bundle.research_statuses

    async def test_competitor_analysis_receives_no_names_when_no_competitors_found(self, settings: Settings) -> None:
        with (
            patch("src.services.research_service.research_primary_keywords", AsyncMock(return_value=_keyword_no_results())),
            patch("src.services.research_service.research_long_tail_keywords", AsyncMock(return_value=_keyword_no_results())),
            patch("src.services.research_service.research_competitors", AsyncMock(return_value=_competitor_no_results())),
            patch("src.services.research_service.research_authority_opportunities", AsyncMock(return_value=_no_results())),
            patch("src.services.research_service.research_brand_presence", AsyncMock(return_value=_no_results())),
            patch("src.services.research_service.research_competitor_analysis",
                  AsyncMock(return_value=_gap_no_results())) as mock_analysis,
            patch("src.services.research_service.research_audience_expansion", AsyncMock(return_value=_no_results())),
        ):
            await research_site("https://example.com", "A local bakery", settings)

        mock_analysis.assert_awaited_once_with("https://example.com", [], settings)

    async def test_a_failed_category_still_produces_a_bundle_with_the_failure_status_recorded(
        self, settings: Settings,
    ) -> None:
        """A provider_failed category must not abort research_site() — the audit must still complete."""
        with (
            patch("src.services.research_service.research_primary_keywords",
                  AsyncMock(return_value=KeywordResearchResult(status=ResearchStatus.PROVIDER_FAILED, error="down"))),
            patch("src.services.research_service.research_long_tail_keywords", AsyncMock(return_value=_keyword_no_results())),
            patch("src.services.research_service.research_competitors", AsyncMock(return_value=_competitor_no_results())),
            patch("src.services.research_service.research_authority_opportunities", AsyncMock(return_value=_no_results())),
            patch("src.services.research_service.research_brand_presence", AsyncMock(return_value=_no_results())),
            patch("src.services.research_service.research_competitor_analysis", AsyncMock(return_value=_gap_no_results())),
            patch("src.services.research_service.research_audience_expansion", AsyncMock(return_value=_no_results())),
        ):
            bundle = await research_site("https://example.com", "A local bakery", settings)

        assert bundle.primary_keywords == []
        assert bundle.research_statuses["primary_keywords"] == ResearchStatus.PROVIDER_FAILED



# ---------------------------------------------------------------------------
# research_audience_expansion()
# ---------------------------------------------------------------------------

class TestResearchAudienceExpansion:

    async def test_returns_parsed_claims(self, settings: Settings) -> None:
        with patch("src.services.research_service.research_with_web_search",
                   AsyncMock(return_value=_research_response(_VALID_CLAIMS_JSON))):
            result = await research_audience_expansion("https://example.com", "A SaaS product", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.claims) == 1

    async def test_missing_api_key_returns_provider_failed(self) -> None:
        empty_settings = Settings(_env_file=None)
        result = await research_audience_expansion("https://example.com", "A SaaS product", empty_settings)
        assert result.status == ResearchStatus.PROVIDER_FAILED
        assert result.claims == []


# ---------------------------------------------------------------------------
# research_brand_presence()
# ---------------------------------------------------------------------------

class TestResearchBrandPresence:

    async def test_returns_parsed_claims(self, settings: Settings) -> None:
        with patch("src.services.research_service.research_with_web_search",
                   AsyncMock(return_value=_research_response(_VALID_CLAIMS_JSON))):
            result = await research_brand_presence("https://example.com", "A local bakery", settings)
        assert result.status == ResearchStatus.SUCCESS
        assert len(result.claims) == 1

    async def test_missing_api_key_returns_provider_failed(self) -> None:
        empty_settings = Settings(_env_file=None)
        result = await research_brand_presence("https://example.com", "A local bakery", empty_settings)
        assert result.status == ResearchStatus.PROVIDER_FAILED
        assert result.claims == []


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
