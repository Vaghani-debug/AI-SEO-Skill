"""
src/services/fetch_service.py

Website fetching service.

Responsibility: download the raw HTTP content from an audited website and
return it as a structured object that the extractor service can analyse.

For the MVP, four resources are fetched per audit:
  1. Homepage    — HTML page that all on-page SEO checks are run against.
  2. robots.txt  — Disallow rules and sitemap pointers.
  3. sitemap.xml — Standard sitemap location (may return 404 — that is recorded).
  4. Extra sitemaps discovered via ``Sitemap:`` directives in robots.txt.

This module uses httpx for async HTTP requests so it does not block the
FastAPI event loop while waiting for remote servers.

Public interface:
    fetch_site(normalized_url, settings) -> SiteFetchResult
"""

import asyncio  # asyncio.gather runs multiple fetches concurrently to reduce total audit time
import logging  # Standard logging; every fetch attempt is logged at INFO or WARNING level
import re  # re.compile used to find Sitemap: lines in robots.txt
from dataclasses import dataclass, field  # dataclass builds lightweight immutable result containers
from urllib.parse import urljoin  # urljoin safely combines a base URL with a relative path

import httpx  # httpx is the async HTTP client; replaces requests for async contexts

from src.config import Settings  # Settings provides timeout and max_redirects configuration

# Module-level logger — records every fetch attempt, redirect, timeout, and error
logger = logging.getLogger(__name__)  # __name__ resolves to "src.services.fetch_service"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Descriptive User-Agent so web server administrators can identify this bot
_USER_AGENT: str = (
    "AI-SEO-Agent/0.1.0 "
    "(SEO audit crawler; educational use; contact: contact@truelinesolution.com)"
)

# Compiled regex to extract Sitemap: URLs from robots.txt content
# Matches lines like: Sitemap: https://www.example.com/sitemap.xml
_SITEMAP_LINE: re.Pattern[str] = re.compile(
    r"^Sitemap:\s*(\S+)",  # "Sitemap:" at line start, then optional spaces, then the URL
    re.MULTILINE | re.IGNORECASE,  # Multiline: ^ matches each line; IGNORECASE: accept "sitemap:"
)


# ---------------------------------------------------------------------------
# Result data models
# ---------------------------------------------------------------------------

@dataclass
class FetchedResource:
    """
    Result of fetching a single URL.

    Every URL attempted during an audit produces one FetchedResource, whether
    the fetch succeeded, returned an HTTP error, or failed entirely (timeout,
    DNS failure, etc.).  Downstream services must check ``is_success`` before
    trusting the ``content`` field.
    """

    url: str
    # The URL that was requested (may differ from the final URL after redirects)

    label: str
    # Human-readable identifier for the resource: "homepage", "robots.txt", "sitemap.xml"

    final_url: str = ""
    # The actual URL after all redirects — useful when a redirect chain was followed

    status_code: int = 0
    # HTTP status code (200, 301, 404, 500, etc.); 0 means the request never completed

    content: str = ""
    # Decoded text content of the response body (HTML, plain text, XML)

    is_success: bool = False
    # True only when status_code is in the 2xx range AND content was decoded successfully

    is_fetched: bool = False
    # True when a network attempt was made, even if it failed with an error

    error_message: str = ""
    # Plain-English description of what went wrong; empty string on success

    used_playwright_fallback: bool = False
    # True when the httpx result looked like a JS shell and Playwright re-rendered it

    used_wayback_fallback: bool = False
    # True when the live fetch failed and this content came from an archive.org snapshot instead

    archived_snapshot_timestamp: str = ""
    # Wayback capture time (e.g. "20230101000000"), populated only when used_wayback_fallback is True

    response_headers: dict[str, str] = field(default_factory=dict)
    # Raw HTTP response headers (lower-cased names), e.g. security headers like
    # Strict-Transport-Security or Content-Security-Policy; empty on failed/archived fetches

    redirect_chain: list[str] = field(default_factory=list)
    # Intermediate URLs visited before final_url, in order; empty if no redirect occurred


@dataclass
class SiteFetchResult:
    """
    All fetched resources for one SEO audit.

    Produced by fetch_site() and consumed by extractor_service.
    Every field is always populated — failed fetches contain FetchedResource
    objects with is_success=False so downstream code never receives None.
    """

    base_url: str
    # The normalised URL that was audited (the input passed to fetch_site)

    homepage: FetchedResource
    # The homepage HTML document

    robots_txt: FetchedResource
    # The /robots.txt file (may be a 404 — that finding is passed to the LLM)

    sitemap_xml: FetchedResource
    # The /sitemap.xml file (may be a 404 — also passed to the LLM as a finding)

    extra_sitemaps: list[FetchedResource] = field(default_factory=list)
    # Additional sitemaps discovered via Sitemap: directives in robots.txt

    @property
    def all_sitemaps(self) -> list[FetchedResource]:
        """Return all sitemap resources (standard + extras) in a flat list."""
        result: list[FetchedResource] = []
        if self.sitemap_xml.is_fetched:
            result.append(self.sitemap_xml)  # Always include the standard sitemap result
        result.extend(self.extra_sitemaps)  # Append any sitemaps found in robots.txt
        return result

    @property
    def all_resources(self) -> list[FetchedResource]:
        """Return every fetched resource in a flat list for iteration."""
        return [self.homepage, self.robots_txt] + self.all_sitemaps


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

