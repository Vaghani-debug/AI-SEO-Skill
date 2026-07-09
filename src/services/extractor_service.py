"""
src/services/extractor_service.py

Verified SEO data extraction service.

Responsibility: parse the raw HTML, robots.txt, and sitemap content
returned by fetch_service and extract every SEO-relevant field that can
be verified from static content.

Fields that require a live browser, Google Search Console, Lighthouse,
or third-party tools (Core Web Vitals, mobile score, schema injected by
JavaScript, backlinks, etc.) are NOT extracted.  They are represented by
the standard unverifiable marker so the LLM report generator can mention
them honestly rather than guessing.

Public interface:
    extract(site: SiteFetchResult) -> AuditEvidence
"""

import logging  # Standard logging — records extraction progress and field-level findings
import re  # Used to parse robots.txt disallow/allow rules
from dataclasses import dataclass, field  # Lightweight data containers for structured evidence
from urllib.parse import urljoin, urlparse  # URL manipulation for link classification

from bs4 import BeautifulSoup  # HTML parser; extracts structured elements from raw HTML

from src.services.fetch_service import SiteFetchResult  # Input type from the fetch layer

# Module-level logger
logger = logging.getLogger(__name__)  # Resolves to "src.services.extractor_service"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Standard phrase required by AI_REPORT_GUIDELINES.md for unverifiable data
UNVERIFIABLE: str = "Could not be verified in this audit."

# BeautifulSoup parser — "lxml" is the fast, lenient C parser installed in requirements.txt
_PARSER: str = "lxml"

# Maximum number of internal/external links to store per page (prevents enormous lists)
_MAX_LINKS: int = 200


# ---------------------------------------------------------------------------
# Evidence data models
# ---------------------------------------------------------------------------

@dataclass
class ImageInfo:
    """
    Metadata for a single image element found in the HTML.

    Only information visible in the static HTML is recorded.
    Whether the image actually loads is not verified in the MVP.
    """

    src: str
    # The resolved URL of the image (absolute, after joining with the page base URL)

    alt: str
    # The alt attribute value; empty string "" if the attribute is present but blank

    has_alt_attribute: bool
    # True if an alt="" attribute exists at all (even if its value is empty)
    # False if the alt attribute is completely absent from the <img> tag


@dataclass
class RobotsTxtEvidence:
    """
    Verified findings extracted from the /robots.txt file.

    Only data present in the static file content is recorded.
    Whether Google has actually obeyed the rules cannot be verified here.
    """

    is_accessible: bool
    # True if the fetch returned HTTP 200; False for 404, timeout, or error

    http_status: int
    # The HTTP status code returned when fetching /robots.txt (0 if not fetched)

    disallow_rules: list[str]
    # All Disallow: values for the * (all robots) user-agent block

    allow_rules: list[str]
    # All Allow: values for the * (all robots) user-agent block

    sitemap_urls: list[str]
    # All Sitemap: directives found in robots.txt

    blocks_root_path: bool
    # True if Disallow: / or Disallow: /* appears in the * user-agent block
    # This would block Googlebot from crawling the entire site — a critical finding


@dataclass
class SitemapEvidence:
    """
    Verified accessibility status of one sitemap file.

    Only HTTP status and basic URL count are verified in the MVP.
    Full sitemap validation (canonical URLs, changefreq accuracy, etc.)
    requires a crawler and is out of scope.
    """

    url: str
    # The full URL of the sitemap that was fetched

    is_accessible: bool
    # True if the fetch returned HTTP 200

    http_status: int
    # HTTP status code returned (0 if not fetched)

    url_count: int
    # Number of <loc> elements found in the sitemap XML (0 if not accessible or not XML)


