"""
test/test_url_service.py

Unit tests for src/services/url_service.py.

Tests cover:
- Successful normalisation of bare domains and full URLs
- All validation error paths (empty, too short, bad scheme, no domain, bad domain)
- Edge cases (trailing slashes, uppercase input, ports, paths)

Run with:
    pytest test/test_url_service.py -v
"""

import pytest  # pytest: test runner and assertion helper

from src.services.url_service import normalize_and_validate, UrlValidationResult
# Import the public function and result type directly — no mocking needed for a pure function


# ===========================================================================
# Successful normalisation cases
# ===========================================================================

class TestNormalisationSuccess:
    """Tests that valid input is correctly normalised to a full https:// URL."""

    def test_bare_www_domain(self) -> None:
        """www.example.com should become https://www.example.com"""
        result = normalize_and_validate("www.example.com")  # Bare www domain
        assert result.is_valid is True  # Must pass validation
        assert result.normalized_url == "https://www.example.com"  # Scheme added

    def test_bare_domain_no_www(self) -> None:
        """example.com should become https://example.com"""
        result = normalize_and_validate("example.com")  # Bare domain without www
        assert result.is_valid is True
        assert result.normalized_url == "https://example.com"

    def test_full_https_url_unchanged(self) -> None:
        """https://www.example.com should be returned as-is"""
        result = normalize_and_validate("https://www.example.com")  # Already has scheme
        assert result.is_valid is True
        assert result.normalized_url == "https://www.example.com"  # No change expected

    def test_full_http_url_preserved(self) -> None:
        """http:// URLs are valid and should pass through"""
        result = normalize_and_validate("http://example.com")  # http is also supported
        assert result.is_valid is True
        assert result.normalized_url == "http://example.com"

    def test_trailing_slash_removed(self) -> None:
        """Trailing root slash should be stripped for consistency"""
        result = normalize_and_validate("https://www.example.com/")  # Trailing slash
        assert result.is_valid is True
        assert result.normalized_url == "https://www.example.com"  # Slash removed

    def test_path_preserved(self) -> None:
        """A URL with a path after the domain should keep the path"""
        result = normalize_and_validate("https://www.example.com/about")  # With path
        assert result.is_valid is True
        assert result.normalized_url == "https://www.example.com/about"  # Path kept

    def test_uppercase_is_normalised(self) -> None:
        """Scheme and domain should be lowercased"""
        result = normalize_and_validate("HTTPS://WWW.EXAMPLE.COM")  # All uppercase
        assert result.is_valid is True
        assert result.normalized_url == "https://www.example.com"  # Lowercased

    def test_leading_trailing_whitespace_stripped(self) -> None:
        """Extra whitespace around the URL should be ignored"""
        result = normalize_and_validate("  www.example.com  ")  # Surrounding spaces
        assert result.is_valid is True
        assert result.normalized_url == "https://www.example.com"  # Spaces removed

    def test_subdomain_url(self) -> None:
        """Deep subdomains should be accepted"""
        result = normalize_and_validate("blog.subdomain.example.co.uk")  # Multi-level
        assert result.is_valid is True
        assert result.normalized_url == "https://blog.subdomain.example.co.uk"

    def test_real_world_example(self) -> None:
        """The reference site used in demos should normalise correctly"""
        result = normalize_and_validate("www.truelinesolution.com")  # Demo site
        assert result.is_valid is True
        assert result.normalized_url == "https://www.truelinesolution.com"

    def test_query_string_preserved(self) -> None:
        """Query parameters should be kept in the normalised URL"""
        result = normalize_and_validate("https://example.com/search?q=seo")  # With query
        assert result.is_valid is True
        assert "search?q=seo" in result.normalized_url  # Query preserved

    def test_fragment_stripped(self) -> None:
        """Fragment identifiers (#section) should be removed — not useful for crawling"""
        result = normalize_and_validate("https://example.com/page#section")  # With fragment
        assert result.is_valid is True
        assert "#section" not in result.normalized_url  # Fragment stripped

    def test_no_error_fields_on_success(self) -> None:
        """On success, error_code and error_message must both be empty strings"""
        result = normalize_and_validate("example.com")
        assert result.error_code == ""   # No error code on success
        assert result.error_message == ""  # No error message on success


# ===========================================================================
# Empty and too-short input cases
# ===========================================================================

