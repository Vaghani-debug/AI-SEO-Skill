"""
test/test_fetch_service.py

Unit tests for src/services/fetch_service.py.

All network calls are mocked so these tests run offline and deterministically.
Each test exercises one specific behaviour or failure mode.

Run with:
    pytest test/test_fetch_service.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch  # Standard library mocking tools
import httpx  # Imported so we can reference httpx exception types in the tests
import pytest  # pytest provides the test runner and fixture system

from src.services.fetch_service import (
    FetchedResource,      # Result model for a single URL
    SiteFetchResult,      # Result model for a full site fetch
    _extract_sitemaps_from_robots,  # Helper function: robots.txt sitemap extraction
    _fetch_resource,      # Internal fetch helper — tested directly to cover error paths
    fetch_site,           # Public orchestration function
)
from src.config import Settings  # Settings provides timeout and redirect configuration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings() -> Settings:
    """Return a Settings instance with short timeouts suitable for unit tests."""
    s = Settings()
    s.fetch_timeout_seconds = 5   # Short timeout — tests should not actually wait
    s.fetch_max_redirects = 3     # Low redirect limit — keeps tests predictable
    return s


def _make_mock_response(
    status_code: int = 200,
    text: str = "<html><title>Test</title></html>",
    url: str = "https://example.com",
) -> MagicMock:
    """
    Create a minimal mock httpx.Response object.

    Only the fields that fetch_service accesses are set.
    """
    response = MagicMock(spec=httpx.Response)  # spec=httpx.Response limits the mock to real attrs
    response.status_code = status_code         # The HTTP status code to return
    response.text = text                       # Decoded body text
    response.url = httpx.URL(url)             # httpx.URL so str(response.url) works correctly
    response.is_success = 200 <= status_code < 300  # Mirror httpx's real is_success property
    return response


def _make_async_client_mock(responses: dict[str, MagicMock]) -> AsyncMock:
    """
    Create a mock httpx.AsyncClient whose get() method returns a different
    response depending on the requested URL.

    Args:
        responses: Mapping of URL string → mock response object.
                   Use '*' as a wildcard key to match any unmatched URL.
    """
    # No spec= here: inside a patch() context, httpx.AsyncClient is already a MagicMock,
    # and Python 3.14's stricter unittest.mock raises InvalidSpecError if you spec a Mock.
    client = AsyncMock()  # Plain AsyncMock — all attributes are available by default

    async def mock_get(url: str, **kwargs) -> MagicMock:
        # Return the matching response, preferring exact URL matches over substring matches
        url_str = str(url)

        # 1. Try an exact URL match first (most precise)
        if url_str in responses:
            return responses[url_str]

        # 2. Try substring matches in insertion order
        for key, resp in responses.items():
            if key != "*" and key in url_str:
                return resp

        # 3. Wildcard fallback
        if "*" in responses:
            return responses["*"]

        # 4. Default 404 for any unmatched URL
        return _make_mock_response(status_code=404, text="Not Found", url=url_str)

    client.get = mock_get  # Replace the async get method with our deterministic version
    return client


# ---------------------------------------------------------------------------
# Tests for _extract_sitemaps_from_robots()
# ---------------------------------------------------------------------------

class TestExtractSitemapsFromRobots:
    """Unit tests for the robots.txt sitemap extractor helper."""

    def test_single_sitemap_line(self) -> None:
        """Extracts one sitemap URL from a simple robots.txt."""
        content = "User-agent: *\nDisallow: /admin\nSitemap: https://example.com/sitemap.xml"
        result = _extract_sitemaps_from_robots(content)
        assert result == ["https://example.com/sitemap.xml"]  # Exactly one URL returned

    def test_multiple_sitemap_lines(self) -> None:
        """Extracts multiple sitemap URLs when several Sitemap: lines are present."""
        content = (
            "User-agent: *\n"
            "Sitemap: https://example.com/sitemap/page.xml\n"
            "Sitemap: https://example.com/sitemap/post.xml\n"
        )
        result = _extract_sitemaps_from_robots(content)
        assert len(result) == 2  # Both sitemaps extracted
        assert "https://example.com/sitemap/page.xml" in result
        assert "https://example.com/sitemap/post.xml" in result

    def test_no_sitemap_lines(self) -> None:
        """Returns an empty list when no Sitemap: directive is present."""
        content = "User-agent: *\nDisallow: /\n"
        result = _extract_sitemaps_from_robots(content)
        assert result == []  # No sitemaps found

    def test_case_insensitive_sitemap_keyword(self) -> None:
        """Handles lowercase 'sitemap:' and mixed-case variants."""
        content = "sitemap: https://example.com/sitemap.xml\n"
        result = _extract_sitemaps_from_robots(content)
        assert len(result) == 1  # Case-insensitive match

    def test_skips_non_http_urls(self) -> None:
        """Skips relative paths and non-HTTP sitemap references."""
        content = (
            "Sitemap: /sitemap.xml\n"           # Relative path — skipped
            "Sitemap: ftp://example.com/s.xml\n"  # FTP — skipped
            "Sitemap: https://example.com/valid.xml\n"  # Valid — kept
        )
        result = _extract_sitemaps_from_robots(content)
        assert result == ["https://example.com/valid.xml"]  # Only the HTTPS URL returned

    def test_deduplicates_identical_urls(self) -> None:
        """Returns each URL only once when it appears multiple times."""
        content = (
            "Sitemap: https://example.com/sitemap.xml\n"
            "Sitemap: https://example.com/sitemap.xml\n"  # Duplicate
        )
        result = _extract_sitemaps_from_robots(content)
        assert result == ["https://example.com/sitemap.xml"]  # De-duplicated to one entry

    def test_truelinesolution_robots_format(self) -> None:
        """Handles the real-world format observed on truelinesolution.com."""
        content = (
            "user-agent: *\n"
            "disallow: /Tls_admin/*\n"
            "sitemap: https://www.truelinesolution.com/sitemap/page.xml\n"
            "sitemap: https://www.truelinesolution.com/sitemap/post.xml\n"
        )
        result = _extract_sitemaps_from_robots(content)
        assert len(result) == 2  # Both real sitemaps extracted


# ---------------------------------------------------------------------------
# Tests for _fetch_resource()
# ---------------------------------------------------------------------------

class TestFetchResource:
    """Unit tests for the internal fetch helper."""

    async def test_successful_200_response(self) -> None:
        """Returns is_success=True and populates content for a 200 response."""
        mock_response = _make_mock_response(200, "<html>...</html>", "https://example.com")
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_response)  # Always return our mock

        result = await _fetch_resource(client, "https://example.com", "homepage", 10)

        assert result.is_success is True       # 200 is a success
        assert result.status_code == 200        # Correct status code
        assert result.content == "<html>...</html>"  # Content populated
        assert result.is_fetched is True        # Attempt was made
        assert result.error_message == ""       # No error on success
        assert result.label == "homepage"       # Label preserved

    async def test_404_response_recorded_not_raised(self) -> None:
        """A 404 is stored as a finding, not raised as an exception."""
        mock_response = _make_mock_response(404, "Not Found", "https://example.com/robots.txt")
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_response)

        result = await _fetch_resource(client, "https://example.com/robots.txt", "robots.txt", 10)

        assert result.is_success is False   # 404 is not a success
        assert result.status_code == 404     # Correct status code stored
        assert result.is_fetched is True     # Attempt was still made
        assert result.error_message == ""    # 404 is not an error — it is data

    async def test_timeout_returns_error_resource(self) -> None:
        """A timeout is captured and returned as is_success=False with an error_message."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(
            side_effect=httpx.TimeoutException("Request timed out")  # Simulate timeout
        )

        result = await _fetch_resource(client, "https://slow.example.com", "homepage", 5)

        assert result.is_success is False           # Timeout is a failure
        assert result.is_fetched is True            # Attempt was made
        assert "timed out" in result.error_message.lower()  # Message mentions timeout
        assert result.status_code == 0              # No status code received

    async def test_dns_failure_returns_error_resource(self) -> None:
        """A DNS / connection error is captured and returned as is_success=False."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(
            side_effect=httpx.ConnectError("Name resolution failed")  # DNS failure
        )

        result = await _fetch_resource(client, "https://nonexistent.invalid", "homepage", 10)

        assert result.is_success is False
        assert result.is_fetched is True
        assert result.error_message != ""  # Error message is populated

    async def test_too_many_redirects_returns_error_resource(self) -> None:
        """Exceeding the redirect limit is captured as an error, not raised."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(
            side_effect=httpx.TooManyRedirects("Exceeded redirect limit")
        )

        result = await _fetch_resource(client, "https://redirect-loop.example.com", "homepage", 10)

        assert result.is_success is False
        assert "redirect" in result.error_message.lower()  # Message mentions redirects