@dataclass
class AuditEvidence:
    """
    All verified SEO data extracted from one website audit.

    This is the complete structured input the report_service passes to the LLM.
    Every field is populated — fields that could not be determined contain
    None, an empty list, or the UNVERIFIABLE constant.
    """

    # --- Audit metadata -----------------------------------------------------

    base_url: str
    # The normalised URL that was audited (from url_service)

    final_url: str
    # The actual URL after redirects — may differ from base_url

    # --- HTTP / HTTPS -------------------------------------------------------

    http_status: int
    # HTTP response code for the homepage (200, 301, 404, 500, etc.)

    is_https: bool
    # True if the final URL uses the https:// scheme

    # --- Page metadata -------------------------------------------------------

    page_title: str | None
    # Text content of the <title> tag; None if missing

    page_title_length: int
    # Character count of page_title (0 if missing)

    meta_description: str | None
    # Content attribute of <meta name="description">; None if missing

    meta_description_length: int
    # Character count of meta_description (0 if missing)

    canonical_url: str | None
    # href attribute of <link rel="canonical">; None if missing

    page_language: str | None
    # lang attribute on the <html> element; None if missing

    # --- Heading structure --------------------------------------------------

    h1_tags: list[str]
    # Text content of every <h1> element on the page

    h2_tags: list[str]
    # Text content of every <h2> element on the page

    # --- Links --------------------------------------------------------------

    internal_links: list[str]
    # Unique absolute URLs that share the same domain as the audited page

    external_links: list[str]
    # Unique absolute URLs that point to a different domain

    # --- Images -------------------------------------------------------------

    images: list[ImageInfo]
    # Every <img> element found on the page

    images_missing_alt_count: int
    # Number of <img> elements with no alt attribute at all

    images_empty_alt_count: int
    # Number of <img> elements with alt="" (blank alt — may be intentional for decorative images)

    # --- Technical SEO -------------------------------------------------------

    robots_txt: RobotsTxtEvidence | None
    # Parsed robots.txt evidence; None if fetch was not attempted

    sitemaps: list[SitemapEvidence]
    # One SitemapEvidence per sitemap URL that was fetched

    # --- Unverifiable fields notice -----------------------------------------

    unverifiable_fields: list[str]
    # Human-readable list of fields that cannot be verified from static content
    # This is passed directly to the LLM as context for what to mark as unverifiable


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def extract(site: SiteFetchResult) -> AuditEvidence:
    """
    Extract all verified SEO data from a SiteFetchResult.

    Parses HTML with BeautifulSoup, parses robots.txt with regex,
    and counts <loc> elements in sitemaps.  No network calls are made —
    this function operates entirely on already-fetched content.

    Args:
        site: The SiteFetchResult produced by fetch_service.fetch_site().

    Returns:
        AuditEvidence containing every extractable SEO field.
    """
    logger.info("Starting SEO data extraction for: %s", site.base_url)

    # --- Parse the homepage HTML -------------------------------------------

    if site.homepage.is_success and site.homepage.content:
        soup = BeautifulSoup(site.homepage.content, _PARSER)
        # BeautifulSoup builds a parse tree from the HTML string
        # _PARSER="lxml" is faster and more lenient than Python's built-in html.parser
    else:
        soup = BeautifulSoup("", _PARSER)  # Empty soup when homepage could not be fetched
        logger.warning("Homepage not available for extraction: %s", site.base_url)

    # --- Extract individual fields ------------------------------------------

    page_title: str | None = _extract_title(soup)
    meta_description: str | None = _extract_meta_description(soup)
    canonical_url: str | None = _extract_canonical(soup)
    page_language: str | None = _extract_language(soup)
    h1_tags: list[str] = _extract_headings(soup, level=1)
    h2_tags: list[str] = _extract_headings(soup, level=2)
    internal_links, external_links = _extract_links(soup, site.base_url)
    images: list[ImageInfo] = _extract_images(soup, site.base_url)

    # --- Compute derived counts ---------------------------------------------

    images_missing_alt: int = sum(1 for img in images if not img.has_alt_attribute)
    # Count images that have no alt attribute at all (worst case — definite accessibility issue)

    images_empty_alt: int = sum(
        1 for img in images if img.has_alt_attribute and img.alt == ""
    )
    # Count images with alt="" (may be intentional for purely decorative images)

    # --- Parse robots.txt ---------------------------------------------------

    robots_evidence: RobotsTxtEvidence | None = _parse_robots_txt(site)

    # --- Parse sitemaps -----------------------------------------------------

    sitemap_evidences: list[SitemapEvidence] = _parse_sitemaps(site)

    # --- Build unverifiable fields list ------------------------------------

    unverifiable: list[str] = _build_unverifiable_list()
    # A fixed list of fields that cannot be determined from static content

    # --- Log extraction summary ---------------------------------------------

    logger.info(
        "Extraction complete for %s: title=%r, h1_count=%d, h2_count=%d, "
        "internal_links=%d, external_links=%d, images=%d",
        site.base_url,
        page_title,
        len(h1_tags),
        len(h2_tags),
        len(internal_links),
        len(external_links),
        len(images),
    )

    return AuditEvidence(
        base_url=site.base_url,
        final_url=site.homepage.final_url or site.base_url,
        # Use the final URL after redirects; fall back to base_url if not available

        http_status=site.homepage.status_code,
        is_https=site.homepage.final_url.startswith("https://") if site.homepage.final_url else site.base_url.startswith("https://"),
        # Check if the final URL (after redirects) uses HTTPS

        page_title=page_title,
        page_title_length=len(page_title) if page_title else 0,

        meta_description=meta_description,
        meta_description_length=len(meta_description) if meta_description else 0,

        canonical_url=canonical_url,
        page_language=page_language,

        h1_tags=h1_tags,
        h2_tags=h2_tags,

        internal_links=internal_links,
        external_links=external_links,

        images=images,
        images_missing_alt_count=images_missing_alt,
        images_empty_alt_count=images_empty_alt,

        robots_txt=robots_evidence,
        sitemaps=sitemap_evidences,

        unverifiable_fields=unverifiable,
    )