async def fetch_site(normalized_url: str, settings: Settings) -> SiteFetchResult:
    """
    Fetch all resources needed for an SEO audit.

    Concurrently downloads the homepage, robots.txt, and sitemap.xml.
    If robots.txt is found, any additional Sitemap: URLs it references
    are also fetched sequentially.

    All fetch failures are recorded as evidence rather than raising exceptions.
    The LLM report generator receives complete context about what was and
    was not accessible.

    Args:
        normalized_url: A validated, scheme-prefixed URL from url_service.
        settings: Application settings providing timeout and redirect limits.

    Returns:
        SiteFetchResult containing all fetched resources and their status.
    """
    logger.info("Starting site fetch for: %s", normalized_url)  # Log the start of the audit fetch

    # Build the well-known URL paths by joining relative paths to the base URL
    robots_url: str = urljoin(normalized_url, "/robots.txt")   # Always /robots.txt at domain root
    sitemap_url: str = urljoin(normalized_url, "/sitemap.xml")  # Standard sitemap location

    # Shared HTTP headers sent with every request
    headers: dict[str, str] = {
        "User-Agent": _USER_AGENT,  # Identify the bot to server admins
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",  # Accept HTML and XML
        "Accept-Language": "en-US,en;q=0.5",  # Request English content where localisation is applied
    }

    # Create a single shared httpx.AsyncClient for the whole audit
    # One client = one connection pool → re-uses TCP connections, reducing latency
    async with httpx.AsyncClient(
        follow_redirects=True,  # Automatically follow 301/302 redirects (common on HTTP→HTTPS)
        headers=headers,  # Apply shared headers to every request
        max_redirects=settings.fetch_max_redirects,  # Prevent infinite redirect loops
    ) as client:

        # Fetch homepage, robots.txt, and sitemap.xml concurrently
        # asyncio.gather sends all three requests at the same time instead of one-by-one
        homepage, robots_txt, sitemap_xml = await asyncio.gather(
            _fetch_with_wayback_fallback(
                client, normalized_url, "homepage", settings.fetch_timeout_seconds,
                retry_attempts=settings.fetch_retry_attempts,
                backoff_base_seconds=settings.fetch_retry_backoff_base_seconds,
                wayback_enabled=settings.wayback_fallback_enabled,
            ),
            _fetch_with_wayback_fallback(
                client, robots_url, "robots.txt", settings.fetch_timeout_seconds,
                retry_attempts=settings.fetch_retry_attempts,
                backoff_base_seconds=settings.fetch_retry_backoff_base_seconds,
                wayback_enabled=settings.wayback_fallback_enabled,
            ),
            _fetch_with_wayback_fallback(
                client, sitemap_url, "sitemap.xml", settings.fetch_timeout_seconds,
                retry_attempts=settings.fetch_retry_attempts,
                backoff_base_seconds=settings.fetch_retry_backoff_base_seconds,
                wayback_enabled=settings.wayback_fallback_enabled,
            ),
        )

        # Discover extra sitemaps referenced in robots.txt
        extra_sitemaps: list[FetchedResource] = []

        if robots_txt.is_success and robots_txt.content:
            # Only parse robots.txt if it was retrieved successfully
            extra_sitemap_urls: list[str] = _extract_sitemaps_from_robots(robots_txt.content)
            logger.debug("Found %d sitemap URL(s) in robots.txt", len(extra_sitemap_urls))

            for smap_url in extra_sitemap_urls:
                # Normalise both URLs before comparing to avoid /sitemap.xml vs /sitemap.xml/ mismatches
                if smap_url.rstrip("/") == sitemap_url.rstrip("/"):
                    continue  # Skip /sitemap.xml if it is already being fetched above

                logger.debug("Fetching extra sitemap from robots.txt: %s", smap_url)
                extra = await _fetch_with_wayback_fallback(
                    client,
                    smap_url,
                    f"sitemap:{smap_url}",  # Label includes the URL to identify it in the report
                    settings.fetch_timeout_seconds,
                    retry_attempts=settings.fetch_retry_attempts,
                    backoff_base_seconds=settings.fetch_retry_backoff_base_seconds,
                    wayback_enabled=settings.wayback_fallback_enabled,
                )
                extra_sitemaps.append(extra)  # Add to the list regardless of success

    # Log a summary of what was fetched
    success_count: int = sum(
        1 for r in [homepage, robots_txt, sitemap_xml, *extra_sitemaps] if r.is_success
    )
    total_count: int = 3 + len(extra_sitemaps)  # homepage + robots.txt + sitemap.xml + extras
    logger.info(
        "Site fetch complete: %d/%d resources retrieved for %s",
        success_count, total_count, normalized_url,
    )

    return SiteFetchResult(
        base_url=normalized_url,  # Store the input URL for downstream reference
        homepage=homepage,
        robots_txt=robots_txt,
        sitemap_xml=sitemap_xml,
        extra_sitemaps=extra_sitemaps,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

# Status codes worth retrying — the server itself signalled a transient problem
_TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _is_transient_failure(resource: FetchedResource) -> bool:
    """True when a retry might succeed: 429/5xx responses, timeouts, or connection errors."""
    if resource.status_code in _TRANSIENT_STATUS_CODES:
        return True
    # status_code stays 0 when no HTTP response was received at all (timeout/DNS/connection
    # error) — except redirect-limit failures, which will fail identically on every retry
    return resource.status_code == 0 and "redirect" not in resource.error_message.lower()


async def _fetch_resource(
    client: httpx.AsyncClient,
    url: str,
    label: str,
    timeout: int,
    retry_attempts: int = 1,
    backoff_base_seconds: float = 0.5,
) -> FetchedResource:
    """
    Fetch a single URL, retrying transient failures, and return a FetchedResource.

    Never raises an exception — all failures are captured and returned
    as a FetchedResource with is_success=False and an error_message.
    This ensures fetch failures are treated as audit evidence rather than
    application errors.

    A real 403/404 is returned immediately on the first attempt since retrying
    would not change the outcome; only timeouts, connection errors, and 429/5xx
    responses are retried, with exponential backoff between attempts.

    Args:
        client: An open httpx.AsyncClient to use for the request.
        url: The URL to fetch.
        label: Human-readable label for logging and report context.
        timeout: Maximum seconds to wait for the response.
        retry_attempts: Maximum attempts before giving up (default 1 = no retry).
        backoff_base_seconds: Base delay for exponential backoff between attempts.

    Returns:
        FetchedResource with populated fields; is_success=False on any failure.
    """
    result: FetchedResource = await _attempt_fetch(client, url, label, timeout)

    attempt = 1
    while attempt < retry_attempts and _is_transient_failure(result):
        delay = backoff_base_seconds * (2 ** (attempt - 1))
        logger.warning(
            "Transient failure fetching %s (attempt %d/%d), retrying in %.1fs: %s",
            url, attempt, retry_attempts, delay, result.error_message or result.status_code,
        )
        await asyncio.sleep(delay)
        result = await _attempt_fetch(client, url, label, timeout)
        attempt += 1

    return result


async def _attempt_fetch(
    client: httpx.AsyncClient,
    url: str,
    label: str,
    timeout: int,
) -> FetchedResource:
    """Make one HTTP attempt at url, capturing any failure as a FetchedResource."""
    logger.debug("Fetching %s: %s", label, url)  # Log every individual fetch attempt

    try:
        response: httpx.Response = await client.get(
            url,
            timeout=httpx.Timeout(timeout),  # Apply the configured timeout to this request
        )

        # Attempt to decode the response body as text
        try:
            content: str = response.text  # httpx auto-detects encoding from Content-Type header
        except Exception as decode_error:
            # Some servers return binary content for text paths — record but don't crash
            content = ""
            logger.warning("Could not decode body for %s: %s", url, decode_error)

        logger.info(
            "Fetched %s: HTTP %d (%s)",
            label, response.status_code, url,
        )

        return FetchedResource(
            url=url,
            label=label,
            final_url=str(response.url),  # str(response.url) gives the URL after redirects
            status_code=response.status_code,
            content=content,
            is_success=response.is_success,  # httpx.is_success is True for 2xx status codes
            is_fetched=True,
            response_headers={k.lower(): v for k, v in response.headers.items()},
        )

    except httpx.TimeoutException:
        # Request exceeded the configured timeout
        logger.warning("Timeout fetching %s: %s (timeout=%ds)", label, url, timeout)
        return FetchedResource(
            url=url,
            label=label,
            is_fetched=True,
            is_success=False,
            error_message=(
                f"Request timed out after {timeout} seconds. "
                "The server did not respond in time."
            ),
        )

    except httpx.TooManyRedirects:
        # Exceeded the configured maximum redirect count
        logger.warning("Too many redirects fetching %s: %s", label, url)
        return FetchedResource(
            url=url,
            label=label,
            is_fetched=True,
            is_success=False,
            error_message="Too many redirects. The page could not be reached.",
        )

    except httpx.RequestError as exc:
        # Network-level error: DNS failure, connection refused, SSL error, etc.
        error_text: str = str(exc)
        logger.warning("Request error fetching %s (%s): %s", label, url, error_text)
        return FetchedResource(
            url=url,
            label=label,
            is_fetched=True,
            is_success=False,
            error_message=f"Could not connect to the server: {error_text}",
        )


# Free, no-auth availability API — tells us the most recent archived snapshot of a URL, if any
_WAYBACK_AVAILABILITY_URL: str = "https://archive.org/wayback/available"


async def _fetch_with_wayback_fallback(
    client: httpx.AsyncClient,
    url: str,
    label: str,
    timeout: int,
    retry_attempts: int = 1,
    backoff_base_seconds: float = 0.5,
    wayback_enabled: bool = True,
) -> FetchedResource:
    """
    Fetch url (with retries), falling back to an archive.org snapshot on failure.

    A live failure (blocked, offline, transient error exhausted its retries)
    would otherwise leave this evidence permanently empty. Falling back to a
    real, citable archived copy keeps the evidence honest — nothing is
    invented, and the report can disclose that the data came from an
    archived snapshot rather than a live fetch.

    Returns:
        The live FetchedResource on success; the archived snapshot's
        FetchedResource if the live fetch failed and a snapshot exists;
        otherwise the original failed FetchedResource unchanged.
    """
    result: FetchedResource = await _fetch_resource(
        client, url, label, timeout, retry_attempts, backoff_base_seconds,
    )

    if result.is_success or not wayback_enabled:
        return result

    logger.info("Live fetch failed for %s (%s); checking archive.org for a snapshot", label, url)
    archived: FetchedResource | None = await _fetch_wayback_snapshot(client, url, label, timeout)

    if archived is None:
        return result  # No archived snapshot available — keep the original failure as evidence

    logger.info(
        "Using archive.org snapshot from %s for %s (%s)",
        archived.archived_snapshot_timestamp, label, url,
    )
    return archived


async def _fetch_wayback_snapshot(
    client: httpx.AsyncClient,
    url: str,
    label: str,
    timeout: int,
) -> FetchedResource | None:
    """
    Look up and fetch the most recent archive.org snapshot of url, if one exists.

    Returns None (never raises) when no snapshot exists or the lookup/fetch
    itself fails — the caller then keeps the original live-fetch failure.
    """
    try:
        availability: httpx.Response = await client.get(
            _WAYBACK_AVAILABILITY_URL,
            params={"url": url},
            timeout=httpx.Timeout(timeout),
        )
        snapshot: dict = availability.json().get("archived_snapshots", {}).get("closest") or {}
    except Exception as exc:
        logger.warning("Wayback availability lookup failed for %s: %s", url, exc)
        return None

    snapshot_url: str = snapshot.get("url", "")
    if not snapshot.get("available") or not snapshot_url:
        return None

    archived: FetchedResource = await _attempt_fetch(client, snapshot_url, label, timeout)
    if not archived.is_success:
        return None

    archived.url = url  # Preserve the originally-requested URL for downstream matching
    archived.used_wayback_fallback = True
    archived.archived_snapshot_timestamp = snapshot.get("timestamp", "")
    return archived


def _extract_sitemaps_from_robots(robots_content: str) -> list[str]:
    """
    Extract sitemap URLs from the text content of a robots.txt file.

    Robots.txt may contain zero or more ``Sitemap:`` directives, each
    pointing to a sitemap XML file.  We extract them here so the fetch
    service can retrieve them and the extractor can verify their accessibility.

    Only absolute HTTP/HTTPS URLs are returned; relative paths and non-HTTP
    values are silently skipped.

    Args:
        robots_content: The raw text content of the robots.txt file.

    Returns:
        A deduplicated list of absolute sitemap URLs found in the file.
    """
    matches: list[str] = _SITEMAP_LINE.findall(robots_content)
    # findall returns all captured groups (the URL part of each Sitemap: line)

    seen: set[str] = set()  # Track already-added URLs to avoid duplicates
    urls: list[str] = []

    for raw_url in matches:
        cleaned: str = raw_url.strip()  # Remove any trailing whitespace or carriage returns

        if not cleaned.startswith(("http://", "https://")):
            # Skip non-HTTP sitemap references (e.g. relative paths, ftp://)
            logger.debug("Skipping non-HTTP sitemap reference: %r", cleaned)
            continue

        if cleaned in seen:
            continue  # Skip duplicate sitemap URLs that appear more than once

        seen.add(cleaned)  # Record this URL so it is not added again
        urls.append(cleaned)  # Add to the output list

    return urls
