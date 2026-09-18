import httpx
import pytest
from pydantic import SecretStr

from app.config import LLMSettings, Settings
from app.llm.base import LLMConfigurationError, LLMUnavailableError
from app.llm.openai_compatible import OpenAICompatibleClient

SETTINGS = LLMSettings(
    provider="gemini", label="Google AI Studio", base_url="https://llm.test/v1", model="test-model",
    api_key=SecretStr("secret"), timeout_seconds=5, extra_body={"chat_template_kwargs": {"enable_thinking": False}}, problem=None,
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
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}  # LLM_EXTRA_BODY passthrough


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
    [(401, LLMConfigurationError), (404, LLMConfigurationError), (410, LLMConfigurationError), (429, LLMUnavailableError), (503, LLMUnavailableError)],
)
def test_http_errors_are_classified(status, error, monkeypatch):
    monkeypatch.setattr("app.llm.openai_compatible.time.sleep", lambda s: None)
    with pytest.raises(error):
        make_client(lambda r: httpx.Response(status, text="nope")).complete("s", "u", json_output=False, max_tokens=10)


def test_timeout_is_retried_once_then_unavailable():
    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMUnavailableError):
        make_client(handler).complete("s", "u", json_output=False, max_tokens=10)
    assert len(attempts) == 3


def test_transient_server_error_recovers_on_retry(monkeypatch):
    monkeypatch.setattr("app.llm.openai_compatible.time.sleep", lambda s: None)
    responses = [httpx.Response(503, text="busy"), ok("hello")]
    assert make_client(lambda r: responses.pop(0)).complete("s", "u", json_output=False, max_tokens=10) == "hello"


def test_missing_key_is_reported_by_settings(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    llm = Settings(_env_file=None, llm_provider="nvidia").llm()
    assert llm.base_url == "https://integrate.api.nvidia.com/v1"
    assert "LLM_API_KEY" in llm.problem
    with pytest.raises(LLMConfigurationError):
        OpenAICompatibleClient(llm)


def test_unknown_provider_is_reported():
    assert "Unknown LLM_PROVIDER" in Settings(_env_file=None, llm_provider="foo", llm_api_key="k").llm().problem


def test_invalid_extra_body_is_reported():
    llm = Settings(_env_file=None, llm_provider="nvidia", llm_api_key="k", llm_extra_body="{not json").llm()
    assert "LLM_EXTRA_BODY" in llm.problem


def test_nvidia_preset_disables_thinking_unless_overridden(monkeypatch):
    monkeypatch.delenv("LLM_EXTRA_BODY", raising=False)
    default = Settings(_env_file=None, llm_provider="nvidia", llm_api_key="k").llm()
    assert default.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}
    custom = Settings(_env_file=None, llm_provider="nvidia", llm_api_key="k", llm_extra_body='{"x": 1}').llm()
    assert custom.extra_body == {"x": 1}


def test_reasoning_echoed_into_content_is_stripped():
    thought = "Okay, the user said hi. I should reply with JSON."
    body = {"choices": [{"message": {"content": thought + '\n\n{"updates": {}}', "reasoning_content": thought}}]}
    client = make_client(lambda r: httpx.Response(200, json=body))
    assert client.complete("s", "u", json_output=True, max_tokens=10) == '{"updates": {}}'

    tagged = {"choices": [{"message": {"content": '<think>hmm</think>\n{"a": 1}'}}]}
    assert make_client(lambda r: httpx.Response(200, json=tagged)).complete("s", "u", json_output=True, max_tokens=10) == '{"a": 1}'
