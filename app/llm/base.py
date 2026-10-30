"""Provider-neutral LLM interface and error types.

Application code depends only on `LLMClient`. Swapping Gemini for NIM, or for any
other provider, means adding one class that implements `complete`.
"""

from __future__ import annotations

from typing import Protocol


class LLMError(Exception):
    """Base class. `user_message` is safe to show in the UI; `str(exc)` is for logs."""

    code = "llm_error"
    user_message = "The assistant couldn't respond just now. Please try again."


class LLMConfigurationError(LLMError):
    """Missing or rejected API key, unknown model, bad base URL. Retrying won't help."""

    code = "llm_not_configured"
    user_message = "The language model is not configured correctly. Check the server's .env settings."


class LLMModelUnavailableError(LLMConfigurationError):
    """The endpoint works but the configured model does not exist for this account.

    Hosted providers retire models without notice, and a retired model is still listed by
    `/v1/models`, so this needs to be said plainly rather than blamed on the API key.
    """

    code = "llm_model_unavailable"
    user_message = (
        "The configured language model is unavailable — it may have been retired by the provider. "
        "Set LLM_MODEL to a model this account can use."
    )


class LLMUnavailableError(LLMError):
    """Timeouts, network failures, rate limits, provider 5xx. Worth retrying later."""

    code = "llm_unavailable"
    user_message = "The language model is temporarily unavailable. Your answers so far are safe — please try again."


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, system: str, user: str, *, json_output: bool, max_tokens: int) -> str:
        """Return the model's text for a single system + user exchange. Raise `LLMError` on failure."""
        ...
