"""
src/services/crawl_service.py

Sitemap inventory service.

Responsibility: turn the raw sitemap resources already fetched by
fetch_service into one deduplicated, normalized SiteInventory. Sitemap
index files (<sitemapindex>) are recursively resolved into their child
<urlset> sitemaps, up to a bounded depth, so multi-sitemap sites are
inventoried completely instead of only reading the top-level file.

This module only builds the inventory. Deterministic page-type
classification and sampling (which pages actually get crawled) and the
multi-page crawl itself are implemented separately.

Public interface:
    build_site_inventory(site, settings) -> SiteInventory
    build_site_evidence(normalized_url, settings) -> SiteEvidence
"""

import asyncio
import logging
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from src.config import Settings
from src.services.audit_models import PageEvidence, PageType, SiteEvidence, SitemapEntry, SiteInventory
from src.services.extractor_service import AuditEvidence, RobotsTxtEvidence, build_page_evidence, extract
from src.services.fetch_service import FetchedResource, SiteFetchResult, fetch_site

logger = logging.getLogger(__name__)

# Same descriptive User-Agent used by fetch_service, so sitemap-index child
# requests identify this bot consistently to server administrators.
_USER_AGENT: str = (
    "AI-SEO-Agent/0.1.0 "
    "(SEO audit crawler; educational use; contact: contact@truelinesolution.com)"
)

# --- URL classification vocabulary -----------------------------------------
# First path segment (lowercased) mapped to a PageType. Heuristic and
# intentionally simple: it only needs to be deterministic and reasonable
# across arbitrary sites, not perfectly accurate for every URL.
_UTILITY_SEGMENTS: frozenset[str] = frozenset({
    "privacy", "privacy-policy", "terms", "terms-of-service", "terms-and-conditions",
    "cookie-policy", "cookies", "sitemap", "search", "login", "signin", "signup",
    "register", "cart", "checkout", "account", "my-account", "wp-admin", "wp-login",
    "404", "page",
})
_CORE_SEGMENTS: frozenset[str] = frozenset({
    "about", "about-us", "contact", "contact-us", "pricing", "faq", "faqs",
    "team", "careers", "home",
})
_SERVICE_PRODUCT_SEGMENTS: frozenset[str] = frozenset({
    "services", "service", "products", "product", "solutions", "solution", "shop",
})
_BLOG_ARTICLE_SEGMENTS: frozenset[str] = frozenset({
    "blog", "blogs", "news", "articles", "article", "insights", "resources",
    "posts", "post",
})
_LOCATION_SEGMENTS: frozenset[str] = frozenset({
    "locations", "location", "cities", "city", "service-area", "service-areas",
    "areas-we-serve",
})
_CATEGORY_SEGMENTS: frozenset[str] = frozenset({
    "category", "categories", "tag", "tags", "collections", "collection",
})

# Fixed, deterministic order in which non-core page-type groups are sampled
_NON_CORE_SAMPLE_ORDER: tuple[PageType, ...] = (
    PageType.SERVICE_PRODUCT,
    PageType.BLOG_ARTICLE,
    PageType.LOCATION,
    PageType.CATEGORY,
    PageType.UTILITY,
)

# Content-types accepted when crawling a sampled page — anything else (PDF,
# image, etc.) is recorded as a skipped page rather than parsed as HTML.
_ALLOWED_CONTENT_TYPES: tuple[str, ...] = ("text/html", "application/xhtml+xml")


