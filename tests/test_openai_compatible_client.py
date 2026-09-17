import httpx
import pytest
from pydantic import SecretStr

from app.config import LLMSettings, Settings
from app.llm.base import LLMConfigurationError, LLMUnavailableError
from app.llm.openai_compatible import OpenAICompatibleClient

SETTINGS = LLMSettings(
    provider="gemini", label="Google AI Studio", base_url="https://llm.test/v1", model="test-model",
    api_key=SecretStr("secret"), timeout_seconds=5, problem=None,
)


def make_client(handler) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(SETTINGS, http=httpx.Client(transport=httpx.MockTransport(handler)))


def ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})


def test_sends_openai_compatible_request_with_json_mode():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = httpx.Response(200, content=request.content).json()
        return ok('{"updates": {}}')

    assert make_client(handler).complete("sys", "user", json_output=True, max_tokens=100) == '{"updates": {}}'
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_falls_back_when_json_mode_unsupported():
    bodies = []

    def handler(request: httpx.Request):
        body = httpx.Response(200, content=request.content).json()
        bodies.append(body)
        return httpx.Response(400, text="response_format not supported") if "response_format" in body else ok("{}")

    client = make_client(handler)
    assert client.complete("s", "u", json_output=True, max_tokens=10) == "{}"
    client.complete("s", "u", json_output=True, max_tokens=10)
    assert len(bodies) == 3  # later calls skip JSON mode straight away


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, LLMConfigurationError), (404, LLMConfigurationError), (429, LLMUnavailableError), (503, LLMUnavailableError)],
)
def test_http_errors_are_classified(status, error):
    with pytest.raises(error):
        make_client(lambda r: httpx.Response(status, text="nope")).complete("s", "u", json_output=False, max_tokens=10)


def test_timeout_is_unavailable():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMUnavailableError):
        make_client(handler).complete("s", "u", json_output=False, max_tokens=10)


def test_missing_key_is_reported_by_settings(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    llm = Settings(_env_file=None, llm_provider="nvidia").llm()
    assert llm.base_url == "https://integrate.api.nvidia.com/v1"
    assert "LLM_API_KEY" in llm.problem
    with pytest.raises(LLMConfigurationError):
        OpenAICompatibleClient(llm)


def test_unknown_provider_is_reported():
    assert "Unknown LLM_PROVIDER" in Settings(_env_file=None, llm_provider="foo", llm_api_key="k").llm().problem