# ---------------------------------------------------------------------------
# Tests for fetch_site()
# ---------------------------------------------------------------------------

class TestFetchSite:
    """Integration tests for the public fetch_site() orchestration function."""

    async def test_returns_site_fetch_result(self, settings: Settings) -> None:
        """fetch_site always returns a SiteFetchResult with all three core fields."""
        mock_html = "<html><head><title>Test</title></head><body>Content</body></html>"
        mock_robots = "User-agent: *\nDisallow:\nSitemap: https://example.com/sitemap.xml\n"
        mock_sitemap = '<?xml version="1.0"?><urlset><url><loc>https://example.com/</loc></url></urlset>'

        # Use full URLs as keys so the matcher is precise and order-independent
        responses = {
            "https://example.com": _make_mock_response(200, mock_html, "https://example.com"),
            "https://example.com/robots.txt": _make_mock_response(200, mock_robots, "https://example.com/robots.txt"),
            "https://example.com/sitemap.xml": _make_mock_response(200, mock_sitemap, "https://example.com/sitemap.xml"),
        }

        with patch("src.services.fetch_service.httpx.AsyncClient") as mock_client_class:
            # Patch AsyncClient to use our mock responses
            mock_instance = _make_async_client_mock(responses)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_site("https://example.com", settings)

        assert isinstance(result, SiteFetchResult)       # Correct return type
        assert result.base_url == "https://example.com"  # Base URL stored
        assert isinstance(result.homepage, FetchedResource)    # Homepage present
        assert isinstance(result.robots_txt, FetchedResource)  # robots.txt present
        assert isinstance(result.sitemap_xml, FetchedResource) # sitemap.xml present

    async def test_homepage_not_found_recorded(self, settings: Settings) -> None:
        """A 404 homepage is recorded in the result, not raised."""
        responses = {"*": _make_mock_response(404, "Not Found")}

        with patch("src.services.fetch_service.httpx.AsyncClient") as mock_client_class:
            mock_instance = _make_async_client_mock(responses)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_site("https://example.com", settings)

        assert result.homepage.is_success is False  # 404 homepage recorded
        assert result.homepage.status_code == 404    # Status code preserved
        assert isinstance(result, SiteFetchResult)   # Function still returned a result

    async def test_extra_sitemaps_fetched_from_robots(self, settings: Settings) -> None:
        """Sitemaps listed in robots.txt that differ from /sitemap.xml are fetched."""
        mock_robots = (
            "User-agent: *\n"
            "Sitemap: https://example.com/sitemap/page.xml\n"
            "Sitemap: https://example.com/sitemap/post.xml\n"
        )
        # Use full URLs as keys to avoid greedy substring matching
        responses = {
            "https://example.com": _make_mock_response(200, "<html>Test</html>"),
            "https://example.com/robots.txt": _make_mock_response(200, mock_robots, "https://example.com/robots.txt"),
            "https://example.com/sitemap.xml": _make_mock_response(404, "Not Found"),  # Standard sitemap missing
            "https://example.com/sitemap/page.xml": _make_mock_response(200, "<urlset/>", "https://example.com/sitemap/page.xml"),
            "https://example.com/sitemap/post.xml": _make_mock_response(200, "<urlset/>", "https://example.com/sitemap/post.xml"),
        }

        with patch("src.services.fetch_service.httpx.AsyncClient") as mock_client_class:
            mock_instance = _make_async_client_mock(responses)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_site("https://example.com", settings)

        assert len(result.extra_sitemaps) == 2  # Both extra sitemaps fetched

    async def test_all_resources_property(self, settings: Settings) -> None:
        """all_resources returns homepage, robots.txt, and all sitemaps."""
        responses = {"*": _make_mock_response(200, "<html/>", "https://example.com")}

        with patch("src.services.fetch_service.httpx.AsyncClient") as mock_client_class:
            mock_instance = _make_async_client_mock(responses)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_site("https://example.com", settings)

        assert len(result.all_resources) >= 3  # At minimum: homepage, robots.txt, sitemap.xml


