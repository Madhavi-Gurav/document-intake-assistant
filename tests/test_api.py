from fastapi.testclient import TestClient

from app.config import LLMSettings
from app.llm.base import LLMUnavailableError
from app.main import create_app
from tests.conftest import TEST_LLM_SETTINGS, ScriptedLLM, fixture


def client_with(llm: ScriptedLLM) -> TestClient:
    return TestClient(create_app(llm_settings=TEST_LLM_SETTINGS, llm_client=llm))


def test_session_lifecycle_and_contract():
    client = client_with(ScriptedLLM([fixture("executor_brother.json")], ["Noted. What is your full name?"]))

    created = client.post("/api/sessions")
    assert created.status_code == 201
    body = created.json()
    assert body["messages"][0]["role"] == "assistant"
    assert body["progress"] == {"confirmed": 0, "required": 8, "outstanding": body["progress"]["outstanding"], "next_field": "full_name"}
    assert body["confirmed_state"]["executor"] == {"name": None, "relationship": None}
    assert "NOT LEGAL ADVICE" in body["document"]["disclaimer"]

    sid = body["session_id"]
    sent = client.post(f"/api/sessions/{sid}/messages", json={"message": "My brother James"})
    assert sent.status_code == 200
    data = sent.json()
    assert data["turn"]["reply"] == "Noted. What is your full name?"
    assert data["session"]["confirmed_state"]["executor"] == {"name": "James", "relationship": "brother"}
    assert data["session"]["state"]["executor_name"]["status"] == "confirmed"

    fetched = client.get(f"/api/sessions/{sid}").json()
    assert fetched == data["session"]


def test_unknown_session_returns_404_error_body():
    res = client_with(ScriptedLLM()).post("/api/sessions/nope/messages", json={"message": "hi"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "session_not_found"


def test_empty_message_is_rejected():
    client = client_with(ScriptedLLM())
    sid = client.post("/api/sessions").json()["session_id"]
    res = client.post(f"/api/sessions/{sid}/messages", json={"message": ""})
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_request"


def test_upstream_failure_returns_502_and_keeps_session_unchanged():
    client = client_with(ScriptedLLM([LLMUnavailableError("boom")]))
    sid = client.post("/api/sessions").json()["session_id"]
    res = client.post(f"/api/sessions/{sid}/messages", json={"message": "I'm Jane"})
    assert res.status_code == 502 and res.json()["error"]["code"] == "llm_unavailable"
    assert len(client.get(f"/api/sessions/{sid}").json()["messages"]) == 1


def test_missing_api_key_starts_app_but_returns_503_on_message():
    settings = LLMSettings(
        provider="gemini", label="Google AI Studio", base_url="https://example", model="m",
        api_key=None, timeout_seconds=1, extra_body={}, problem="LLM_API_KEY is not set.",
    )
    client = TestClient(create_app(llm_settings=settings))
    health = client.get("/api/health").json()
    assert health["llm_configured"] is False and "LLM_API_KEY" in health["llm_problem"]

    sid = client.post("/api/sessions").json()["session_id"]
    res = client.post(f"/api/sessions/{sid}/messages", json={"message": "hi"})
    assert res.status_code == 503 and res.json()["error"]["code"] == "llm_not_configured"


def test_frontend_is_served():
    client = client_with(ScriptedLLM())
    assert "Document Intake Assistant" in client.get("/").text