async def build_site_inventory(site: SiteFetchResult, settings: Settings) -> SiteInventory:
    """
    Build a deduplicated SiteInventory from every sitemap already fetched
    for this audit, recursively resolving sitemap index files.

    Args:
        site: The SiteFetchResult produced by fetch_service.fetch_site().
        settings: Application settings providing inventory/depth limits.

    Returns:
        SiteInventory with normalized, classified entries and a stable
        sampled_urls list built by select_crawl_sample().
    """
    entries: list[SitemapEntry] = []
    seen_urls: set[str] = set()
    seen_sitemaps: set[str] = {resource.url for resource in site.all_sitemaps}

    pending: list[FetchedResource] = list(site.all_sitemaps)
    depth: int = 0

    headers: dict[str, str] = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        while pending and depth < settings.sitemap_index_max_depth and len(entries) < settings.sitemap_inventory_limit:
            next_pending: list[FetchedResource] = []

            for resource in pending:
                if not (resource.is_success and resource.content):
                    continue

                try:
                    soup = BeautifulSoup(resource.content, "xml")
                except Exception as parse_error:
                    logger.warning("Could not parse sitemap XML at %s: %s", resource.url, parse_error)
                    continue

                child_sitemap_tags = soup.find_all("sitemap")
                if child_sitemap_tags:
                    # This file is a sitemap index — recurse into each child sitemap.
                    for tag in child_sitemap_tags:
                        loc_tag = tag.find("loc")
                        if loc_tag is None:
                            continue
                        child_url: str = loc_tag.get_text(strip=True)
                        if not child_url or child_url in seen_sitemaps:
                            continue
                        seen_sitemaps.add(child_url)
                        child_resource: FetchedResource = await _fetch_sitemap(
                            client, child_url, settings.fetch_timeout_seconds
                        )
                        next_pending.append(child_resource)
                    continue

                # Otherwise this is a leaf sitemap (<urlset>) — extract page URLs.
                for url_tag in soup.find_all("url"):
                    if len(entries) >= settings.sitemap_inventory_limit:
                        break

                    loc_tag = url_tag.find("loc")
                    if loc_tag is None:
                        continue
                    page_url: str = loc_tag.get_text(strip=True)
                    normalized: str = _normalize_url(page_url)
                    if not normalized or normalized in seen_urls:
                        continue
                    seen_urls.add(normalized)

                    lastmod_tag = url_tag.find("lastmod")
                    entries.append(SitemapEntry(
                        url=page_url,
                        source_sitemap=resource.url,
                        lastmod=lastmod_tag.get_text(strip=True) if lastmod_tag else None,
                    ))

            pending = next_pending
            depth += 1

    if pending:
        logger.warning(
            "Sitemap index recursion for %s stopped at max depth %d with %d sitemap(s) unresolved",
            site.base_url, settings.sitemap_index_max_depth, len(pending),
        )

    for entry in entries:
        entry.page_type = classify_url(entry.url)

    total_url_count: int = len(entries)
    sampled_urls: list[str] = select_crawl_sample(entries, settings)

    logger.info(
        "Sitemap inventory for %s: %d unique URL(s) discovered, %d sampled for crawling",
        site.base_url, total_url_count, len(sampled_urls),
    )

    return SiteInventory(
        base_url=site.base_url,
        entries=entries,
        total_url_count=total_url_count,
        sampled_urls=sampled_urls,
    )


def classify_url(url: str) -> PageType:
    """
    Deterministically classify one URL into a PageType using its first
    path segment. This is a heuristic, not an SEO finding: it only needs
    to be stable and reasonable across arbitrary sites so the same page
    is always sampled the same way, not perfectly accurate for every URL.

    Args:
        url: An absolute page URL.

    Returns:
        The best-guess PageType for this URL.
    """
    path: str = urlparse(url).path.strip("/").lower()
    if not path:
        return PageType.CORE  # Homepage

    segments: list[str] = [segment for segment in path.split("/") if segment]
    first_segment: str = segments[0] if segments else ""

    if first_segment in _UTILITY_SEGMENTS:
        return PageType.UTILITY
    if first_segment in _LOCATION_SEGMENTS:
        return PageType.LOCATION
    if first_segment in _BLOG_ARTICLE_SEGMENTS:
        return PageType.BLOG_ARTICLE
    if first_segment in _SERVICE_PRODUCT_SEGMENTS:
        return PageType.SERVICE_PRODUCT
    if first_segment in _CATEGORY_SEGMENTS:
        return PageType.CATEGORY
    if first_segment in _CORE_SEGMENTS:
        return PageType.CORE
    if len(segments) <= 1:
        return PageType.CORE  # Shallow, unrecognized top-level pages default to core/navigation

    return PageType.UTILITY  # Deep, unrecognized paths default to the lowest sampling priority


