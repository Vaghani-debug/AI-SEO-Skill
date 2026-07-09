"""
test/test_extractor_service.py

Unit tests for src/services/extractor_service.py.

Uses static HTML and robots.txt fixtures — no network calls.
Each test class exercises one extraction category.

Run with:
    pytest test/test_extractor_service.py -v
"""

import pytest  # pytest: test runner

from src.services.extractor_service import (
    AuditEvidence,
    ImageInfo,
    RobotsTxtEvidence,
    SitemapEvidence,
    extract,
)
from src.services.fetch_service import FetchedResource, SiteFetchResult


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_site(
    homepage_html: str = "<html><head></head><body></body></html>",
    homepage_status: int = 200,
    robots_content: str = "",
    robots_status: int = 200,
    sitemap_content: str = "",
    sitemap_status: int = 404,
    base_url: str = "https://example.com",
) -> SiteFetchResult:
    """Build a SiteFetchResult from simple string arguments for test convenience."""

    def _resource(url: str, label: str, content: str, status: int) -> FetchedResource:
        return FetchedResource(
            url=url,
            label=label,
            final_url=url,
            status_code=status,
            content=content,
            is_success=200 <= status < 300,
            is_fetched=True,
        )

    return SiteFetchResult(
        base_url=base_url,
        homepage=_resource(base_url, "homepage", homepage_html, homepage_status),
        robots_txt=_resource(f"{base_url}/robots.txt", "robots.txt", robots_content, robots_status),
        sitemap_xml=_resource(f"{base_url}/sitemap.xml", "sitemap.xml", sitemap_content, sitemap_status),
    )


# ---------------------------------------------------------------------------
# AuditEvidence structure tests
# ---------------------------------------------------------------------------

class TestExtractReturnsCorrectType:
    """Verify that extract() always returns a properly typed AuditEvidence."""

    def test_returns_audit_evidence(self) -> None:
        """extract() returns an AuditEvidence instance."""
        site = _make_site()
        result = extract(site)
        assert isinstance(result, AuditEvidence)

    def test_base_url_preserved(self) -> None:
        """base_url in the result matches the input URL."""
        site = _make_site(base_url="https://example.com")
        result = extract(site)
        assert result.base_url == "https://example.com"

    def test_http_status_recorded(self) -> None:
        """HTTP status code from the homepage is stored correctly."""
        site = _make_site(homepage_status=200)
        result = extract(site)
        assert result.http_status == 200

    def test_404_homepage_status_recorded(self) -> None:
        """A 404 homepage status is preserved in the evidence."""
        site = _make_site(homepage_status=404, homepage_html="")
        result = extract(site)
        assert result.http_status == 404

    def test_https_detection(self) -> None:
        """is_https is True for https:// URLs."""
        site = _make_site(base_url="https://secure.example.com")
        result = extract(site)
        assert result.is_https is True

    def test_http_not_https(self) -> None:
        """is_https is False for http:// URLs."""
        site = _make_site(base_url="http://insecure.example.com")
        result = extract(site)
        assert result.is_https is False


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------

class TestTitleExtraction:

    def test_extracts_title(self) -> None:
        """Extracts the text content of a <title> tag."""
        html = "<html><head><title>Best IT Company in Surat</title></head><body/></html>"
        result = extract(_make_site(homepage_html=html))
        assert result.page_title == "Best IT Company in Surat"

    def test_title_length_correct(self) -> None:
        """page_title_length matches the actual character count."""
        html = "<html><head><title>Hello</title></head><body/></html>"
        result = extract(_make_site(homepage_html=html))
        assert result.page_title_length == 5

    def test_missing_title_returns_none(self) -> None:
        """Returns None when no <title> element is present."""
        html = "<html><head></head><body/></html>"
        result = extract(_make_site(homepage_html=html))
        assert result.page_title is None
        assert result.page_title_length == 0

    def test_empty_title_returns_none(self) -> None:
        """Returns None for an empty <title></title>."""
        html = "<html><head><title>  </title></head><body/></html>"
        result = extract(_make_site(homepage_html=html))
        assert result.page_title is None

    def test_title_whitespace_stripped(self) -> None:
        """Leading and trailing whitespace is stripped from the title."""
        html = "<html><head><title>  My Site  </title></head><body/></html>"
        result = extract(_make_site(homepage_html=html))
        assert result.page_title == "My Site"