# ---------------------------------------------------------------------------
# Private extraction helpers — HTML
# ---------------------------------------------------------------------------

def _extract_title(soup: BeautifulSoup) -> str | None:
    """Extract the text content of the <title> element."""
    tag = soup.find("title")  # Find the first <title> tag in the document
    if not tag:
        return None  # No <title> element — missing title is itself an SEO finding
    text = tag.get_text(strip=True)  # strip=True removes leading/trailing whitespace
    return text if text else None  # Return None for an empty <title></title>


def _extract_meta_description(soup: BeautifulSoup) -> str | None:
    """Extract the content attribute from <meta name='description'>."""
    tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.IGNORECASE)})
    # Case-insensitive match catches <meta name="Description"> and <meta name="DESCRIPTION">
    if not tag:
        return None  # Missing meta description — an SEO finding
    content: str | None = tag.get("content")  # type: ignore[assignment]
    if not content:
        return None
    content = content.strip()
    return content if content else None  # Return None for an empty content attribute


def _extract_canonical(soup: BeautifulSoup) -> str | None:
    """Extract the href attribute from <link rel='canonical'>."""
    tag = soup.find("link", attrs={"rel": re.compile(r"canonical", re.IGNORECASE)})
    # BeautifulSoup stores rel as a list for some elements — re.compile handles both forms
    if not tag:
        return None  # No canonical tag — the page may have duplicate content issues
    href: str | None = tag.get("href")  # type: ignore[assignment]
    if not href:
        return None
    return href.strip() or None  # Return None for an empty href=""


def _extract_language(soup: BeautifulSoup) -> str | None:
    """Extract the lang attribute from the <html> element."""
    html_tag = soup.find("html")  # The root <html> element
    if not html_tag:
        return None
    lang: str | None = html_tag.get("lang")  # type: ignore[assignment]
    if not lang:
        return None
    return lang.strip() or None  # Return None for lang=""


def _extract_headings(soup: BeautifulSoup, level: int) -> list[str]:
    """
    Extract text content of all heading elements at the given level.

    Args:
        soup: Parsed BeautifulSoup document.
        level: Heading level to extract (1 for H1, 2 for H2, etc.).

    Returns:
        List of non-empty heading text strings.
    """
    tags = soup.find_all(f"h{level}")  # Find all <h1>, <h2>, etc. elements
    texts: list[str] = []
    for tag in tags:
        text: str = tag.get_text(separator=" ", strip=True)
        # separator=" " joins inline child elements with a space rather than running words together
        if text:
            texts.append(text)  # Only add non-empty headings
    return texts


