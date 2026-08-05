"""
test/test_pagespeed_service.py

Unit tests for src/services/pagespeed_service.py.

All httpx.AsyncClient calls are mocked - no real network calls are made.

Run with:
    pytest test/test_pagespeed_service.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest  # pytest: test runner

from src.config import Settings
from src.services.pagespeed_service import fetch_performance_evidence


def _settings(**overrides) -> Settings:
    s = Settings()
    s.pagespeed_enabled = True
    s.pagespeed_timeout_seconds = 5
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _mock_response(status_code: int = 200, json_body: dict | None = None) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    return response


def _patched_client(response: MagicMock | None = None, side_effect: Exception | None = None):
    client = AsyncMock(spec=httpx.AsyncClient)
    if side_effect is not None:
        client.get = AsyncMock(side_effect=side_effect)
    else:
        client.get = AsyncMock(return_value=response)

    context_manager = AsyncMock()
    context_manager.__aenter__ = AsyncMock(return_value=client)
    context_manager.__aexit__ = AsyncMock(return_value=False)
    return patch("src.services.pagespeed_service.httpx.AsyncClient", return_value=context_manager)


_FIELD_PAYLOAD = {
    "loadingExperience": {
        "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2400},
            "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 8},
            "INTERACTION_TO_NEXT_PAINT": {"percentile": 180},
        }
    },
    "lighthouseResult": {
        "categories": {"performance": {"score": 0.87}},
        "audits": {},
    },
}

_LAB_ONLY_PAYLOAD = {
    "loadingExperience": {},
    "lighthouseResult": {
        "categories": {"performance": {"score": 0.62}},
        "audits": {
            "largest-contentful-paint": {"numericValue": 3100.0},
            "cumulative-layout-shift": {"numericValue": 0.15},
        },
    },
}


class TestFetchPerformanceEvidenceDisabled:

    async def test_returns_unavailable_when_disabled(self) -> None:
        result = await fetch_performance_evidence("https://example.com", _settings(pagespeed_enabled=False))
        assert result.is_available is False
        assert result.data_source == ""
        assert result.performance_score is None


class TestFetchPerformanceEvidenceFieldData:

    async def test_returns_field_data_when_available(self) -> None:
        response = _mock_response(json_body=_FIELD_PAYLOAD)
        with _patched_client(response=response):
            result = await fetch_performance_evidence("https://example.com", _settings())

        assert result.is_available is True
        assert result.data_source == "field"
        assert result.largest_contentful_paint_ms == 2400.0
        assert result.cumulative_layout_shift == 0.08
        assert result.interaction_to_next_paint_ms == 180.0
        assert result.performance_score == 87.0
        assert result.source_url == "https://example.com"


class TestFetchPerformanceEvidenceLabData:

    async def test_falls_back_to_lab_data_when_no_field_data(self) -> None:
        response = _mock_response(json_body=_LAB_ONLY_PAYLOAD)
        with _patched_client(response=response):
            result = await fetch_performance_evidence("https://example.com", _settings())

        assert result.is_available is True
        assert result.data_source == "lab"
        assert result.largest_contentful_paint_ms == 3100.0
        assert result.cumulative_layout_shift == 0.15
        assert result.interaction_to_next_paint_ms is None
        assert result.performance_score == 62.0


class TestFetchPerformanceEvidenceFailureModes:

    async def test_returns_unavailable_on_network_error(self) -> None:
        with _patched_client(side_effect=httpx.TimeoutException("timed out")):
            result = await fetch_performance_evidence("https://example.com", _settings())
        assert result.is_available is False
        assert result.data_source == ""

    async def test_returns_unavailable_on_non_200_status(self) -> None:
        response = _mock_response(status_code=429)
        with _patched_client(response=response):
            result = await fetch_performance_evidence("https://example.com", _settings())
        assert result.is_available is False

    async def test_returns_unavailable_on_invalid_json(self) -> None:
        response = _mock_response(status_code=200)
        response.json.side_effect = ValueError("not json")
        with _patched_client(response=response):
            result = await fetch_performance_evidence("https://example.com", _settings())
        assert result.is_available is False

    async def test_returns_unavailable_when_payload_has_no_usable_metrics(self) -> None:
        response = _mock_response(json_body={"loadingExperience": {}, "lighthouseResult": {}})
        with _patched_client(response=response):
            result = await fetch_performance_evidence("https://example.com", _settings())
        assert result.is_available is False
        assert result.data_source == ""

    async def test_uses_api_key_in_request_params_when_configured(self) -> None:
        response = _mock_response(json_body=_FIELD_PAYLOAD)
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=response)
        context_manager = AsyncMock()
        context_manager.__aenter__ = AsyncMock(return_value=client)
        context_manager.__aexit__ = AsyncMock(return_value=False)

        with patch("src.services.pagespeed_service.httpx.AsyncClient", return_value=context_manager):
            await fetch_performance_evidence("https://example.com", _settings(pagespeed_api_key="test-key"))

        called_params = client.get.call_args.kwargs["params"]
        assert called_params["key"] == "test-key"
