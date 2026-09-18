"""Runtime configuration, read from environment variables or a local `.env` file."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

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
        "model": "nvidia/nemotron-3-super-120b-a12b",
    },
}


class LLMSettings(BaseModel):
    provider: str
    label: str
    base_url: str
    model: str
    api_key: SecretStr | None
    timeout_seconds: float
    extra_body: dict[str, Any]  # provider-specific request fields, e.g. disabling "thinking"
    problem: str | None  # human-readable reason the LLM cannot be used, if any


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = "nvidia"
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_extra_body: str | None = None  # JSON object merged into every chat-completions request

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
        extra_body: dict[str, Any] = {}
        if self.llm_extra_body and self.llm_extra_body.strip():
            try:
                extra_body = json.loads(self.llm_extra_body)
            except json.JSONDecodeError as exc:
                problem = problem or f"LLM_EXTRA_BODY is not valid JSON: {exc.msg}"
            if not isinstance(extra_body, dict):
                problem = problem or "LLM_EXTRA_BODY must be a JSON object."
                extra_body = {}
        return LLMSettings(
            provider=provider,
            label=preset["label"],
            base_url=(self.llm_base_url or preset["base_url"]).rstrip("/"),
            model=self.llm_model or preset["model"],
            api_key=key,
            timeout_seconds=self.llm_timeout_seconds,
            extra_body=extra_body,
            problem=problem,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