def _extract_links(
    soup: BeautifulSoup,
    base_url: str,
) -> tuple[list[str], list[str]]:
    """
    Extract and classify all <a href> links as internal or external.

    Internal links share the same registered domain as base_url.
    External links point to a different domain.

    Returns:
        Tuple of (internal_links, external_links) — each a deduplicated list
        of absolute URL strings, capped at _MAX_LINKS each.
    """
    base_domain: str = urlparse(base_url).netloc.lower()
    # Extract just the domain portion (e.g. "www.example.com") for comparison

    internal: set[str] = set()  # Use sets for automatic deduplication
    external: set[str] = set()

    for tag in soup.find_all("a", href=True):
        raw_href: str = tag["href"].strip()  # Raw href value from the tag

        if not raw_href or raw_href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue  # Skip anchors, mailto links, phone links, and JavaScript pseudo-links

        # Resolve relative URLs to absolute using the page's base URL
        absolute: str = urljoin(base_url, raw_href)

        parsed = urlparse(absolute)  # Parse the absolute URL to extract its domain

        if parsed.scheme not in ("http", "https"):
            continue  # Skip non-web links (ftp://, data:, etc.)

        link_domain: str = parsed.netloc.lower()  # Domain of the linked URL

        if link_domain == base_domain or link_domain.endswith(f".{base_domain}"):
            # Subdomains of the base domain are also considered internal
            if len(internal) < _MAX_LINKS:
                internal.add(absolute)
        else:
            if len(external) < _MAX_LINKS:
                external.add(absolute)

    return sorted(internal), sorted(external)
    # sorted() gives a consistent order for deterministic tests and reports


def _extract_images(soup: BeautifulSoup, base_url: str) -> list[ImageInfo]:
    """
    Extract all <img> elements and their alt text.

    Only images with an src attribute are included.
    The src is resolved to an absolute URL.

    Args:
        soup: Parsed BeautifulSoup document.
        base_url: Used to resolve relative image URLs.

    Returns:
        List of ImageInfo objects for every <img> found.
    """
    images: list[ImageInfo] = []

    for tag in soup.find_all("img"):
        raw_src: str | None = tag.get("src")  # Raw src attribute value

        if not raw_src or not raw_src.strip():
            continue  # Skip images with no src

        absolute_src: str = urljoin(base_url, raw_src.strip())
        # Resolve relative paths like /images/logo.png to https://example.com/images/logo.png

        has_alt: bool = tag.has_attr("alt")  # True if the alt attribute exists at all
        alt_value: str = tag.get("alt", "").strip()  # "" if absent or blank

        images.append(ImageInfo(
            src=absolute_src,
            alt=alt_value,
            has_alt_attribute=has_alt,
        ))

    return images


# ---------------------------------------------------------------------------
# Private extraction helpers — robots.txt
# ---------------------------------------------------------------------------

def _parse_robots_txt(site: SiteFetchResult) -> RobotsTxtEvidence | None:
    """
    Parse the robots.txt fetch result into structured evidence.

    Extracts Disallow and Allow rules for the wildcard (*) user-agent,
    Sitemap directives, and detects whether the root path is blocked.

    Args:
        site: The SiteFetchResult; site.robots_txt contains the fetched content.

    Returns:
        RobotsTxtEvidence, or None if robots.txt was not fetched.
    """
    r = site.robots_txt  # Shorthand reference to the robots.txt FetchedResource

    if not r.is_fetched:
        return None  # robots.txt was not part of this audit — should not happen in MVP

    if not r.is_success or not r.content:
        # robots.txt returned 404 or an error — record the status but no rules
        return RobotsTxtEvidence(
            is_accessible=False,
            http_status=r.status_code,
            disallow_rules=[],
            allow_rules=[],
            sitemap_urls=[],
            blocks_root_path=False,
        )

    disallow_rules: list[str] = []  # All Disallow: values for user-agent: *
    allow_rules: list[str] = []     # All Allow: values for user-agent: *
    sitemap_urls: list[str] = []    # All Sitemap: directive values

    in_wildcard_block: bool = False  # Track when we are inside a User-agent: * block

    for raw_line in r.content.splitlines():
        line = raw_line.strip()  # Remove leading/trailing whitespace

        if not line or line.startswith("#"):
            continue  # Skip blank lines and comment lines

        lower_line: str = line.lower()

        if lower_line.startswith("user-agent:"):
            # Start of a new user-agent block
            agent_value: str = line.split(":", 1)[1].strip()
            in_wildcard_block = (agent_value == "*")
            # Set True when the block applies to all robots (*)
            continue

        if lower_line.startswith("sitemap:"):
            # Sitemap: directives apply globally, not per user-agent
            sitemap_value: str = line.split(":", 1)[1].strip()
            if sitemap_value:
                sitemap_urls.append(sitemap_value)
            continue

        if in_wildcard_block:
            # Only record Disallow/Allow rules when inside the wildcard user-agent block
            if lower_line.startswith("disallow:"):
                value: str = line.split(":", 1)[1].strip()
                if value:  # Empty Disallow: means "allow everything" — skip
                    disallow_rules.append(value)

            elif lower_line.startswith("allow:"):
                value = line.split(":", 1)[1].strip()
                if value:
                    allow_rules.append(value)

    # Detect if the root path is blocked for all robots
    blocks_root: bool = any(
        rule in ("/", "/*")  # Both forms block the entire site
        for rule in disallow_rules
    )

    return RobotsTxtEvidence(
        is_accessible=True,
        http_status=r.status_code,
        disallow_rules=disallow_rules,
        allow_rules=allow_rules,
        sitemap_urls=sitemap_urls,
        blocks_root_path=blocks_root,
    )