# ---------------------------------------------------------------------------
# Additional tests for _fetch_resource() — HTTP status variants
# ---------------------------------------------------------------------------

class TestFetchResourceStatusCodes:
    """Tests covering various HTTP status code behaviours."""

    async def test_500_server_error_recorded_not_raised(self) -> None:
        """A 500 Internal Server Error is stored as is_success=False, not raised."""
        mock_response = _make_mock_response(500, "Internal Server Error", "https://example.com")
        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await _fetch_resource(client, "https://example.com", "homepage", 10)

        assert result.is_success is False    # 5xx is not a success
        assert result.status_code == 500      # Status code preserved
        assert result.is_fetched is True      # Attempt was made
        assert result.error_message == ""     # 500 is data, not an exception message

    async def test_503_service_unavailable_recorded(self) -> None:
        """A 503 Service Unavailable is stored correctly."""
        mock_response = _make_mock_response(503, "Service Unavailable", "https://example.com")
        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await _fetch_resource(client, "https://example.com", "homepage", 10)

        assert result.is_success is False
        assert result.status_code == 503

    async def test_301_redirect_final_url_captured(self) -> None:
        """When a redirect is followed, final_url reflects the destination."""
        # httpx follows redirects automatically; the response.url is the final URL
        mock_response = _make_mock_response(200, "<html/>", "https://www.example.com")
        # URL in the response differs from the requested URL (simulates HTTP→HTTPS redirect)
        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await _fetch_resource(client, "http://example.com", "homepage", 10)

        assert result.is_success is True
        assert result.final_url == "https://www.example.com"  # Final URL after redirect stored
        assert result.url == "http://example.com"             # Original requested URL preserved

    async def test_url_preserved_in_error_resource(self) -> None:
        """The original request URL is always stored, even on network failure."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        requested_url = "https://unreachable.example.com"

        result = await _fetch_resource(client, requested_url, "homepage", 10)

        assert result.url == requested_url      # URL preserved
        assert result.label == "homepage"       # Label preserved
        assert result.is_success is False       # Marked as failure

    async def test_label_preserved_in_error_resource(self) -> None:
        """The label is preserved on every error type."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

        result = await _fetch_resource(client, "https://example.com/robots.txt", "robots.txt", 5)

        assert result.label == "robots.txt"     # Label always stored

    async def test_status_code_zero_on_network_error(self) -> None:
        """status_code is 0 when no HTTP response is received."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("DNS failure"))

        result = await _fetch_resource(client, "https://example.com", "homepage", 10)

        assert result.status_code == 0          # No HTTP code available

    async def test_error_message_nonempty_on_network_error(self) -> None:
        """error_message is populated for every type of network error."""
        for exc in [
            httpx.TimeoutException("Timeout"),
            httpx.ConnectError("DNS failure"),
            httpx.TooManyRedirects("Too many redirects"),
        ]:
            client = AsyncMock()
            client.get = AsyncMock(side_effect=exc)
            result = await _fetch_resource(client, "https://example.com", "homepage", 5)
            assert result.error_message != "", f"Expected error_message for {type(exc).__name__}"

    async def test_error_message_empty_on_http_error(self) -> None:
        """error_message is empty when HTTP responded (even with 4xx/5xx)."""
        mock_response = _make_mock_response(404, "Not Found")
        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)

        result = await _fetch_resource(client, "https://example.com/missing", "page", 10)

        assert result.error_message == ""       # 404 is data, not an application error
        assert result.is_fetched is True
        assert result.status_code == 404


# ---------------------------------------------------------------------------
# Additional tests for FetchedResource — model defaults
# ---------------------------------------------------------------------------

class TestFetchedResourceDefaults:
    """Tests that FetchedResource initialises with sensible default values."""

    def test_default_values(self) -> None:
        """FetchedResource defaults are safe and sensible."""
        r = FetchedResource(url="https://example.com", label="homepage")

        assert r.final_url == ""        # Empty until a response is received
        assert r.status_code == 0       # 0 means no response yet
        assert r.content == ""          # No content by default
        assert r.is_success is False    # Default to failed state
        assert r.is_fetched is False    # Default to not attempted
        assert r.error_message == ""    # No error message by default


# ---------------------------------------------------------------------------
# Additional tests for _extract_sitemaps_from_robots — edge cases
# ---------------------------------------------------------------------------

class TestExtractSitemapsEdgeCases:
    """Edge-case handling in the robots.txt sitemap extractor."""

    def test_empty_string_returns_empty_list(self) -> None:
        """An empty string returns an empty list without raising."""
        result = _extract_sitemaps_from_robots("")
        assert result == []

    def test_only_comments_returns_empty_list(self) -> None:
        """A robots.txt containing only comments has no sitemaps."""
        content = "# This is a comment\n# Another comment\n"
        result = _extract_sitemaps_from_robots(content)
        assert result == []

    def test_windows_line_endings_handled(self) -> None:
        """Windows \\r\\n line endings do not break sitemap extraction."""
        content = "User-agent: *\r\nSitemap: https://example.com/sitemap.xml\r\n"
        result = _extract_sitemaps_from_robots(content)
        assert len(result) == 1
        assert result[0] == "https://example.com/sitemap.xml"  # URL is clean (no \\r)

    def test_http_sitemap_accepted(self) -> None:
        """http:// sitemaps are accepted as well as https://."""
        content = "Sitemap: http://example.com/sitemap.xml\n"
        result = _extract_sitemaps_from_robots(content)
        assert len(result) == 1
        assert result[0].startswith("http://")

    def test_whitespace_around_url_stripped(self) -> None:
        """Extra spaces around the sitemap URL are stripped."""
        content = "Sitemap:  https://example.com/sitemap.xml  \n"
        result = _extract_sitemaps_from_robots(content)
        assert result == ["https://example.com/sitemap.xml"]  # Clean URL returned


