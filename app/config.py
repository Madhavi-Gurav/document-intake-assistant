"""Runtime configuration, read from environment variables or a local `.env` file."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Both providers expose an OpenAI-compatible chat-completions API.
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "gemini": {
        "label": "Google AI Studio",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-flash",
    },
    "nvidia": {
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "meta/llama-3.3-70b-instruct",
    },
}


class LLMSettings(BaseModel):
    provider: str
    label: str
    base_url: str
    model: str
    api_key: SecretStr | None
    timeout_seconds: float
    problem: str | None  # human-readable reason the LLM cannot be used, if any


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = "gemini"
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: float = 60.0

    def llm(self) -> LLMSettings:
        provider = self.llm_provider.strip().lower()
        preset = PROVIDER_PRESETS.get(provider)
        problem = None
        if preset is None:
            problem = f"Unknown LLM_PROVIDER '{self.llm_provider}'. Use one of: {', '.join(PROVIDER_PRESETS)}."
            preset = {"label": provider or "unknown", "base_url": "", "model": ""}
        key = self.llm_api_key if self.llm_api_key and self.llm_api_key.get_secret_value().strip() else None
        if problem is None and key is None:
            problem = "LLM_API_KEY is not set. Copy .env.example to .env and add your API key."
        return LLMSettings(
            provider=provider,
            label=preset["label"],
            base_url=(self.llm_base_url or preset["base_url"]).rstrip("/"),
            model=self.llm_model or preset["model"],
            api_key=key,
            timeout_seconds=self.llm_timeout_seconds,
            problem=problem,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