# ---------------------------------------------------------------------------
# Private extraction helpers — sitemaps
# ---------------------------------------------------------------------------

def _parse_sitemaps(site: SiteFetchResult) -> list[SitemapEvidence]:
    """
    Build a SitemapEvidence record for each fetched sitemap.

    Counts the number of <loc> elements in each accessible sitemap XML file.
    Does not validate the <loc> URLs themselves — that is out of scope for the MVP.

    Args:
        site: The SiteFetchResult containing all sitemap fetch results.

    Returns:
        List of SitemapEvidence, one per sitemap that was attempted.
    """
    evidences: list[SitemapEvidence] = []

    for sitemap_resource in site.all_sitemaps:
        url_count: int = 0  # Start at 0; only increment if we can parse the XML

        if sitemap_resource.is_success and sitemap_resource.content:
            try:
                # Use BeautifulSoup to count <loc> tags in the sitemap XML
                sitemap_soup = BeautifulSoup(sitemap_resource.content, "xml")
                # "xml" parser is stricter and correct for sitemap XML files
                url_count = len(sitemap_soup.find_all("loc"))
                # Each <loc> element represents one URL entry in the sitemap
            except Exception as parse_error:
                logger.warning(
                    "Could not parse sitemap XML at %s: %s",
                    sitemap_resource.url,
                    parse_error,
                )
                # url_count stays 0; the sitemap was accessible but not valid XML

        evidences.append(SitemapEvidence(
            url=sitemap_resource.url,
            is_accessible=sitemap_resource.is_success,
            http_status=sitemap_resource.status_code,
            url_count=url_count,
        ))

    return evidences


# ---------------------------------------------------------------------------
# Unverifiable fields catalogue
# ---------------------------------------------------------------------------

def _build_unverifiable_list() -> list[str]:
    """
    Return the fixed list of SEO fields that cannot be verified
    from static HTML content in the MVP.

    This list is passed to the LLM so it knows which sections to
    mark with the standard unverifiable phrase rather than guessing.
    """
    return [
        "Core Web Vitals (LCP, INP, CLS) — requires Lighthouse or PageSpeed Insights API",
        "Mobile-friendliness score — requires browser rendering",
        "Page speed / TTFB — requires a live performance test",
        "Structured data / JSON-LD schema — may be injected by JavaScript and is invisible in static HTML",
        "Google Search Console data — requires API access",
        "Keyword rankings — requires a rank-tracking tool",
        "Backlinks — requires a link index database (Ahrefs, Semrush, etc.)",
        "Competitor analysis — requires external data sources",
        "Full broken-link crawl — only the homepage links are visible in this audit",
        "HTTP response headers (HSTS, X-Robots-Tag, Cache-Control) — not accessible via static fetch",
        "Hreflang implementation — requires crawling multiple locale pages",
    ]
