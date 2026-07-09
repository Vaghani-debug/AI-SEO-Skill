"""
src/services/url_service.py

URL normalisation and validation service.

Responsibility: transform raw user input into a clean, validated URL string
that downstream services (fetch_service, extractor_service, etc.) can use
without any further URL handling.

Public interface:
    normalize_and_validate(raw_url: str) -> UrlValidationResult

This module uses only the Python standard library (urllib.parse) so it
carries zero runtime dependencies and is trivially testable.
"""

import logging  # Standard library logging — records validation results for debugging
import re  # Standard library regex — used for basic domain character validation
from dataclasses import dataclass, field  # dataclass provides a clean structured result without Pydantic overhead
from urllib.parse import urlparse, urlunparse  # urlparse splits a URL into its components; urlunparse reassembles them

# Module-level logger — records every normalisation and every validation failure
logger = logging.getLogger(__name__)  # __name__ resolves to "src.services.url_service"

# ---------------------------------------------------------------------------
# Supported schemes
# ---------------------------------------------------------------------------

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
# frozenset is immutable, which makes membership tests (scheme in _ALLOWED_SCHEMES) O(1)
# Only http and https are supported; ftp, file, data, mailto, etc. are all rejected


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class UrlValidationResult:
    """
    Outcome of a URL normalisation and validation attempt.

    Callers should check `is_valid` before using `normalized_url`.
    When `is_valid` is False, `error_code` and `error_message` describe
    what went wrong in terms suitable for the UI and API error response.
    """

    is_valid: bool
    # True if the URL passed all checks and normalized_url is safe to use

    normalized_url: str = ""
    # The cleaned, scheme-prefixed URL ready for fetching.
    # Only meaningful when is_valid is True.

    error_code: str = ""
    # Short machine-readable code: "empty", "scheme_not_allowed", "no_domain", "invalid_domain"
    # Empty string when is_valid is True.

    error_message: str = ""
    # Plain-English message suitable for display in the UI error card.
    # Empty string when is_valid is True.


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def normalize_and_validate(raw_url: str) -> UrlValidationResult:
    """
    Normalise and validate a raw URL string provided by the user.

    Normalisation rules:
    - Strip leading and trailing whitespace.
    - If no scheme is present, prepend ``https://``.
    - Convert the scheme and host to lowercase.
    - Remove any trailing slash from the root path so URLs are consistent.

    Validation rules:
    - Input must not be empty after stripping.
    - Input must not be shorter than 4 characters (minimum: ``a.bc``).
    - Scheme must be ``http`` or ``https``.
    - Network location (domain) must not be empty.
    - Domain must contain at least one dot (basic TLD presence check).
    - Domain characters must match the expected pattern (letters, digits, hyphens, dots).

    Args:
        raw_url: The raw string entered by the user, e.g. ``www.example.com``
                 or ``https://example.com``.

    Returns:
        UrlValidationResult with is_valid=True and a normalised URL on success,
        or is_valid=False with error_code and error_message on failure.
    """
    logger.debug("Normalising and validating URL input: %r", raw_url)  # Log the raw input for debugging

    # --- Step 1: Strip whitespace -------------------------------------------

    stripped: str = raw_url.strip()  # Remove leading/trailing spaces, tabs, newlines

    if not stripped:
        # Guard: reject completely empty or whitespace-only input
        logger.warning("URL validation failed: empty input")
        return UrlValidationResult(
            is_valid=False,
            error_code="empty",
            error_message="Please enter a website URL before clicking Audit.",
        )

    # --- Step 2: Reject obviously invalid inputs ----------------------------

    if len(stripped) < 4:
        # Guard: the shortest valid URL has the form ``a.bc`` (4 characters)
        logger.warning("URL validation failed: input too short (%r)", stripped)
        return UrlValidationResult(
            is_valid=False,
            error_code="too_short",
            error_message=f"'{stripped}' does not look like a valid website address.",
        )

    # --- Step 2b: Detect scheme-without-slashes patterns --------------------
    # Schemes like mailto:, javascript:, data:, ftp: use "scheme:" without "://".
    # _ensure_scheme only adds "https://" when "://" is absent, so these would
    # slip through the scheme check later as a domain string.
    # We catch them here before normalisation: if the input has ":" but not "://"
    # and the part before ":" contains no dot (ruling out host:port like example.com:8080),
    # treat it as an unsupported scheme attempt.

    if ":" in stripped and "://" not in stripped:
        # Split on the first colon to isolate the potential scheme token
        potential_scheme: str = stripped.split(":")[0].lower()

        if "." not in potential_scheme:
            # Confirmed as a scheme-like prefix (not a host:port) — check if it is allowed
            if potential_scheme not in _ALLOWED_SCHEMES:
                # Reject unsupported bare schemes: mailto, javascript, data, etc.
                logger.warning(
                    "URL validation failed: bare unsupported scheme %r in %r",
                    potential_scheme,
                    stripped,
                )
                return UrlValidationResult(
                    is_valid=False,
                    error_code="scheme_not_allowed",
                    error_message=(
                        f"The scheme '{potential_scheme}:' is not supported. "
                        "Please enter an http:// or https:// website address."
                    ),
                )

    # --- Step 3: Prepend scheme if missing ----------------------------------

    url_with_scheme: str = _ensure_scheme(stripped)
    # After this step the string is guaranteed to start with a scheme (https:// or as-is)

    # --- Step 4: Parse into components --------------------------------------

    parsed = urlparse(url_with_scheme)
    # parsed.scheme  → "https"
    # parsed.netloc  → "www.example.com" (the domain)
    # parsed.path    → "/" or ""
    # parsed.query   → query string if present
    # parsed.fragment → fragment if present

    # --- Step 5: Validate scheme --------------------------------------------

    scheme: str = parsed.scheme.lower()  # Normalise scheme to lowercase

    if scheme not in _ALLOWED_SCHEMES:
        # Reject ftp://, file://, data:, mailto:, javascript:, etc.
        logger.warning("URL validation failed: unsupported scheme %r in %r", scheme, url_with_scheme)
        return UrlValidationResult(
            is_valid=False,
            error_code="scheme_not_allowed",
            error_message=(
                f"The scheme '{scheme}://' is not supported. "
                "Please enter an http:// or https:// website address."
            ),
        )

    # --- Step 6: Validate domain presence -----------------------------------

    netloc: str = parsed.netloc.lower()  # Normalise domain to lowercase

    if not netloc:
        # Guard: a URL with a scheme but no domain (e.g. "https://") is invalid
        logger.warning("URL validation failed: no domain found in %r", url_with_scheme)
        return UrlValidationResult(
            is_valid=False,
            error_code="no_domain",
            error_message="The URL does not contain a valid domain name. Please check and try again.",
        )

    # --- Step 7: Validate domain format -------------------------------------

    domain_error: str = _validate_domain(netloc)
    # Returns an empty string if valid, or a user-facing message if invalid

    if domain_error:
        logger.warning("URL validation failed: domain check failed for %r — %s", netloc, domain_error)
        return UrlValidationResult(
            is_valid=False,
            error_code="invalid_domain",
            error_message=domain_error,
        )

    # --- Step 8: Reassemble the normalised URL ------------------------------

    # Remove trailing slash from root path for consistency:
    # https://example.com/ → https://example.com
    clean_path: str = parsed.path if parsed.path and parsed.path != "/" else ""

    normalised: str = urlunparse((
        scheme,           # scheme: "https"
        netloc,           # netloc: "www.example.com"
        clean_path,       # path: "" or "/about" etc.
        parsed.params,    # params: rarely used; preserve as-is
        parsed.query,     # query: preserve query string if present
        "",               # fragment: strip fragment (#section) — not useful for crawling
    ))

    logger.info("URL normalised successfully: %r → %r", raw_url, normalised)  # Log the successful normalisation

    return UrlValidationResult(
        is_valid=True,
        normalized_url=normalised,
        # error_code and error_message default to "" which signals success
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _ensure_scheme(url: str) -> str:
    """
    Prepend ``https://`` to a URL that has no scheme.

    Recognises the following bare-domain formats and adds a scheme:
    - ``www.example.com``      → ``https://www.example.com``
    - ``example.com``          → ``https://example.com``
    - ``example.com/path``     → ``https://example.com/path``

    URLs that already start with a scheme (e.g. ``http://``, ``https://``,
    ``ftp://``) are returned unchanged so the scheme validator can reject
    unsupported ones.

    Args:
        url: A stripped URL string that may or may not have a scheme.

    Returns:
        The URL string with a scheme guaranteed to be present.
    """
    if "://" in url:
        # The URL already has a scheme separator; return it unchanged
        return url

    # No "://" found — treat as a bare domain and add https://
    return f"https://{url}"


# Regex pattern that matches valid domain/host characters
# Allows: letters (a-z A-Z), digits (0-9), hyphens (-), dots (.)
# Also allows port numbers with colon, e.g. "localhost:8000"
_DOMAIN_PATTERN: re.Pattern[str] = re.compile(
    r"^[a-z0-9]"          # Must start with a letter or digit (lowercase after normalisation)
    r"[a-z0-9\-\.]*"      # Middle: letters, digits, hyphens, dots
    r"[a-z0-9]"           # Must end with a letter or digit
    r"(\:\d+)?$"          # Optional port number, e.g. ":8080"
)
# Note: IPv6 addresses and internationalised domain names (IDN) are out of scope for the MVP


def _validate_domain(netloc: str) -> str:
    """
    Perform basic structural validation on a domain name.

    Checks:
    1. The domain contains at least one dot (rules out ``localhost``-style
       input unless used intentionally — acceptable for the MVP).
    2. No label (part between dots) is empty (rules out ``example..com``).
    3. No label exceeds 63 characters (DNS limit per RFC 1035).
    4. The full netloc matches the expected character pattern.

    Args:
        netloc: The lowercase domain string extracted from a parsed URL,
                e.g. ``www.example.com`` or ``example.co.uk``.

    Returns:
        Empty string if the domain is valid.
        A user-facing error message string if the domain is invalid.
    """
    # Strip port number from netloc before checking the domain itself
    host: str = netloc.split(":")[0]  # "example.com:8080" → "example.com"

    if "." not in host:
        # Require at least one dot — rules out bare words like "localhost" and "example"
        return (
            f"'{host}' does not look like a valid domain name. "
            "Please include the full domain, e.g. www.example.com."
        )

    labels: list[str] = host.split(".")  # Split on dots to check each DNS label individually

    for label in labels:
        if not label:
            # Empty label means consecutive dots: "example..com"
            return f"'{host}' contains consecutive dots, which is not a valid domain name."

        if len(label) > 63:
            # RFC 1035: each DNS label must be 63 characters or fewer
            return f"'{host}' contains a segment longer than 63 characters, which is not valid."

    if not _DOMAIN_PATTERN.match(host):
        # Full character-set check — catches domains with spaces, @, slashes, etc.
        return (
            f"'{host}' contains invalid characters. "
            "Please enter a standard web address."
        )

    return ""  # Empty string signals: domain is valid
