"""Client for OpenAI-compatible chat-completions APIs (Google AI Studio, NVIDIA NIM)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import LLMSettings
from app.llm.base import LLMConfigurationError, LLMError, LLMUnavailableError

log = logging.getLogger(__name__)


class OpenAICompatibleClient:
    def __init__(self, settings: LLMSettings, http: httpx.Client | None = None) -> None:
        if settings.problem or settings.api_key is None:
            raise LLMConfigurationError(settings.problem or "missing API key")
        self.provider = settings.provider
        self.model = settings.model
        self._url = f"{settings.base_url}/chat/completions"
        self._http = http or httpx.Client(timeout=settings.timeout_seconds)
        self._headers = {"Authorization": f"Bearer {settings.api_key.get_secret_value()}"}
        # Not every model behind these gateways supports JSON mode; we find out on first use.
        self._json_mode_supported = True

    def complete(self, system: str, user: str, *, json_output: bool, max_tokens: int) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0 if json_output else 0.4,
            "max_tokens": max_tokens,
        }
        if json_output and self._json_mode_supported:
            body["response_format"] = {"type": "json_object"}

        response = self._post(body)
        if response.status_code == 400 and "response_format" in body:
            # JSON mode is a nicety: the prompt already demands JSON and the parser validates it.
            log.warning("Provider rejected response_format; retrying without JSON mode: %s", _error_text(response))
            self._json_mode_supported = False
            body.pop("response_format")
            response = self._post(body)

        self._raise_for_status(response)
        try:
            choice = response.json()["choices"][0]
            content = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailableError(f"unexpected response body from provider: {exc!r}") from exc
        if choice.get("finish_reason") == "length":
            log.warning("Model output hit max_tokens=%s; it may be truncated", max_tokens)
        return content

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        try:
            return self._http.post(self._url, json=body, headers=self._headers)
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError(f"request to {self.provider} timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"could not reach {self.provider}: {exc}") from exc

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        detail = f"{self.provider} returned HTTP {status}: {_error_text(response)}"
        if status in (401, 403):
            raise LLMConfigurationError(f"API key rejected. {detail}")
        if status == 404:
            raise LLMConfigurationError(f"Model '{self.model}' or endpoint not found. {detail}")
        if status == 429 or status >= 500:
            raise LLMUnavailableError(detail)
        raise LLMError(detail)


def _error_text(response: httpx.Response) -> str:
    text = response.text.strip().replace("\n", " ")
    return text[:500]