def select_crawl_sample(entries: list[SitemapEntry], settings: Settings) -> list[str]:
    """
    Build a stable, deterministic crawl sample from classified inventory entries.

    All core/navigation pages are included first (up to crawl_core_page_limit),
    then up to crawl_sample_per_type_limit representative pages are added per
    remaining page type, in a fixed group order, until crawl_sample_limit is
    reached. Entry order within each group is preserved from the inventory,
    so an unchanged site always produces the same sample.

    Args:
        entries: Classified SitemapEntry list (page_type must already be set).
        settings: Application settings providing the sampling limits.

    Returns:
        A list of sampled page URLs, never longer than settings.crawl_sample_limit.
    """
    total_budget: int = settings.crawl_sample_limit
    if total_budget <= 0 or not entries:
        return []

    core_entries: list[SitemapEntry] = [entry for entry in entries if entry.page_type == PageType.CORE]
    core_take: int = min(len(core_entries), settings.crawl_core_page_limit, total_budget)
    sampled: list[str] = [entry.url for entry in core_entries[:core_take]]

    remaining: int = total_budget - len(sampled)
    for page_type in _NON_CORE_SAMPLE_ORDER:
        if remaining <= 0:
            break
        group: list[str] = [entry.url for entry in entries if entry.page_type == page_type]
        take: int = min(len(group), settings.crawl_sample_per_type_limit, remaining)
        sampled.extend(group[:take])
        remaining -= take

    return sampled


async def crawl_sampled_pages(
    inventory: SiteInventory,
    robots_txt: RobotsTxtEvidence | None,
    settings: Settings,
) -> list[FetchedResource]:
    """
    Fetch every URL in inventory.sampled_urls with bounded concurrency.

    Same-origin URLs, robots.txt Disallow rules, content-type, and response
    size are all checked before content is accepted. Every sampled URL
    always produces exactly one FetchedResource — network errors, off-origin
    URLs, disallowed paths, and oversized/non-HTML responses are recorded as
    evidence (is_success=False, error_message set) instead of raising, so one
    bad page never aborts the audit.

    Pages that fetch successfully via httpx but look like a JS shell (very
    little visible text — typical of client-rendered single-page apps) are
    re-rendered once using a single shared headless Chromium instance. If
    rendering also fails, the original httpx result is kept.

    Args:
        inventory: SiteInventory whose sampled_urls will be crawled.
        robots_txt: Parsed robots.txt evidence for the audited site, if any.
        settings: Application settings providing concurrency/size/timeout limits.

    Returns:
        One FetchedResource per URL in inventory.sampled_urls, same order.
    """
    if not inventory.sampled_urls:
        return []

    base_netloc: str = urlparse(inventory.base_url).netloc.lower()
    disallow_rules: list[str] = robots_txt.disallow_rules if robots_txt else []
    semaphore = asyncio.Semaphore(max(1, settings.crawl_concurrency))

    headers: dict[str, str] = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }

    async def _crawl_one(client: httpx.AsyncClient, url: str) -> FetchedResource:
        async with semaphore:
            return await _crawl_page(client, url, base_netloc, disallow_rules, settings)

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, max_redirects=settings.fetch_max_redirects
    ) as client:
        results: list[FetchedResource] = list(await asyncio.gather(
            *(_crawl_one(client, url) for url in inventory.sampled_urls)
        ))

    results = await _apply_playwright_fallback(results, settings)

    success_count: int = sum(1 for result in results if result.is_success)
    fallback_count: int = sum(1 for result in results if result.used_playwright_fallback)
    logger.info(
        "Sampled crawl complete: %d/%d page(s) fetched successfully for %s (%d re-rendered with Playwright)",
        success_count, len(results), inventory.base_url, fallback_count,
    )
    return results