# ---------------------------------------------------------------------------
# Meta description extraction
# ---------------------------------------------------------------------------

class TestMetaDescriptionExtraction:

    def test_extracts_meta_description(self) -> None:
        """Extracts the content attribute of <meta name='description'>."""
        html = '<html><head><meta name="description" content="We build websites."/></head><body/></html>'
        result = extract(_make_site(homepage_html=html))
        assert result.meta_description == "We build websites."

    def test_missing_meta_description_returns_none(self) -> None:
        """Returns None when no meta description is present."""
        html = "<html><head></head><body/></html>"
        result = extract(_make_site(homepage_html=html))
        assert result.meta_description is None
        assert result.meta_description_length == 0

    def test_meta_description_length(self) -> None:
        """meta_description_length matches the content character count."""
        description = "A" * 155  # Ideal meta description length
        html = f'<html><head><meta name="description" content="{description}"/></head><body/></html>'
        result = extract(_make_site(homepage_html=html))
        assert result.meta_description_length == 155

    def test_case_insensitive_meta_name(self) -> None:
        """<meta name='Description'> (capitalised) is also matched."""
        html = '<html><head><meta name="Description" content="Uppercase name."/></head><body/></html>'
        result = extract(_make_site(homepage_html=html))
        assert result.meta_description == "Uppercase name."


# ---------------------------------------------------------------------------
# Canonical URL extraction
# ---------------------------------------------------------------------------

class TestCanonicalExtraction:

    def test_extracts_canonical_url(self) -> None:
        """Extracts href from <link rel='canonical'>."""
        html = '<html><head><link rel="canonical" href="https://example.com/page"/></head><body/></html>'
        result = extract(_make_site(homepage_html=html))
        assert result.canonical_url == "https://example.com/page"

    def test_missing_canonical_returns_none(self) -> None:
        """Returns None when no canonical link is present."""
        html = "<html><head></head><body/></html>"
        result = extract(_make_site(homepage_html=html))
        assert result.canonical_url is None


# ---------------------------------------------------------------------------
# Language extraction
# ---------------------------------------------------------------------------

class TestLanguageExtraction:

    def test_extracts_lang_attribute(self) -> None:
        """Extracts the lang attribute from the <html> element."""
        html = '<html lang="en"><head></head><body/></html>'
        result = extract(_make_site(homepage_html=html))
        assert result.page_language == "en"

    def test_missing_lang_returns_none(self) -> None:
        """Returns None when the <html> element has no lang attribute."""
        html = "<html><head></head><body/></html>"
        result = extract(_make_site(homepage_html=html))
        assert result.page_language is None


# ---------------------------------------------------------------------------
# Heading extraction
# ---------------------------------------------------------------------------

class TestHeadingExtraction:

    def test_extracts_single_h1(self) -> None:
        """Extracts one H1 heading."""
        html = "<html><body><h1>Main Heading</h1></body></html>"
        result = extract(_make_site(homepage_html=html))
        assert result.h1_tags == ["Main Heading"]

    def test_extracts_multiple_h2(self) -> None:
        """Extracts multiple H2 headings."""
        html = "<html><body><h2>Section A</h2><h2>Section B</h2></body></html>"
        result = extract(_make_site(homepage_html=html))
        assert "Section A" in result.h2_tags
        assert "Section B" in result.h2_tags
        assert len(result.h2_tags) == 2

    def test_no_headings_returns_empty_list(self) -> None:
        """Returns empty lists when no H1 or H2 headings are present."""
        html = "<html><body><p>No headings here.</p></body></html>"
        result = extract(_make_site(homepage_html=html))
        assert result.h1_tags == []
        assert result.h2_tags == []

    def test_multiple_h1_all_extracted(self) -> None:
        """Multiple H1 tags are all captured (detecting multiple H1s is itself a finding)."""
        html = "<html><body><h1>First</h1><h1>Second</h1></body></html>"
        result = extract(_make_site(homepage_html=html))
        assert len(result.h1_tags) == 2


# ---------------------------------------------------------------------------
# Link extraction
# ---------------------------------------------------------------------------

