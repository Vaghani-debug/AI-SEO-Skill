"""
test/test_config.py

Unit tests for src/config.py — specifically the llm_provider Literal
validation and the shared Gemini/OpenAI retry and search-context settings
added as the single-switch config layer.

Run with:
    pytest test/test_config.py -v
"""

import pytest
from pydantic import ValidationError

from src.config import Settings


class TestLLMProviderValidation:

    @pytest.mark.parametrize("provider", ["perplexity", "gemini", "openai"])
    def test_valid_providers_construct_settings(self, provider: str) -> None:
        settings = Settings(llm_provider=provider)
        assert settings.llm_provider == provider

    def test_invalid_provider_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(llm_provider="claude")

    def test_default_provider_is_gemini(self) -> None:
        # Constructed with no override so any real .env LLM_PROVIDER value doesn't affect this check
        settings = Settings(_env_file=None)
        assert settings.llm_provider == "gemini"


class TestSharedLLMSettingsDefaults:

    def test_openai_search_context_size_defaults_to_high(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.openai_search_context_size == "high"

    def test_llm_retry_defaults(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.llm_retry_attempts == 3
        assert settings.llm_retry_backoff_base_seconds == 1.0