# ---------------------------------------------------------------------------
# Additional tests for fetch_site() — missing resources
# ---------------------------------------------------------------------------

class TestFetchSiteMissingResources:
    """Tests that fetch_site handles missing robots.txt and sitemap gracefully."""

    async def test_robots_txt_404_still_returns_result(self, settings: Settings) -> None:
        """A 404 robots.txt does not abort the audit; the result is still returned."""
        responses = {
            "https://example.com": _make_mock_response(200, "<html/>"),
            "https://example.com/robots.txt": _make_mock_response(404, "Not Found"),
            "https://example.com/sitemap.xml": _make_mock_response(200, "<urlset/>"),
        }

        with patch("src.services.fetch_service.httpx.AsyncClient") as mock_client_class:
            mock_instance = _make_async_client_mock(responses)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_site("https://example.com", settings)

        assert isinstance(result, SiteFetchResult)         # Result returned despite 404 robots.txt
        assert result.robots_txt.is_success is False        # 404 recorded
        assert result.robots_txt.status_code == 404         # Status code preserved
        assert result.extra_sitemaps == []                  # No extras if robots.txt missing

    async def test_sitemap_xml_404_still_returns_result(self, settings: Settings) -> None:
        """A 404 sitemap.xml does not abort the audit."""
        responses = {
            "https://example.com": _make_mock_response(200, "<html/>"),
            "https://example.com/robots.txt": _make_mock_response(200, "User-agent: *\n"),
            "https://example.com/sitemap.xml": _make_mock_response(404, "Not Found"),
        }

        with patch("src.services.fetch_service.httpx.AsyncClient") as mock_client_class:
            mock_instance = _make_async_client_mock(responses)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_site("https://example.com", settings)

        assert isinstance(result, SiteFetchResult)
        assert result.sitemap_xml.is_success is False   # 404 sitemap recorded
        assert result.sitemap_xml.status_code == 404

    async def test_standard_sitemap_not_duplicated_in_extras(self, settings: Settings) -> None:
        """When robots.txt lists /sitemap.xml, it is not fetched a second time."""
        mock_robots = "User-agent: *\nSitemap: https://example.com/sitemap.xml\n"
        responses = {
            "https://example.com": _make_mock_response(200, "<html/>"),
            "https://example.com/robots.txt": _make_mock_response(200, mock_robots),
            "https://example.com/sitemap.xml": _make_mock_response(200, "<urlset/>"),
        }

        with patch("src.services.fetch_service.httpx.AsyncClient") as mock_client_class:
            mock_instance = _make_async_client_mock(responses)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await fetch_site("https://example.com", settings)

        # /sitemap.xml is already fetched as sitemap_xml — it must not appear in extra_sitemaps too
        assert result.extra_sitemaps == []
        assert len(result.all_sitemaps) == 1   # Only one sitemap in total