async def build_site_evidence(normalized_url: str, settings: Settings) -> SiteEvidence:
    """
    Orchestrate a full multi-page audit: fetch the site, build the sitemap
    inventory, crawl the sampled pages, and assemble one SiteEvidence.

    This is the production entry point the Phase 4 section pipeline needs
    (report_service.build_audit_context() takes a SiteEvidence) — it reuses
    extractor_service.extract()'s existing homepage/robots.txt/sitemap
    parsing rather than duplicating it, plus build_page_evidence() for
    every sampled page and the homepage itself.

    Args:
        normalized_url: The website URL to audit (already validated/normalised).
        settings: Application settings providing fetch/crawl/sitemap limits.

    Returns:
        SiteEvidence with the homepage, sampled pages, sitemap inventory,
        robots.txt evidence, and sitemap evidence all populated.
    """
    site: SiteFetchResult = await fetch_site(normalized_url, settings)
    homepage_audit_evidence: AuditEvidence = extract(site)
    inventory: SiteInventory = await build_site_inventory(site, settings)
    sampled_resources: list[FetchedResource] = await crawl_sampled_pages(
        inventory, homepage_audit_evidence.robots_txt, settings,
    )

    page_types_by_url: dict[str, PageType] = {entry.url: entry.page_type for entry in inventory.entries}
    sampled_pages: list[PageEvidence] = [
        build_page_evidence(resource, page_types_by_url.get(resource.url, PageType.UTILITY), site.base_url)
        for resource in sampled_resources
    ]
    homepage_page_evidence: PageEvidence = build_page_evidence(site.homepage, PageType.CORE, site.base_url)

    evidence = SiteEvidence(
        base_url=site.base_url,
        final_url=homepage_audit_evidence.final_url,
        homepage=homepage_page_evidence,
        sampled_pages=sampled_pages,
        inventory=inventory,
        robots_txt=homepage_audit_evidence.robots_txt,
        sitemaps=homepage_audit_evidence.sitemaps,
        unverifiable_fields=homepage_audit_evidence.unverifiable_fields,
    )
    logger.info(
        "build_site_evidence complete for %s: %d sampled page(s), %d sitemap URL(s) total",
        normalized_url, len(sampled_pages), inventory.total_url_count,
    )
    return evidence


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _crawl_page(
    client: httpx.AsyncClient,
    url: str,
    base_netloc: str,
    disallow_rules: list[str],
    settings: Settings,
) -> FetchedResource:
    """Fetch and validate one sampled page URL; never raises."""
    if urlparse(url).netloc.lower() != base_netloc:
        return FetchedResource(
            url=url, label="sampled_page", is_fetched=True, is_success=False,
            error_message="Skipped: URL is not same-origin as the audited site",
        )

    if _is_disallowed(url, disallow_rules):
        return FetchedResource(
            url=url, label="sampled_page", is_fetched=True, is_success=False,
            error_message="Skipped: blocked by a robots.txt Disallow rule",
        )

    try:
        response: httpx.Response = await client.get(url, timeout=settings.fetch_timeout_seconds)
    except httpx.HTTPError as exc:
        logger.warning("Could not crawl sampled page %s: %s", url, exc)
        return FetchedResource(
            url=url, label="sampled_page", is_fetched=True, is_success=False, error_message=str(exc),
        )

    content_type: str = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type and not any(content_type.startswith(allowed) for allowed in _ALLOWED_CONTENT_TYPES):
        return FetchedResource(
            url=url, label="sampled_page", final_url=str(response.url), status_code=response.status_code,
            is_fetched=True, is_success=False,
            error_message=f"Skipped: unsupported content-type '{content_type}'",
        )

    if len(response.content) > settings.crawl_max_page_bytes:
        return FetchedResource(
            url=url, label="sampled_page", final_url=str(response.url), status_code=response.status_code,
            is_fetched=True, is_success=False,
            error_message=f"Skipped: response exceeded the {settings.crawl_max_page_bytes}-byte size limit",
        )

    return FetchedResource(
        url=url,
        label="sampled_page",
        final_url=str(response.url),
        status_code=response.status_code,
        content=response.text if response.is_success else "",
        is_success=response.is_success,
        is_fetched=True,
        redirect_chain=[str(redirect.url) for redirect in response.history],
    )