class TestLinkExtraction:

    def test_internal_link_classified(self) -> None:
        """Links to the same domain are classified as internal."""
        html = '<html><body><a href="/about">About</a></body></html>'
        result = extract(_make_site(homepage_html=html, base_url="https://example.com"))
        assert any("example.com" in link for link in result.internal_links)

    def test_external_link_classified(self) -> None:
        """Links to a different domain are classified as external."""
        html = '<html><body><a href="https://google.com">Google</a></body></html>'
        result = extract(_make_site(homepage_html=html, base_url="https://example.com"))
        assert any("google.com" in link for link in result.external_links)

    def test_anchor_links_excluded(self) -> None:
        """Pure anchor links (#section) are not included in any list."""
        html = '<html><body><a href="#top">Back to top</a></body></html>'
        result = extract(_make_site(homepage_html=html))
        assert not any("#top" in link for link in result.internal_links + result.external_links)

    def test_mailto_links_excluded(self) -> None:
        """mailto: links are not included."""
        html = '<html><body><a href="mailto:info@example.com">Email</a></body></html>'
        result = extract(_make_site(homepage_html=html))
        assert not any("mailto" in link for link in result.internal_links + result.external_links)

    def test_no_duplicate_links(self) -> None:
        """Duplicate href values appear only once in the output."""
        html = (
            '<html><body>'
            '<a href="/page">Link 1</a>'
            '<a href="/page">Link 2</a>'  # Same href as Link 1
            '</body></html>'
        )
        result = extract(_make_site(homepage_html=html, base_url="https://example.com"))
        matching = [l for l in result.internal_links if l.endswith("/page")]
        assert len(matching) == 1  # De-duplicated to one entry


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

class TestImageExtraction:

    def test_extracts_image_with_alt(self) -> None:
        """Image with a descriptive alt attribute is extracted correctly."""
        html = '<html><body><img src="/logo.png" alt="Company logo"/></body></html>'
        result = extract(_make_site(homepage_html=html, base_url="https://example.com"))
        assert len(result.images) == 1
        assert result.images[0].has_alt_attribute is True
        assert result.images[0].alt == "Company logo"

    def test_counts_images_missing_alt(self) -> None:
        """images_missing_alt_count correctly counts images with no alt attribute."""
        html = (
            '<html><body>'
            '<img src="/a.jpg"/>'           # No alt attribute — missing
            '<img src="/b.jpg" alt=""/>'    # Empty alt — present but blank
            '<img src="/c.jpg" alt="OK"/>'  # Good alt
            '</body></html>'
        )
        result = extract(_make_site(homepage_html=html))
        assert result.images_missing_alt_count == 1   # Only /a.jpg has no alt attribute

    def test_counts_images_empty_alt(self) -> None:
        """images_empty_alt_count correctly counts images with alt=''."""
        html = (
            '<html><body>'
            '<img src="/a.jpg"/>'
            '<img src="/b.jpg" alt=""/>'
            '</body></html>'
        )
        result = extract(_make_site(homepage_html=html))
        assert result.images_empty_alt_count == 1  # Only /b.jpg has alt=""

    def test_relative_image_src_resolved(self) -> None:
        """Relative image src values are resolved to absolute URLs."""
        html = '<html><body><img src="/images/photo.jpg" alt="Photo"/></body></html>'
        result = extract(_make_site(homepage_html=html, base_url="https://example.com"))
        assert result.images[0].src == "https://example.com/images/photo.jpg"


# ---------------------------------------------------------------------------
# robots.txt parsing
# ---------------------------------------------------------------------------