# ---------------------------------------------------------------------------
# Additional tests for SiteFetchResult — property behaviour
# ---------------------------------------------------------------------------

class TestSiteFetchResultProperties:
    """Tests for the all_sitemaps and all_resources computed properties."""

    def _make_resource(self, url: str, label: str, success: bool = True) -> FetchedResource:
        """Helper to create a FetchedResource for property tests."""
        return FetchedResource(
            url=url,
            label=label,
            final_url=url,
            status_code=200 if success else 404,
            content="<xml/>",
            is_success=success,
            is_fetched=True,
        )

    def test_all_sitemaps_includes_sitemap_xml_when_fetched(self) -> None:
        """all_sitemaps includes sitemap_xml when it was fetched (even if 404)."""
        result = SiteFetchResult(
            base_url="https://example.com",
            homepage=self._make_resource("https://example.com", "homepage"),
            robots_txt=self._make_resource("https://example.com/robots.txt", "robots.txt"),
            sitemap_xml=self._make_resource("https://example.com/sitemap.xml", "sitemap.xml"),
            extra_sitemaps=[],
        )
        # sitemap_xml was fetched → it should appear in all_sitemaps
        assert len(result.all_sitemaps) == 1
        assert result.all_sitemaps[0].label == "sitemap.xml"

    def test_all_sitemaps_includes_extra_sitemaps(self) -> None:
        """all_sitemaps combines sitemap_xml and extra_sitemaps."""
        extra = self._make_resource("https://example.com/sitemap/posts.xml", "sitemap:posts")
        result = SiteFetchResult(
            base_url="https://example.com",
            homepage=self._make_resource("https://example.com", "homepage"),
            robots_txt=self._make_resource("https://example.com/robots.txt", "robots.txt"),
            sitemap_xml=self._make_resource("https://example.com/sitemap.xml", "sitemap.xml"),
            extra_sitemaps=[extra],
        )
        assert len(result.all_sitemaps) == 2           # sitemap.xml + extra
        urls = [s.url for s in result.all_sitemaps]
        assert "https://example.com/sitemap.xml" in urls
        assert "https://example.com/sitemap/posts.xml" in urls

    def test_all_resources_includes_homepage_and_robots(self) -> None:
        """all_resources always contains homepage and robots.txt."""
        result = SiteFetchResult(
            base_url="https://example.com",
            homepage=self._make_resource("https://example.com", "homepage"),
            robots_txt=self._make_resource("https://example.com/robots.txt", "robots.txt"),
            sitemap_xml=self._make_resource("https://example.com/sitemap.xml", "sitemap.xml"),
        )
        labels = [r.label for r in result.all_resources]
        assert "homepage" in labels
        assert "robots.txt" in labels

