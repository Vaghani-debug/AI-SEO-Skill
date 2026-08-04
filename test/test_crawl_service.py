"""
test/test_crawl_service.py

Unit tests for src/services/crawl_service.py.

All network calls (child sitemap fetches during index recursion) are
mocked so these tests run offline and deterministically.

Run with:
    pytest test/test_crawl_service.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import Settings
from src.services.audit_models import PageType, SiteInventory, SitemapEntry
from src.services.crawl_service import (
    build_site_evidence,
    build_site_inventory,
    classify_url,
    crawl_sampled_pages,
    select_crawl_sample,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.services.extractor_service import RobotsTxtEvidence
from src.services.fetch_service import FetchedResource, SiteFetchResult


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def settings() -> Settings:
    s = Settings()
    s.fetch_timeout_seconds = 5
    s.sitemap_inventory_limit = 500
    s.sitemap_index_max_depth = 3
    s.crawl_sample_limit = 30
    return s


def _resource(url: str, content: str, is_success: bool = True) -> FetchedResource:
    return FetchedResource(
        url=url,
        label=f"sitemap:{url}",
        final_url=url,
        status_code=200 if is_success else 404,
        content=content,
        is_success=is_success,
        is_fetched=True,
    )


def _urlset_xml(urls_and_lastmods: list[tuple[str, str | None]]) -> str:
    entries = "".join(
        f"<url><loc>{url}</loc>{f'<lastmod>{lastmod}</lastmod>' if lastmod else ''}</url>"
        for url, lastmod in urls_and_lastmods
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'


def _sitemapindex_xml(child_urls: list[str]) -> str:
    entries = "".join(f"<sitemap><loc>{url}</loc></sitemap>" for url in child_urls)
    return f'<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</sitemapindex>'


def _make_site(sitemap_xml: FetchedResource, extra_sitemaps: list[FetchedResource] | None = None) -> SiteFetchResult:
    return SiteFetchResult(
        base_url="https://example.com",
        homepage=_resource("https://example.com", "<html></html>"),
        robots_txt=_resource("https://example.com/robots.txt", ""),
        sitemap_xml=sitemap_xml,
        extra_sitemaps=extra_sitemaps or [],
    )


def _make_async_client_mock(responses: dict[str, MagicMock]) -> AsyncMock:
    client = AsyncMock()

    async def mock_get(url: str, **kwargs) -> MagicMock:
        url_str = str(url)
        if url_str in responses:
            return responses[url_str]
        return _make_mock_response(status_code=404, text="Not Found", url=url_str)

    client.get = mock_get
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    return client


def _make_mock_response(status_code: int = 200, text: str = "", url: str = "https://example.com") -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text
    response.content = text.encode("utf-8")
    response.headers = {"content-type": "text/html; charset=utf-8"}
    response.url = httpx.URL(url)
    response.is_success = 200 <= status_code < 300
    response.history = []
    return response


def _make_page_inventory(urls: list[str], base_url: str = "https://example.com") -> SiteInventory:
    entries = [SitemapEntry(url=url, source_sitemap=f"{base_url}/sitemap.xml") for url in urls]
    return SiteInventory(base_url=base_url, entries=entries, total_url_count=len(entries), sampled_urls=urls)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildSiteInventoryLeafSitemap:
    """Tests for a single, non-indexed <urlset> sitemap."""

    @pytest.mark.asyncio
    async def test_extracts_urls_and_lastmod(self, settings: Settings) -> None:
        xml = _urlset_xml([
            ("https://example.com/", "2026-01-01"),
            ("https://example.com/about", None),
        ])
        site = _make_site(_resource("https://example.com/sitemap.xml", xml))

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=_make_async_client_mock({})):
            inventory: SiteInventory = await build_site_inventory(site, settings)

        assert inventory.total_url_count == 2
        assert inventory.entries[0].url == "https://example.com/"
        assert inventory.entries[0].lastmod == "2026-01-01"
        assert inventory.entries[1].lastmod is None
        assert inventory.entries[0].source_sitemap == "https://example.com/sitemap.xml"

    @pytest.mark.asyncio
    async def test_deduplicates_repeated_urls(self, settings: Settings) -> None:
        xml = _urlset_xml([
            ("https://example.com/about", None),
            ("https://example.com/about/", None),  # Same page, trailing slash
        ])
        site = _make_site(_resource("https://example.com/sitemap.xml", xml))

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=_make_async_client_mock({})):
            inventory = await build_site_inventory(site, settings)

        assert inventory.total_url_count == 1

    @pytest.mark.asyncio
    async def test_inaccessible_sitemap_yields_empty_inventory(self, settings: Settings) -> None:
        site = _make_site(_resource("https://example.com/sitemap.xml", "", is_success=False))

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=_make_async_client_mock({})):
            inventory = await build_site_inventory(site, settings)

        assert inventory.total_url_count == 0
        assert inventory.entries == []
        assert inventory.sampled_urls == []


class TestBuildSiteInventorySitemapIndex:
    """Tests for recursive resolution of a <sitemapindex> file."""

    @pytest.mark.asyncio
    async def test_recurses_into_child_sitemaps(self, settings: Settings) -> None:
        index_xml = _sitemapindex_xml([
            "https://example.com/sitemap-pages.xml",
            "https://example.com/sitemap-posts.xml",
        ])
        pages_xml = _urlset_xml([("https://example.com/", None)])
        posts_xml = _urlset_xml([("https://example.com/blog/post-1", "2026-02-01")])

        site = _make_site(_resource("https://example.com/sitemap.xml", index_xml))

        responses = {
            "https://example.com/sitemap-pages.xml": _make_mock_response(text=pages_xml, url="https://example.com/sitemap-pages.xml"),
            "https://example.com/sitemap-posts.xml": _make_mock_response(text=posts_xml, url="https://example.com/sitemap-posts.xml"),
        }

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=_make_async_client_mock(responses)):
            inventory = await build_site_inventory(site, settings)

        assert inventory.total_url_count == 2
        urls = {entry.url for entry in inventory.entries}
        assert urls == {"https://example.com/", "https://example.com/blog/post-1"}
        posts_entry = next(e for e in inventory.entries if e.url == "https://example.com/blog/post-1")
        assert posts_entry.source_sitemap == "https://example.com/sitemap-posts.xml"
        assert posts_entry.lastmod == "2026-02-01"

    @pytest.mark.asyncio
    async def test_stops_at_max_depth(self, settings: Settings) -> None:
        settings.sitemap_index_max_depth = 1  # Only resolve the top-level index, not its children

        index_xml = _sitemapindex_xml(["https://example.com/sitemap-pages.xml"])
        pages_xml = _urlset_xml([("https://example.com/", None)])

        site = _make_site(_resource("https://example.com/sitemap.xml", index_xml))
        responses = {
            "https://example.com/sitemap-pages.xml": _make_mock_response(text=pages_xml, url="https://example.com/sitemap-pages.xml"),
        }

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=_make_async_client_mock(responses)):
            inventory = await build_site_inventory(site, settings)

        # Depth 1 only resolves the index itself (fetching, but not parsing, its children)
        assert inventory.total_url_count == 0


class TestBuildSiteInventoryLimits:
    """Tests for the configurable inventory/sample size limits."""

    @pytest.mark.asyncio
    async def test_respects_inventory_limit(self, settings: Settings) -> None:
        settings.sitemap_inventory_limit = 2
        xml = _urlset_xml([
            ("https://example.com/1", None),
            ("https://example.com/2", None),
            ("https://example.com/3", None),
        ])
        site = _make_site(_resource("https://example.com/sitemap.xml", xml))

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=_make_async_client_mock({})):
            inventory = await build_site_inventory(site, settings)

        assert inventory.total_url_count == 2

    @pytest.mark.asyncio
    async def test_sampled_urls_bounded_by_crawl_sample_limit(self, settings: Settings) -> None:
        settings.crawl_sample_limit = 1
        xml = _urlset_xml([
            ("https://example.com/1", None),
            ("https://example.com/2", None),
        ])
        site = _make_site(_resource("https://example.com/sitemap.xml", xml))

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=_make_async_client_mock({})):
            inventory = await build_site_inventory(site, settings)

        assert inventory.total_url_count == 2
        assert len(inventory.sampled_urls) == 1


class TestClassifyUrl:
    """Tests for the deterministic URL -> PageType classifier."""

    def test_homepage_is_core(self) -> None:
        assert classify_url("https://example.com/") == PageType.CORE
        assert classify_url("https://example.com") == PageType.CORE

    def test_recognized_core_pages(self) -> None:
        assert classify_url("https://example.com/about-us") == PageType.CORE
        assert classify_url("https://example.com/contact") == PageType.CORE

    def test_service_and_product_pages(self) -> None:
        assert classify_url("https://example.com/services/hair-transplant") == PageType.SERVICE_PRODUCT
        assert classify_url("https://example.com/products/widget") == PageType.SERVICE_PRODUCT

    def test_blog_and_article_pages(self) -> None:
        assert classify_url("https://example.com/blog/post-1") == PageType.BLOG_ARTICLE
        assert classify_url("https://example.com/news/announcement") == PageType.BLOG_ARTICLE

    def test_location_pages(self) -> None:
        assert classify_url("https://example.com/locations/dallas") == PageType.LOCATION

    def test_category_pages(self) -> None:
        assert classify_url("https://example.com/category/tools") == PageType.CATEGORY

    def test_utility_pages(self) -> None:
        assert classify_url("https://example.com/privacy-policy") == PageType.UTILITY
        assert classify_url("https://example.com/cart") == PageType.UTILITY

    def test_shallow_unrecognized_path_defaults_to_core(self) -> None:
        assert classify_url("https://example.com/pricing-plans") == PageType.CORE

    def test_deep_unrecognized_path_defaults_to_utility(self) -> None:
        assert classify_url("https://example.com/some/deep/unrecognized/path") == PageType.UTILITY


class TestSelectCrawlSample:
    """Tests for deterministic, capped crawl sample selection."""

    def _entry(self, url: str, page_type: PageType) -> SitemapEntry:
        return SitemapEntry(url=url, source_sitemap="https://example.com/sitemap.xml", page_type=page_type)

    def test_includes_all_core_pages_within_cap(self) -> None:
        settings = Settings()
        settings.crawl_sample_limit = 30
        settings.crawl_core_page_limit = 10
        entries = [self._entry(f"https://example.com/core-{i}", PageType.CORE) for i in range(3)]

        sampled = select_crawl_sample(entries, settings)

        assert sampled == [e.url for e in entries]

    def test_caps_core_pages_at_core_page_limit(self) -> None:
        settings = Settings()
        settings.crawl_sample_limit = 30
        settings.crawl_core_page_limit = 2
        entries = [self._entry(f"https://example.com/core-{i}", PageType.CORE) for i in range(5)]

        sampled = select_crawl_sample(entries, settings)

        assert sampled == ["https://example.com/core-0", "https://example.com/core-1"]

    def test_samples_up_to_per_type_limit_across_groups(self) -> None:
        settings = Settings()
        settings.crawl_sample_limit = 30
        settings.crawl_core_page_limit = 10
        settings.crawl_sample_per_type_limit = 2
        entries = (
            [self._entry(f"https://example.com/blog-{i}", PageType.BLOG_ARTICLE) for i in range(5)]
            + [self._entry(f"https://example.com/service-{i}", PageType.SERVICE_PRODUCT) for i in range(5)]
        )

        sampled = select_crawl_sample(entries, settings)

        # Fixed group order: SERVICE_PRODUCT before BLOG_ARTICLE
        assert sampled == [
            "https://example.com/service-0", "https://example.com/service-1",
            "https://example.com/blog-0", "https://example.com/blog-1",
        ]

    def test_respects_overall_sample_limit(self) -> None:
        settings = Settings()
        settings.crawl_sample_limit = 3
        settings.crawl_core_page_limit = 10
        settings.crawl_sample_per_type_limit = 5
        entries = (
            [self._entry("https://example.com/", PageType.CORE)]
            + [self._entry(f"https://example.com/service-{i}", PageType.SERVICE_PRODUCT) for i in range(5)]
        )

        sampled = select_crawl_sample(entries, settings)

        assert len(sampled) == 3

    def test_empty_entries_returns_empty_sample(self) -> None:
        assert select_crawl_sample([], Settings()) == []

    def test_stable_ordering_is_deterministic_across_calls(self) -> None:
        settings = Settings()
        entries = [self._entry(f"https://example.com/blog-{i}", PageType.BLOG_ARTICLE) for i in range(3)]

        first_call = select_crawl_sample(entries, settings)
        second_call = select_crawl_sample(entries, settings)

        assert first_call == second_call


class TestCrawlSampledPages:
    """Tests for bounded concurrent page crawling with same-origin/robots/size checks."""

    def _settings(self) -> Settings:
        s = Settings()
        s.fetch_timeout_seconds = 5
        s.fetch_max_redirects = 3
        s.crawl_concurrency = 5
        s.crawl_max_page_bytes = 2_000_000
        s.crawl_js_shell_word_threshold = 0  # These tests only exercise the httpx path, not the Playwright fallback
        return s

    async def test_empty_sample_returns_empty_list(self) -> None:
        inventory = _make_page_inventory([])
        result = await crawl_sampled_pages(inventory, None, self._settings())
        assert result == []

    async def test_fetches_same_origin_page_successfully(self) -> None:
        inventory = _make_page_inventory(["https://example.com/about"])
        responses = {"https://example.com/about": _make_mock_response(text="<html>About</html>", url="https://example.com/about")}
        client = _make_async_client_mock(responses)

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client):
            result = await crawl_sampled_pages(inventory, None, self._settings())

        assert len(result) == 1
        assert result[0].is_success is True
        assert result[0].content == "<html>About</html>"

    async def test_skips_off_origin_url(self) -> None:
        inventory = _make_page_inventory(["https://other-domain.com/page"])
        client = _make_async_client_mock({})

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client):
            result = await crawl_sampled_pages(inventory, None, self._settings())

        assert result[0].is_success is False
        assert "same-origin" in result[0].error_message

    async def test_skips_url_blocked_by_robots_disallow(self) -> None:
        inventory = _make_page_inventory(["https://example.com/admin/panel"])
        robots = RobotsTxtEvidence(
            is_accessible=True, http_status=200,
            disallow_rules=["/admin"], allow_rules=[], sitemap_urls=[], blocks_root_path=False,
        )
        client = _make_async_client_mock({})

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client):
            result = await crawl_sampled_pages(inventory, robots, self._settings())

        assert result[0].is_success is False
        assert "Disallow" in result[0].error_message

    async def test_skips_non_html_content_type(self) -> None:
        inventory = _make_page_inventory(["https://example.com/file.pdf"])
        response = _make_mock_response(text="%PDF-1.4", url="https://example.com/file.pdf")
        response.headers = {"content-type": "application/pdf"}
        client = _make_async_client_mock({"https://example.com/file.pdf": response})

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client):
            result = await crawl_sampled_pages(inventory, None, self._settings())

        assert result[0].is_success is False
        assert "content-type" in result[0].error_message

    async def test_skips_oversized_response(self) -> None:
        settings = self._settings()
        settings.crawl_max_page_bytes = 10
        inventory = _make_page_inventory(["https://example.com/big"])
        response = _make_mock_response(text="<html>" + ("x" * 100) + "</html>", url="https://example.com/big")
        client = _make_async_client_mock({"https://example.com/big": response})

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client):
            result = await crawl_sampled_pages(inventory, None, settings)

        assert result[0].is_success is False
        assert "size limit" in result[0].error_message

    async def test_records_network_error_as_evidence(self) -> None:
        inventory = _make_page_inventory(["https://example.com/unreachable"])
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client):
            result = await crawl_sampled_pages(inventory, None, self._settings())

        assert result[0].is_success is False
        assert result[0].error_message

    async def test_preserves_input_order_for_multiple_urls(self) -> None:
        urls = [f"https://example.com/page-{i}" for i in range(4)]
        inventory = _make_page_inventory(urls)
        responses = {url: _make_mock_response(text=f"<html>{i}</html>", url=url) for i, url in enumerate(urls)}
        client = _make_async_client_mock(responses)

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client):
            result = await crawl_sampled_pages(inventory, None, self._settings())

        assert [r.url for r in result] == urls
        assert all(r.is_success for r in result)


def _make_playwright_context_manager(
    rendered_html_by_url: dict[str, str] | None = None,
    raise_for_url: dict[str, Exception] | None = None,
) -> MagicMock:
    """Build a mock standing in for `async with async_playwright() as playwright:`."""
    rendered_html_by_url = rendered_html_by_url or {}
    raise_for_url = raise_for_url or {}

    def _make_page() -> MagicMock:
        page = MagicMock()
        page.url = ""

        async def goto(url: str, wait_until: str | None = None):
            page.url = url
            if url in raise_for_url:
                raise raise_for_url[url]
            return MagicMock(status=200)

        async def content() -> str:
            return rendered_html_by_url.get(page.url, "<html></html>")

        page.goto = AsyncMock(side_effect=goto)
        page.content = AsyncMock(side_effect=content)
        page.set_default_navigation_timeout = MagicMock()
        return page

    context = AsyncMock()
    context.new_page = AsyncMock(side_effect=_make_page)
    context.close = AsyncMock()

    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    chromium = MagicMock()
    chromium.launch = AsyncMock(return_value=browser)

    playwright_obj = MagicMock()
    playwright_obj.chromium = chromium

    pw_context_manager = MagicMock()
    pw_context_manager.__aenter__ = AsyncMock(return_value=playwright_obj)
    pw_context_manager.__aexit__ = AsyncMock(return_value=False)
    return pw_context_manager


class TestLooksLikeJsShell:
    """Tests for the JS-shell detection heuristic."""

    def test_empty_html_is_a_shell(self) -> None:
        from src.services.crawl_service import _looks_like_js_shell

        assert _looks_like_js_shell("", Settings()) is True

    def test_thin_html_below_threshold_is_a_shell(self) -> None:
        from src.services.crawl_service import _looks_like_js_shell

        settings = Settings()
        settings.crawl_js_shell_word_threshold = 50
        html = "<html><body><div id='root'></div></body></html>"

        assert _looks_like_js_shell(html, settings) is True

    def test_substantial_text_is_not_a_shell(self) -> None:
        from src.services.crawl_service import _looks_like_js_shell

        settings = Settings()
        settings.crawl_js_shell_word_threshold = 5
        html = "<html><body><p>" + " ".join(["word"] * 20) + "</p></body></html>"

        assert _looks_like_js_shell(html, settings) is False


class TestCrawlSampledPagesPlaywrightFallback:
    """Tests for the single-shared-browser Playwright re-render fallback."""

    def _settings(self) -> Settings:
        s = Settings()
        s.fetch_timeout_seconds = 5
        s.fetch_max_redirects = 3
        s.crawl_concurrency = 5
        s.crawl_max_page_bytes = 2_000_000
        s.crawl_js_shell_word_threshold = 10
        s.playwright_navigation_timeout_ms = 5_000
        return s

    async def test_does_not_launch_browser_when_content_is_substantial(self) -> None:
        url = "https://example.com/about"
        inventory = _make_page_inventory([url])
        text = "<html><body><p>" + " ".join(["word"] * 30) + "</p></body></html>"
        client = _make_async_client_mock({url: _make_mock_response(text=text, url=url)})
        pw_context_manager = _make_playwright_context_manager()

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client), \
             patch("src.services.crawl_service.async_playwright", return_value=pw_context_manager) as pw_mock:
            result = await crawl_sampled_pages(inventory, None, self._settings())

        pw_mock.assert_not_called()
        assert result[0].used_playwright_fallback is False
        assert result[0].content == text

    async def test_renders_js_shell_page_successfully(self) -> None:
        url = "https://example.com/app"
        inventory = _make_page_inventory([url])
        shell_html = "<html><body><div id='root'></div></body></html>"
        rendered_html = "<html><body><p>" + " ".join(["word"] * 30) + "</p></body></html>"
        client = _make_async_client_mock({url: _make_mock_response(text=shell_html, url=url)})
        pw_context_manager = _make_playwright_context_manager(rendered_html_by_url={url: rendered_html})

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client), \
             patch("src.services.crawl_service.async_playwright", return_value=pw_context_manager):
            result = await crawl_sampled_pages(inventory, None, self._settings())

        assert result[0].used_playwright_fallback is True
        assert result[0].content == rendered_html
        assert result[0].is_success is True

    async def test_keeps_original_result_when_rendering_fails(self) -> None:
        url = "https://example.com/app"
        inventory = _make_page_inventory([url])
        shell_html = "<html><body><div id='root'></div></body></html>"
        client = _make_async_client_mock({url: _make_mock_response(text=shell_html, url=url)})
        pw_context_manager = _make_playwright_context_manager(
            raise_for_url={url: PlaywrightTimeoutError("navigation timed out")}
        )

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client), \
             patch("src.services.crawl_service.async_playwright", return_value=pw_context_manager):
            result = await crawl_sampled_pages(inventory, None, self._settings())

        assert result[0].used_playwright_fallback is False
        assert result[0].content == shell_html

    async def test_skips_fallback_for_already_failed_httpx_result(self) -> None:
        url = "https://example.com/missing"
        inventory = _make_page_inventory([url])
        client = _make_async_client_mock({url: _make_mock_response(status_code=404, text="", url=url)})
        pw_context_manager = _make_playwright_context_manager()

        with patch("src.services.crawl_service.httpx.AsyncClient", return_value=client), \
             patch("src.services.crawl_service.async_playwright", return_value=pw_context_manager) as pw_mock:
            result = await crawl_sampled_pages(inventory, None, self._settings())

        pw_mock.assert_not_called()
        assert result[0].is_success is False
        assert result[0].used_playwright_fallback is False


class TestBuildSiteEvidence:
    """
    Tests for build_site_evidence() — the orchestrator that assembles a
    production SiteEvidence from fetch_site() + build_site_inventory() +
    crawl_sampled_pages(). Those three calls are patched here since they
    are already covered by their own test classes above; this class only
    verifies the orchestration/assembly logic.
    """

    def _settings(self) -> Settings:
        s = Settings()
        s.fetch_timeout_seconds = 5
        return s

    async def test_assembles_site_evidence_from_orchestrated_calls(self) -> None:
        site = SiteFetchResult(
            base_url="https://example.com",
            homepage=_resource("https://example.com", "<html><head><title>Home</title></head></html>"),
            robots_txt=_resource("https://example.com/robots.txt", "User-agent: *\nDisallow:\n"),
            sitemap_xml=_resource("https://example.com/sitemap.xml", ""),
        )
        inventory = SiteInventory(
            base_url="https://example.com",
            entries=[
                SitemapEntry(
                    url="https://example.com/about",
                    source_sitemap="https://example.com/sitemap.xml",
                    page_type=PageType.CORE,
                ),
            ],
            total_url_count=1,
            sampled_urls=["https://example.com/about"],
        )
        sampled_resource = _resource("https://example.com/about", "<html><head><title>About</title></head></html>")

        with patch("src.services.crawl_service.fetch_site", AsyncMock(return_value=site)), \
             patch("src.services.crawl_service.build_site_inventory", AsyncMock(return_value=inventory)), \
             patch("src.services.crawl_service.crawl_sampled_pages", AsyncMock(return_value=[sampled_resource])):
            evidence = await build_site_evidence("https://example.com", self._settings())

        assert evidence.base_url == "https://example.com"
        assert evidence.homepage.page_title == "Home"
        assert evidence.homepage.page_type == PageType.CORE
        assert len(evidence.sampled_pages) == 1
        assert evidence.sampled_pages[0].url == "https://example.com/about"
        assert evidence.sampled_pages[0].page_title == "About"
        assert evidence.sampled_pages[0].page_type == PageType.CORE
        assert evidence.inventory is inventory
        assert evidence.robots_txt is not None

    async def test_defaults_unmatched_sampled_page_to_utility_type(self) -> None:
        # A sampled URL with no matching inventory entry (defensive edge case) still gets a PageType.
        site = SiteFetchResult(
            base_url="https://example.com",
            homepage=_resource("https://example.com", "<html></html>"),
            robots_txt=_resource("https://example.com/robots.txt", ""),
            sitemap_xml=_resource("https://example.com/sitemap.xml", ""),
        )
        inventory = SiteInventory(base_url="https://example.com", entries=[], total_url_count=0, sampled_urls=[])
        sampled_resource = _resource("https://example.com/unlisted", "<html></html>")

        with patch("src.services.crawl_service.fetch_site", AsyncMock(return_value=site)), \
             patch("src.services.crawl_service.build_site_inventory", AsyncMock(return_value=inventory)), \
             patch("src.services.crawl_service.crawl_sampled_pages", AsyncMock(return_value=[sampled_resource])):
            evidence = await build_site_evidence("https://example.com", self._settings())

        assert evidence.sampled_pages[0].page_type == PageType.UTILITY

