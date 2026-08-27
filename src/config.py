"""
src/config.py

Application configuration.

Loads all settings from environment variables (via .env file) into a typed
Pydantic settings model.  Every service in the application should import
Settings from this module — no service should read os.environ directly.

Usage:
    from src.config import get_settings
    settings = get_settings()
"""

from functools import lru_cache  # lru_cache lets us create a singleton settings object without a global variable
from typing import Literal  # Literal restricts llm_provider to the three supported values at load time

from pydantic import Field  # Field lets us set defaults and descriptions for each setting
from pydantic_settings import BaseSettings  # BaseSettings reads fields from environment variables automatically
from pydantic_settings import SettingsConfigDict  # SettingsConfigDict replaces the nested class Config syntax (Pydantic v2)


class Settings(BaseSettings):
    """
    All configuration values for the AI SEO Agent MVP.

    Values are read from environment variables.
    If a .env file is present in the working directory, it is loaded automatically.
    """

    # --- LLM / Gemini -------------------------------------------------------

    gemini_api_key: str = Field(
        default="",  # Empty default so the app starts without crashing; a missing key is caught at report generation time
        description="Google Gemini API key loaded from GEMINI_API_KEY in .env",
    )

    gemini_model: str = Field(
        default="gemini-2.5-flash",  # Updated: gemini-1.5-flash is deprecated; gemini-2.5-flash is the current fast model
        description="Gemini model name to use for LLM report generation",
    )

    gemini_thinking_level: str = Field(
        default="high",  # Higher thinking level improves multi-step SEO reasoning at the cost of latency/tokens
        description="Gemini generation_config thinking_level (e.g. 'low', 'high') for report and research calls",
    )

    # --- LLM / Perplexity -------------------------------------------------
    perplexity_api_key: str = Field(
        default="",  # Empty default so the app starts without crashing; a missing key is caught at report generation time
        description="Perplexity API key loaded from PERPLEXITY_API_KEY in .env",
    )
    perplexity_model: str = Field(
        default="sonar-pro",  # sonar-pro: advanced search model with grounding, best for comprehensive reports
        description="Perplexity model name to use for LLM report generation",
    )

    # --- LLM / OpenAI -------------------------------------------------------

    openai_api_key: str = Field(
        default="",  # Empty default so the app starts without crashing; a missing key is caught at call time
        description="OpenAI API key loaded from OPENAI_API_KEY in .env",
    )

    openai_model: str = Field(
        default="gpt-5.6",  # Current flagship model with Responses API web_search tool support
        description="OpenAI model name to use for LLM report generation and research (Responses API)",
    )

    openai_reasoning_effort: str = Field(
        default="medium",  # One of: none, minimal, low, medium, high, xhigh, max
        description="OpenAI Responses API reasoning effort for report and research calls",
    )

    openai_search_context_size: str = Field(
        default="high",  # One of: low, medium, high — "high" favors thorough SEO research coverage over token cost
        description="OpenAI Responses API web_search tool search_context_size for research calls",
    )

    # llm_provider is the single switch: change only this value (and its matching api key) to swap providers everywhere
    llm_provider: Literal["perplexity", "gemini", "openai"] = Field(
        default="gemini",  # Change to "perplexity", "gemini", or "openai" in .env to select the active provider
        description="LLM provider to use: 'gemini', 'perplexity', or 'openai'",
    )

    perplexity_retry_attempts: int = Field(
        default=3,  # e.g. a transient rate-limit or 5xx on attempt 1 gets two more chances
        description="Maximum attempts for a single Perplexity research call before giving up on transient failures",
    )

    perplexity_retry_backoff_base_seconds: float = Field(
        default=1.0,  # Exponential backoff: 1.0s, 2.0s, 4.0s, ... between retry attempts
        description="Base delay in seconds for exponential backoff between Perplexity retry attempts",
    )

    # Shared across Gemini/OpenAI adapters (Perplexity keeps its own dedicated fields above)
    llm_retry_attempts: int = Field(
        default=3,  # Same shape as perplexity_retry_attempts, generalized for the other two providers
        description="Maximum attempts for a single Gemini/OpenAI call before giving up on transient failures",
    )

    llm_retry_backoff_base_seconds: float = Field(
        default=1.0,  # Exponential backoff: 1.0s, 2.0s, 4.0s, ... between retry attempts
        description="Base delay in seconds for exponential backoff between Gemini/OpenAI retry attempts",
    )

    # --- HTTP Fetch ----------------------------------------------------------

    fetch_timeout_seconds: int = Field(
        default=15,  # Maximum seconds to wait for any single HTTP request before giving up
        description="Timeout in seconds for outbound HTTP requests to audited websites",
    )

    fetch_max_redirects: int = Field(
        default=5,  # Limit redirect chains to prevent infinite loops on misconfigured sites
        description="Maximum number of HTTP redirects to follow when fetching a URL",
    )

    fetch_retry_attempts: int = Field(
        default=3,  # e.g. a transient 503 or timeout on attempt 1 gets two more chances
        description="Maximum attempts for a single fetch before giving up on transient failures",
    )

    fetch_retry_backoff_base_seconds: float = Field(
        default=0.5,  # Exponential backoff: 0.5s, 1.0s, 2.0s, ... between retry attempts
        description="Base delay in seconds for exponential backoff between fetch retry attempts",
    )

    wayback_fallback_enabled: bool = Field(
        default=True,  # Fall back to a real, citable archived snapshot rather than leaving evidence empty
        description="When True, fetch an archive.org snapshot if a live fetch fails after all retries",
    )

    # --- PageSpeed Insights (Core Web Vitals / Performance) -------------------
    # (Removed: pagespeed_enabled/pagespeed_api_key/pagespeed_timeout_seconds
    # were only used by the deleted sampled-crawl pipeline's pagespeed_service.)

    # --- Report Storage ------------------------------------------------------

    reports_dir: str = Field(
        default="reports",  # Local folder where generated .md and .json report files are saved by audit_id
        description="Directory path (relative to project root) where audit reports are stored",
    )

    # --- Application ---------------------------------------------------------

    app_title: str = Field(
        default="AI SEO Agent",  # Shown in the OpenAPI docs at /docs
        description="Application title shown in API documentation",
    )

    app_version: str = Field(
        default="0.1.0",  # MVP version; increment when the API shape changes
        description="Application version shown in API documentation",
    )

    debug: bool = Field(
        default=False,  # Set to True in development to enable detailed error responses
        description="Enable debug mode; never set to True in production",
    )

    # Pydantic v2 settings configuration — replaces the deprecated nested `class Config` syntax
    model_config = SettingsConfigDict(
        env_file=".env",      # Load environment variables from .env in the working directory
        extra="ignore",       # Silently ignore environment variables that have no matching field
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the application settings singleton.

    Uses lru_cache so the .env file is read exactly once per process,
    not on every request.  Call get_settings() wherever settings are needed
    instead of instantiating Settings() directly.
    """
    return Settings()  # Reads .env and environment variables on first call only