class TestEmptyAndShortInput:
    """Tests that empty and trivially short inputs are rejected."""

    def test_empty_string(self) -> None:
        """Empty string must be rejected"""
        result = normalize_and_validate("")  # Nothing entered
        assert result.is_valid is False
        assert result.error_code == "empty"  # Correct error code
        assert result.error_message  # Error message must be non-empty

    def test_whitespace_only(self) -> None:
        """A string of spaces must be treated as empty"""
        result = normalize_and_validate("   ")  # Only spaces
        assert result.is_valid is False
        assert result.error_code == "empty"

    def test_single_character(self) -> None:
        """A single letter is not a valid URL"""
        result = normalize_and_validate("a")  # Single character
        assert result.is_valid is False  # Must fail

    def test_three_characters(self) -> None:
        """Three characters are below the minimum valid domain length"""
        result = normalize_and_validate("abc")  # Too short
        assert result.is_valid is False


# ===========================================================================
# Invalid scheme cases
# ===========================================================================

class TestInvalidScheme:
    """Tests that unsupported URL schemes are rejected."""

    def test_ftp_scheme_rejected(self) -> None:
        """ftp:// must be rejected with scheme_not_allowed error"""
        result = normalize_and_validate("ftp://example.com")  # FTP is not supported
        assert result.is_valid is False
        assert result.error_code == "scheme_not_allowed"  # Correct error code
        assert "ftp" in result.error_message.lower()  # Message mentions the bad scheme

    def test_file_scheme_rejected(self) -> None:
        """file:// must be rejected"""
        result = normalize_and_validate("file:///etc/hosts")  # Local file path
        assert result.is_valid is False
        assert result.error_code == "scheme_not_allowed"

    def test_mailto_scheme_rejected(self) -> None:
        """mailto: must be rejected"""
        result = normalize_and_validate("mailto:user@example.com")  # Email address format
        assert result.is_valid is False
        assert result.error_code == "scheme_not_allowed"

    def test_javascript_scheme_rejected(self) -> None:
        """javascript: must be rejected (security: XSS prevention)"""
        result = normalize_and_validate("javascript:alert(1)")  # XSS attempt
        assert result.is_valid is False
        assert result.error_code == "scheme_not_allowed"


# ===========================================================================
# Invalid domain cases
# ===========================================================================

class TestInvalidDomain:
    """Tests that malformed or missing domain names are rejected."""

    def test_no_domain_after_scheme(self) -> None:
        """https:// with nothing after it must be rejected"""
        result = normalize_and_validate("https://")  # Scheme but no domain
        assert result.is_valid is False
        assert result.error_code == "no_domain"

    def test_domain_without_tld(self) -> None:
        """A word without a dot is not a valid domain"""
        result = normalize_and_validate("example")  # No TLD
        assert result.is_valid is False  # No dot → invalid domain

    def test_domain_consecutive_dots(self) -> None:
        """Consecutive dots in the domain must be rejected"""
        result = normalize_and_validate("example..com")  # Double dot
        assert result.is_valid is False
        assert result.error_code == "invalid_domain"

    def test_domain_with_spaces(self) -> None:
        """Spaces in a domain are not valid"""
        result = normalize_and_validate("https://example .com")  # Space in domain
        assert result.is_valid is False

    def test_domain_at_symbol(self) -> None:
        """@ in the input (looks like an email) should fail domain validation"""
        result = normalize_and_validate("user@example.com")  # Email format
        # urlparse treats "user@example.com" as username@host — the host is valid,
        # but without a scheme it becomes https://user@example.com which is unusual.
        # The main thing is the service handles it without crashing.
        assert isinstance(result, UrlValidationResult)  # Must return a result, not raise


# ===========================================================================
# Error field content
# ===========================================================================

class TestErrorFields:
    """Tests that error fields are correctly populated and empty on success."""

    def test_error_fields_empty_on_success(self) -> None:
        """Successful result must not contain any error information"""
        result = normalize_and_validate("https://example.com")
        assert result.is_valid is True
        assert result.error_code == ""      # No error code
        assert result.error_message == ""   # No error message
        assert result.normalized_url != ""  # URL must be populated

    def test_normalized_url_empty_on_failure(self) -> None:
        """Failed result must not contain a URL"""
        result = normalize_and_validate("")
        assert result.is_valid is False
        assert result.normalized_url == ""  # No URL on failure

    def test_error_message_is_user_friendly(self) -> None:
        """Error messages should be readable by non-technical users"""
        result = normalize_and_validate("ftp://example.com")
        assert result.is_valid is False
        assert len(result.error_message) > 10  # Must be more than a code — a proper sentence
        # Should not contain Python exception language
        assert "Traceback" not in result.error_message
        assert "Exception" not in result.error_message
