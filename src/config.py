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

    # --- LLM / Perplexity -------------------------------------------------
    perplexity_api_key: str = Field(
        default="",  # Empty default so the app starts without crashing; a missing key is caught at report generation time
        description="Perplexity API key loaded from PERPLEXITY_API_KEY in .env",
    )
    perplexity_model: str = Field(
        default="sonar-pro",  # sonar-pro: advanced search model with grounding, best for comprehensive reports
        description="Perplexity model name to use for LLM report generation",
    )

    llm_provider: str = Field(
        default="gemini",  # Change to "perplexity" in .env to route all LLM calls to Perplexity
        description="LLM provider to use: 'gemini' or 'perplexity'",
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

    # --- Report Storage ------------------------------------------------------

    reports_dir: str = Field(
        default="reports",  # Local folder where generated .md and .pdf files are saved by audit_id
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
