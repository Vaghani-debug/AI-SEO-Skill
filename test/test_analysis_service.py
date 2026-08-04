"""
test/test_analysis_service.py

Unit tests for src/services/analysis_service.py.

Uses hand-built SiteEvidence/PageEvidence fixtures — no network calls,
no LLM calls. Each test class exercises one category of deterministic
checks and verifies both the emitted Finding(s) and the resulting
category/overall scores.

Run with:
    pytest test/test_analysis_service.py -v
"""

from src.services.analysis_service import analyze_site
from src.services.audit_models import (
    ImageInfo,
    PageEvidence,
    PageType,
    RobotsTxtEvidence,
    SitemapEvidence,
    SiteEvidence,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_page(
    url: str = "https://example.com",
    page_type: PageType = PageType.CORE,
    http_status: int = 200,
    is_https: bool = True,
    used_playwright_fallback: bool = False,
    page_title: str | None = "A Good Page Title Here",
    meta_description: str | None = "A meta description of reasonable length for testing purposes here.",
    canonical_url: str | None = "https://example.com",
    page_language: str | None = "en",
    meta_robots: str | None = None,
    h1_tags: list[str] | None = None,
    word_count: int = 400,
    schema_types: list[str] | None = None,
    internal_links: list[str] | None = None,
    images: list[ImageInfo] | None = None,
) -> PageEvidence:
    return PageEvidence(
        url=url,
        page_type=page_type,
        http_status=http_status,
        is_https=is_https,
        used_playwright_fallback=used_playwright_fallback,
        page_title=page_title,
        meta_description=meta_description,
        canonical_url=canonical_url,
        page_language=page_language,
        meta_robots=meta_robots,
        h1_tags=h1_tags if h1_tags is not None else ["A Heading"],
        word_count=word_count,
        schema_types=schema_types if schema_types is not None else ["Organization"],
        internal_links=internal_links if internal_links is not None else ["https://example.com/about"],
        images=images if images is not None else [],
    )


_UNSET = object()
# Sentinel so an explicit robots_txt=None (meaning "no robots.txt evidence")
# can be distinguished from "caller didn't specify, use the default fixture".


def _make_site(
    homepage: PageEvidence | None = None,
    sampled_pages: list[PageEvidence] | None = None,
    robots_txt: RobotsTxtEvidence | None = _UNSET,  # type: ignore[assignment]
    sitemaps: list[SitemapEvidence] | None = None,
) -> SiteEvidence:
    return SiteEvidence(
        base_url="https://example.com",
        final_url="https://example.com",
        homepage=homepage if homepage is not None else _make_page(),
        sampled_pages=sampled_pages if sampled_pages is not None else [],
        robots_txt=robots_txt if robots_txt is not _UNSET else RobotsTxtEvidence(
            is_accessible=True,
            http_status=200,
            disallow_rules=[],
            allow_rules=[],
            sitemap_urls=["https://example.com/sitemap.xml"],
            blocks_root_path=False,
        ),
        sitemaps=sitemaps if sitemaps is not None else [
            SitemapEvidence(url="https://example.com/sitemap.xml", is_accessible=True, http_status=200, url_count=5)
        ],
    )


# ---------------------------------------------------------------------------
# Clean site — baseline / no findings
# ---------------------------------------------------------------------------

class TestAnalyzeSiteCleanBaseline:

    def test_clean_site_has_no_findings(self) -> None:
        result = analyze_site(_make_site())
        assert result.findings == []

    def test_clean_site_scores_100_overall(self) -> None:
        result = analyze_site(_make_site())
        assert result.overall_score == 100.0

    def test_all_six_categories_present(self) -> None:
        result = analyze_site(_make_site())
        categories = {cs.category for cs in result.category_scores}
        assert categories == {
            "Technical SEO", "On-Page SEO", "Content Quality",
            "Performance", "Accessibility", "Security",
        }

    def test_category_weights_sum_to_100(self) -> None:
        result = analyze_site(_make_site())
        assert sum(cs.weight_percent for cs in result.category_scores) == 100.0

    def test_performance_always_scores_100_in_mvp(self) -> None:
        result = analyze_site(_make_site())
        performance = next(cs for cs in result.category_scores if cs.category == "Performance")
        assert performance.score == 100.0


# ---------------------------------------------------------------------------
# Technical SEO checks
# ---------------------------------------------------------------------------

class TestTechnicalSeoChecks:

    def test_non_https_homepage_flagged_critical(self) -> None:
        site = _make_site(homepage=_make_page(is_https=False))
        result = analyze_site(site)
        finding = next(f for f in result.findings if "HTTPS" in f.title)
        assert finding.category == "Technical SEO"
        assert finding.severity.value == "Critical"

    def test_non_200_homepage_status_flagged(self) -> None:
        site = _make_site(homepage=_make_page(http_status=500))
        result = analyze_site(site)
        assert any("HTTP 500" in f.title for f in result.findings)

    def test_missing_robots_txt_flagged(self) -> None:
        site = _make_site(robots_txt=None)
        result = analyze_site(site)
        assert any("robots.txt is missing" in f.title for f in result.findings)

    def test_robots_txt_blocking_root_flagged_critical(self) -> None:
        blocking_robots = RobotsTxtEvidence(
            is_accessible=True, http_status=200, disallow_rules=["/"],
            allow_rules=[], sitemap_urls=[], blocks_root_path=True,
        )
        site = _make_site(robots_txt=blocking_robots)
        result = analyze_site(site)
        finding = next(f for f in result.findings if "blocks the entire site" in f.title)
        assert finding.severity.value == "Critical"
        assert finding.score_deduction == 50.0

    def test_no_accessible_sitemap_flagged(self) -> None:
        site = _make_site(sitemaps=[
            SitemapEvidence(url="https://example.com/sitemap.xml", is_accessible=False, http_status=404, url_count=0)
        ])
        result = analyze_site(site)
        assert any("No accessible XML sitemap" in f.title for f in result.findings)

    def test_missing_canonical_flagged(self) -> None:
        site = _make_site(homepage=_make_page(canonical_url=None))
        result = analyze_site(site)
        assert any("canonical tag" in f.title for f in result.findings)

    def test_no_schema_anywhere_flagged(self) -> None:
        site = _make_site(homepage=_make_page(schema_types=[]))
        result = analyze_site(site)
        assert any("structured data" in f.title.lower() for f in result.findings)

    def test_noindex_homepage_flagged_critical(self) -> None:
        site = _make_site(homepage=_make_page(meta_robots="noindex, nofollow"))
        result = analyze_site(site)
        finding = next(f for f in result.findings if "noindex" in f.title.lower())
        assert finding.severity.value == "Critical"
        assert finding.score_deduction == 50.0


# ---------------------------------------------------------------------------
# On-Page SEO checks
# ---------------------------------------------------------------------------

class TestOnPageSeoChecks:

    def test_missing_title_flagged(self) -> None:
        site = _make_site(homepage=_make_page(page_title=None))
        result = analyze_site(site)
        assert any("<title>" in f.title for f in result.findings)

    def test_title_too_short_flagged(self) -> None:
        site = _make_site(homepage=_make_page(page_title="Short"))
        result = analyze_site(site)
        assert any("title" in f.title.lower() and "length" in f.title.lower() for f in result.findings)

    def test_missing_meta_description_flagged(self) -> None:
        site = _make_site(homepage=_make_page(meta_description=None))
        result = analyze_site(site)
        assert any("meta description" in f.title.lower() for f in result.findings)

    def test_missing_h1_flagged(self) -> None:
        site = _make_site(homepage=_make_page(h1_tags=[]))
        result = analyze_site(site)
        assert any("H1" in f.title for f in result.findings)

    def test_multiple_h1_flagged(self) -> None:
        site = _make_site(homepage=_make_page(h1_tags=["First", "Second"]))
        result = analyze_site(site)
        assert any("H1" in f.title for f in result.findings)

    def test_no_internal_links_flagged_low_severity(self) -> None:
        site = _make_site(homepage=_make_page(internal_links=[]))
        result = analyze_site(site)
        finding = next(f for f in result.findings if "internal links" in f.title.lower())
        assert finding.severity.value == "Low"

    def test_proportional_deduction_scales_with_affected_pages(self) -> None:
        one_bad_page = _make_site(sampled_pages=[
            _make_page(url="https://example.com/p1", page_title=None),
            _make_page(url="https://example.com/p2"),
            _make_page(url="https://example.com/p3"),
        ])
        all_bad_pages = _make_site(homepage=_make_page(page_title=None), sampled_pages=[
            _make_page(url="https://example.com/p1", page_title=None),
            _make_page(url="https://example.com/p2", page_title=None),
        ])

        one_bad_result = analyze_site(one_bad_page)
        all_bad_result = analyze_site(all_bad_pages)

        one_bad_finding = next(f for f in one_bad_result.findings if "<title>" in f.title)
        all_bad_finding = next(f for f in all_bad_result.findings if "<title>" in f.title)

        assert one_bad_finding.score_deduction < all_bad_finding.score_deduction


# ---------------------------------------------------------------------------
# Content Quality checks
# ---------------------------------------------------------------------------

class TestContentQualityChecks:

    def test_thin_content_flagged(self) -> None:
        site = _make_site(homepage=_make_page(word_count=50))
        result = analyze_site(site)
        assert any("thin content" in f.title.lower() for f in result.findings)

    def test_sufficient_content_not_flagged(self) -> None:
        site = _make_site(homepage=_make_page(word_count=400))
        result = analyze_site(site)
        assert not any("thin content" in f.title.lower() for f in result.findings)


# ---------------------------------------------------------------------------
# Accessibility checks
# ---------------------------------------------------------------------------

class TestAccessibilityChecks:

    def test_missing_alt_text_flagged(self) -> None:
        site = _make_site(homepage=_make_page(images=[
            ImageInfo(src="https://example.com/a.png", alt="", has_alt_attribute=False),
        ]))
        result = analyze_site(site)
        assert any("alt attributes" in f.title.lower() for f in result.findings)

    def test_present_alt_text_not_flagged(self) -> None:
        site = _make_site(homepage=_make_page(images=[
            ImageInfo(src="https://example.com/a.png", alt="Logo", has_alt_attribute=True),
        ]))
        result = analyze_site(site)
        assert not any("alt attributes" in f.title.lower() for f in result.findings)

    def test_missing_language_flagged(self) -> None:
        site = _make_site(homepage=_make_page(page_language=None))
        result = analyze_site(site)
        assert any("language attribute" in f.title.lower() for f in result.findings)


# ---------------------------------------------------------------------------
# Security checks
# ---------------------------------------------------------------------------

class TestSecurityChecks:

    def test_non_https_site_flagged_critical_in_security_category(self) -> None:
        site = _make_site(homepage=_make_page(is_https=False))
        result = analyze_site(site)
        security_finding = next(f for f in result.findings if f.category == "Security")
        assert security_finding.severity.value == "Critical"
        assert security_finding.score_deduction == 100.0

    def test_non_https_site_security_category_score_is_zero(self) -> None:
        site = _make_site(homepage=_make_page(is_https=False))
        result = analyze_site(site)
        security = next(cs for cs in result.category_scores if cs.category == "Security")
        assert security.score == 0.0

    def test_https_site_has_no_security_findings(self) -> None:
        site = _make_site(homepage=_make_page(is_https=True))
        result = analyze_site(site)
        assert not any(f.category == "Security" for f in result.findings)


# ---------------------------------------------------------------------------
# Overall score computation
# ---------------------------------------------------------------------------

class TestOverallScoreComputation:

    def test_overall_score_reflects_category_weights(self) -> None:
        # HTTPS-only failure hits both Technical (40%) and Security (5%) categories.
        site = _make_site(homepage=_make_page(is_https=False))
        result = analyze_site(site)

        technical = next(cs for cs in result.category_scores if cs.category == "Technical SEO")
        security = next(cs for cs in result.category_scores if cs.category == "Security")
        assert technical.score == 70.0
        assert security.score == 0.0

        expected_overall = round(
            sum(cs.score * cs.weight_percent for cs in result.category_scores) / 100.0, 1
        )
        assert result.overall_score == expected_overall

    def test_category_score_never_goes_below_zero(self) -> None:
        # Stack multiple technical failures whose deductions would exceed 100 points.
        blocking_robots = RobotsTxtEvidence(
            is_accessible=True, http_status=200, disallow_rules=["/"],
            allow_rules=[], sitemap_urls=[], blocks_root_path=True,
        )
        site = _make_site(
            homepage=_make_page(
                is_https=False, http_status=500, canonical_url=None,
                schema_types=[], meta_robots="noindex",
            ),
            robots_txt=blocking_robots,
            sitemaps=[SitemapEvidence(url="https://example.com/sitemap.xml", is_accessible=False, http_status=404, url_count=0)],
        )
        result = analyze_site(site)
        technical = next(cs for cs in result.category_scores if cs.category == "Technical SEO")
        assert technical.score == 0.0
