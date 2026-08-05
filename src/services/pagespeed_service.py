"""
src/services/pagespeed_service.py

External research boundary: calls Google's free PageSpeed Insights (PSI)
API to collect real Core Web Vitals and Lighthouse performance data for
a site's homepage.

PSI is queried for the homepage URL. Real-user field data (Chrome UX
Report) is preferred when Google has enough traffic for the URL to
report it, since it reflects actual visitors; a single simulated
Lighthouse "lab" run is used as a fallback so smaller/lower-traffic
sites still get a performance signal. Nothing is ever invented: if PSI
cannot be reached or returns no usable data, the returned
PerformanceEvidence has is_available=False and every metric is None
(docs/SEO_RULES.md / SCORING_ENGINE.md Principle 4: Evidence Based).

This module makes network calls to the PageSpeed Insights API and must
be mocked in all tests other than an explicit live-integration test.

Public interface:
    fetch_performance_evidence(url, settings) -> PerformanceEvidence
"""

import logging

import httpx

from src.config import Settings
from src.services.audit_models import PerformanceEvidence

logger = logging.getLogger(__name__)

_PAGESPEED_API_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

_UNAVAILABLE = PerformanceEvidence(is_available=False, data_source="")
# Shared "no data" result returned whenever PSI is disabled, unreachable, or has nothing usable


async def fetch_performance_evidence(url: str, settings: Settings) -> PerformanceEvidence:
    """
    Call PageSpeed Insights for `url` and return whatever real Core Web
    Vitals / Lighthouse data is available. Returns an unavailable
    PerformanceEvidence (no fabricated metrics) if the feature is
    disabled or the call fails for any reason.
    """
    if not settings.pagespeed_enabled:
        return _UNAVAILABLE

    params: dict[str, str] = {"url": url, "strategy": "mobile", "category": "performance"}
    if settings.pagespeed_api_key:
        params["key"] = settings.pagespeed_api_key

    try:
        async with httpx.AsyncClient(timeout=settings.pagespeed_timeout_seconds) as client:
            response = await client.get(_PAGESPEED_API_URL, params=params)
    except httpx.HTTPError as request_error:
        logger.warning("PageSpeed Insights request failed for %s: %s", url, request_error)
        return _UNAVAILABLE

    if response.status_code != 200:
        logger.warning("PageSpeed Insights returned HTTP %d for %s", response.status_code, url)
        return _UNAVAILABLE

    try:
        payload = response.json()
    except ValueError:
        logger.warning("PageSpeed Insights returned invalid JSON for %s", url)
        return _UNAVAILABLE

    return _parse_pagespeed_payload(payload, url)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_pagespeed_payload(payload: dict, url: str) -> PerformanceEvidence:
    """Prefer real-user field data; fall back to the lab-simulated Lighthouse run."""
    field_metrics = (payload.get("loadingExperience") or {}).get("metrics") or {}
    lighthouse = payload.get("lighthouseResult") or {}
    audits = lighthouse.get("audits") or {}

    field_lcp = _field_percentile(field_metrics, "LARGEST_CONTENTFUL_PAINT_MS")
    field_cls_raw = _field_percentile(field_metrics, "CUMULATIVE_LAYOUT_SHIFT_SCORE")
    field_inp = _field_percentile(field_metrics, "INTERACTION_TO_NEXT_PAINT")

    if field_lcp is not None or field_cls_raw is not None or field_inp is not None:
        return PerformanceEvidence(
            is_available=True,
            data_source="field",
            performance_score=_lighthouse_score(lighthouse),
            largest_contentful_paint_ms=field_lcp,
            cumulative_layout_shift=_field_cls_to_score(field_cls_raw),
            interaction_to_next_paint_ms=field_inp,
            source_url=url,
        )

    lab_lcp = _audit_numeric_value(audits, "largest-contentful-paint")
    lab_cls = _audit_numeric_value(audits, "cumulative-layout-shift")
    lab_score = _lighthouse_score(lighthouse)

    if lab_lcp is None and lab_cls is None and lab_score is None:
        return _UNAVAILABLE

    return PerformanceEvidence(
        is_available=True,
        data_source="lab",
        performance_score=lab_score,
        largest_contentful_paint_ms=lab_lcp,
        cumulative_layout_shift=lab_cls,
        interaction_to_next_paint_ms=None,  # INP is not produced by a single lab Lighthouse run
        source_url=url,
    )


def _field_percentile(field_metrics: dict, key: str) -> float | None:
    metric = field_metrics.get(key)
    if not isinstance(metric, dict):
        return None
    value = metric.get("percentile")
    return float(value) if isinstance(value, (int, float)) else None


def _field_cls_to_score(raw_percentile: float | None) -> float | None:
    # CrUX reports CLS as an integer percentile equal to the real score * 100 (e.g. 12 -> 0.12).
    return round(raw_percentile / 100, 3) if raw_percentile is not None else None


def _audit_numeric_value(audits: dict, audit_id: str) -> float | None:
    audit = audits.get(audit_id)
    if not isinstance(audit, dict):
        return None
    value = audit.get("numericValue")
    return float(value) if isinstance(value, (int, float)) else None


def _lighthouse_score(lighthouse: dict) -> float | None:
    categories = lighthouse.get("categories") or {}
    performance = categories.get("performance") or {}
    score = performance.get("score")
    return round(score * 100, 1) if isinstance(score, (int, float)) else None