def _is_disallowed(url: str, disallow_rules: list[str]) -> bool:
    """
    Check a URL's path against robots.txt Disallow rules using simple prefix
    matching. Wildcard (*) and $ end-anchor syntax are not evaluated — this
    is a deliberately conservative heuristic, not a full robots.txt parser.
    """
    path: str = urlparse(url).path or "/"
    for rule in disallow_rules:
        cleaned_rule: str = rule.strip()
        if not cleaned_rule:
            continue
        if path.startswith(cleaned_rule):
            return True
    return False


def _looks_like_js_shell(html: str, settings: Settings) -> bool:
    """
    Heuristic check for a client-rendered "app shell" page: very little
    visible text is present in the static HTML because content is injected
    by JavaScript after load. Not a definitive test, just a trigger for the
    Playwright re-render fallback.
    """
    if not html.strip():
        return True
    visible_text: str = BeautifulSoup(html, "lxml").get_text(separator=" ", strip=True)
    word_count: int = len(visible_text.split())
    return word_count < settings.crawl_js_shell_word_threshold


async def _apply_playwright_fallback(
    results: list[FetchedResource], settings: Settings
) -> list[FetchedResource]:
    """
    Re-render any JS-shell/empty pages using one shared headless Chromium
    browser instance. Pages that already have real content are left as-is,
    so the browser is only launched when at least one page actually needs it.
    """
    shell_indices: list[int] = [
        i for i, result in enumerate(results)
        if result.is_success and _looks_like_js_shell(result.content, settings)
    ]
    if not shell_indices:
        return results

    logger.info("Re-rendering %d JS-shell page(s) with Playwright", len(shell_indices))
    semaphore = asyncio.Semaphore(max(1, settings.crawl_concurrency))

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            async def _render_one(index: int) -> None:
                async with semaphore:
                    results[index] = await _render_page(browser, results[index], settings)

            await asyncio.gather(*(_render_one(index) for index in shell_indices))
        finally:
            await browser.close()

    return results


async def _render_page(browser: PlaywrightBrowser, original: FetchedResource, settings: Settings) -> FetchedResource:
    """
    Re-fetch one page's rendered DOM via headless Chromium. On any rendering
    failure, the original httpx result is kept unchanged — a broken render
    must never be worse than the evidence we already had.
    """
    context = await browser.new_context(user_agent=_USER_AGENT, ignore_https_errors=True)
    try:
        page = await context.new_page()
        page.set_default_navigation_timeout(settings.playwright_navigation_timeout_ms)
        response = await page.goto(original.url, wait_until="domcontentloaded")
        content: str = await page.content()
        return FetchedResource(
            url=original.url,
            label=original.label,
            final_url=page.url or original.final_url,
            status_code=response.status if response else original.status_code,
            content=content,
            is_success=True,
            is_fetched=True,
            used_playwright_fallback=True,
        )
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        logger.warning("Playwright rendering failed for %s: %s", original.url, exc)
        return original
    finally:
        await context.close()


async def _fetch_sitemap(client: httpx.AsyncClient, url: str, timeout_seconds: int) -> FetchedResource:
    """
    Fetch one child sitemap URL discovered inside a sitemap index.

    Failures are recorded as an unsuccessful FetchedResource rather than
    raising, consistent with fetch_service's error-as-evidence approach.
    """
    try:
        response: httpx.Response = await client.get(url, timeout=timeout_seconds)
        return FetchedResource(
            url=url,
            label=f"sitemap:{url}",
            final_url=str(response.url),
            status_code=response.status_code,
            content=response.text if response.is_success else "",
            is_success=response.is_success,
            is_fetched=True,
        )
    except httpx.HTTPError as exc:
        logger.warning("Could not fetch child sitemap %s: %s", url, exc)
        return FetchedResource(
            url=url,
            label=f"sitemap:{url}",
            is_fetched=True,
            is_success=False,
            error_message=str(exc),
        )


def _normalize_url(url: str) -> str:
    """Normalize a sitemap <loc> URL for deduplication (strip whitespace and trailing slash)."""
    cleaned: str = url.strip()
    if not cleaned:
        return ""
    if cleaned.endswith("/") and cleaned.count("/") > 2:
        # Keep a bare scheme://host/ as-is; only strip trailing slash on deeper paths
        cleaned = cleaned.rstrip("/")
    return cleaned
