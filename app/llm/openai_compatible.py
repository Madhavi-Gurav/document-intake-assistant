"""Client for OpenAI-compatible chat-completions APIs (Google AI Studio, NVIDIA NIM)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from app.config import LLMSettings
from app.llm.base import LLMConfigurationError, LLMError, LLMModelUnavailableError, LLMUnavailableError

log = logging.getLogger(__name__)


MAX_ATTEMPTS = 3  # hosted free tiers often return 503 "overloaded" or stall; brief retries fix most of it


class OpenAICompatibleClient:
    def __init__(self, settings: LLMSettings, http: httpx.Client | None = None) -> None:
        if settings.problem or settings.api_key is None:
            raise LLMConfigurationError(settings.problem or "missing API key")
        self.provider = settings.provider
        self.model = settings.model
        self._url = f"{settings.base_url}/chat/completions"
        self._http = http or httpx.Client(timeout=settings.timeout_seconds)
        self._extra_body = settings.extra_body
        self._headers = {"Authorization": f"Bearer {settings.api_key.get_secret_value()}"}
        # Not every model behind these gateways supports JSON mode; we find out on first use.
        self._json_mode_supported = True

    def complete(self, system: str, user: str, *, json_output: bool, max_tokens: int) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0 if json_output else 0.4,
            "max_tokens": max_tokens,
            **self._extra_body,
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
            message = choice["message"]
            content = message.get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailableError(f"unexpected response body from provider: {exc!r}") from exc
        if choice.get("finish_reason") == "length":
            log.warning("Model output hit max_tokens=%s; it may be truncated", max_tokens)
        return _strip_reasoning(content, message.get("reasoning_content"))

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            last = attempt == MAX_ATTEMPTS
            try:
                response = self._http.post(self._url, json=body, headers=self._headers)
            except httpx.TimeoutException as exc:
                if last:
                    raise LLMUnavailableError(f"request to {self.provider} timed out") from exc
                log.warning("Request to %s timed out; retrying", self.provider)
                continue
            except httpx.HTTPError as exc:
                if last:
                    raise LLMUnavailableError(f"could not reach {self.provider}: {exc}") from exc
                log.warning("Request to %s failed (%s); retrying", self.provider, exc)
                continue
            if (response.status_code == 429 or response.status_code >= 500) and not last:
                log.warning("%s returned HTTP %s; retrying", self.provider, response.status_code)
                time.sleep(1.5 * attempt)
                continue
            return response
        raise AssertionError("unreachable")

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        detail = f"{self.provider} returned HTTP {status}: {_error_text(response)}"
        if status in (401, 403):
            raise LLMConfigurationError(f"API key rejected. {detail}")
        if status in (404, 410):
            raise LLMModelUnavailableError(f"Model '{self.model}' or endpoint not found (or retired). {detail}")
        if status == 429 or status >= 500:
            raise LLMUnavailableError(detail)
        raise LLMError(detail)


def _error_text(response: httpx.Response) -> str:
    text = response.text.strip().replace("\n", " ")
    return text[:500]


_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


def _strip_reasoning(content: str, reasoning: str | None) -> str:
    """Some reasoning models echo their chain of thought into `content`; keep only the answer."""
    content = _THINK_BLOCK.sub("", content)
    if reasoning and content.lstrip().startswith(reasoning.strip()[:200]):
        content = content.lstrip()[len(reasoning.strip()):]
    return content.strip()