class TestRobotsTxtParsing:

    def test_accessible_robots_txt(self) -> None:
        """A 200 robots.txt is marked as accessible."""
        site = _make_site(robots_content="User-agent: *\nDisallow:\n", robots_status=200)
        result = extract(site)
        assert result.robots_txt is not None
        assert result.robots_txt.is_accessible is True

    def test_inaccessible_robots_txt(self) -> None:
        """A 404 robots.txt is recorded as inaccessible."""
        site = _make_site(robots_content="", robots_status=404)
        result = extract(site)
        assert result.robots_txt is not None
        assert result.robots_txt.is_accessible is False

    def test_disallow_rules_extracted(self) -> None:
        """Disallow: values for user-agent: * are collected."""
        robots = "User-agent: *\nDisallow: /admin\nDisallow: /checkout\n"
        result = extract(_make_site(robots_content=robots, robots_status=200))
        assert "/admin" in result.robots_txt.disallow_rules
        assert "/checkout" in result.robots_txt.disallow_rules

    def test_blocks_root_path_detected(self) -> None:
        """blocks_root_path is True when Disallow: / is found."""
        robots = "User-agent: *\nDisallow: /\n"
        result = extract(_make_site(robots_content=robots, robots_status=200))
        assert result.robots_txt.blocks_root_path is True

    def test_blocks_root_path_false_for_partial_disallow(self) -> None:
        """blocks_root_path is False when only subdirectories are blocked."""
        robots = "User-agent: *\nDisallow: /admin\n"
        result = extract(_make_site(robots_content=robots, robots_status=200))
        assert result.robots_txt.blocks_root_path is False

    def test_sitemap_urls_extracted_from_robots(self) -> None:
        """Sitemap: lines in robots.txt are recorded in the evidence."""
        robots = "User-agent: *\nSitemap: https://example.com/sitemap.xml\n"
        result = extract(_make_site(robots_content=robots, robots_status=200))
        assert "https://example.com/sitemap.xml" in result.robots_txt.sitemap_urls

    def test_comments_and_blank_lines_skipped(self) -> None:
        """Comment lines and blank lines in robots.txt do not break parsing."""
        robots = "# Main robots.txt\n\nUser-agent: *\nDisallow: /secret\n"
        result = extract(_make_site(robots_content=robots, robots_status=200))
        assert "/secret" in result.robots_txt.disallow_rules


# ---------------------------------------------------------------------------
# Sitemap evidence
# ---------------------------------------------------------------------------

class TestSitemapEvidence:

    def test_accessible_sitemap_recorded(self) -> None:
        """A 200 sitemap.xml is recorded as accessible."""
        sitemap_xml = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/</loc></url></urlset>'
        site = _make_site(sitemap_content=sitemap_xml, sitemap_status=200)
        result = extract(site)
        assert len(result.sitemaps) >= 1
        sitemap = next((s for s in result.sitemaps if "sitemap.xml" in s.url), None)
        assert sitemap is not None
        assert sitemap.is_accessible is True

    def test_sitemap_url_count(self) -> None:
        """url_count reflects the number of <loc> elements in the sitemap."""
        sitemap_xml = (
            '<?xml version="1.0"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            '<url><loc>https://example.com/</loc></url>'
            '<url><loc>https://example.com/about</loc></url>'
            '<url><loc>https://example.com/contact</loc></url>'
            '</urlset>'
        )
        site = _make_site(sitemap_content=sitemap_xml, sitemap_status=200)
        result = extract(site)
        sitemap = next((s for s in result.sitemaps if "sitemap.xml" in s.url), None)
        assert sitemap is not None
        assert sitemap.url_count == 3

    def test_missing_sitemap_recorded(self) -> None:
        """A 404 sitemap.xml is recorded with is_accessible=False."""
        site = _make_site(sitemap_content="", sitemap_status=404)
        result = extract(site)
        sitemap = next((s for s in result.sitemaps if "sitemap.xml" in s.url), None)
        assert sitemap is not None
        assert sitemap.is_accessible is False
        assert sitemap.url_count == 0


# ---------------------------------------------------------------------------
# Unverifiable fields
# ---------------------------------------------------------------------------

class TestUnverifiableFields:

    def test_unverifiable_fields_populated(self) -> None:
        """extract() always populates the unverifiable_fields list."""
        result = extract(_make_site())
        assert len(result.unverifiable_fields) > 0

    def test_core_web_vitals_listed_as_unverifiable(self) -> None:
        """Core Web Vitals are explicitly listed as unverifiable."""
        result = extract(_make_site())
        assert any("Core Web Vitals" in f for f in result.unverifiable_fields)

    def test_schema_markup_listed_as_unverifiable(self) -> None:
        """Schema / structured data is listed as unverifiable."""
        result = extract(_make_site())
        assert any("schema" in f.lower() or "structured data" in f.lower() for f in result.unverifiable_fields)
