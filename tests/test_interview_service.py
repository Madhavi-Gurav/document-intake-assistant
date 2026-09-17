import json

import pytest

from app.domain.state import FieldStatus
from app.llm.base import LLMConfigurationError, LLMUnavailableError
from app.services.interview import InterviewService, TurnError
from app.services.sessions import SessionStore
from tests.conftest import ScriptedLLM, fixture


def test_multi_field_answer_updates_state_and_moves_to_next_question(make_service):
    llm = ScriptedLLM([fixture("valid_multi_field.json")], ["Thanks Jane! Should this cover assets worldwide?"])
    service = make_service(llm)
    session = service.start_session()

    session, turn = service.handle_message(session.id, "I'm Jane Smith, I live at 12 Oak Lane, Leeds LS1 4AB and have no children")

    assert session.state.full_name.value == "Jane Smith"
    assert session.state.home_address.status == FieldStatus.CONFIRMED
    assert session.asked_field == "covers_worldwide_assets"
    assert turn.reply_source == "llm"
    assert [m.role for m in session.messages] == ["assistant", "user", "assistant"]

    # The reply prompt is told to ask the next missing field only.
    reply_payload = json.loads(llm.calls[-1]["user"])
    assert reply_payload["next_field"] == "covers_worldwide_assets"


def test_extraction_request_carries_state_and_last_question(make_service):
    llm = ScriptedLLM([fixture("valid_multi_field.json"), fixture("executor_brother.json")])
    service = make_service(llm)
    sid = service.start_session().id
    service.handle_message(sid, "I'm Jane Smith, I live at 12 Oak Lane, Leeds LS1 4AB and have no children")
    service.handle_message(sid, "My brother James")

    second = json.loads(llm.extraction_calls()[1]["user"])
    assert second["current_state"]["full_name"]["status"] == "confirmed"
    assert second["last_question_field"] == "covers_worldwide_assets"
    assert second["recent_messages"][-1] == {"role": "user", "content": "My brother James"}


def test_correction_through_conversation(make_service):
    service = make_service(ScriptedLLM([fixture("executor_brother.json"), fixture("correction_executor.json")]))
    sid = service.start_session().id
    service.handle_message(sid, "My brother James")
    session, turn = service.handle_message(sid, "Sorry, my executor should be my friend Sarah Jones")

    assert session.state.executor_name.value == "Sarah Jones"
    assert session.state.executor_relationship.value == "friend"
    assert {c.kind for c in turn.changes} == {"corrected"}
    assert "updated" in turn.reply  # template fallback acknowledges the correction


def test_ambiguous_answer_is_stored_unconfirmed_and_followed_up(make_service):
    service = make_service(ScriptedLLM([fixture("ambiguous_first_name.json")]))
    sid = service.start_session().id
    session, turn = service.handle_message(sid, "Jane")

    assert session.state.full_name.status == FieldStatus.UNCONFIRMED
    assert session.asked_field == "full_name"
    assert "surname" in turn.reply  # template uses the note as the follow-up question


def test_invented_fact_is_not_saved(make_service):
    service = make_service(ScriptedLLM([fixture("invented_surname.json")]))
    sid = service.start_session().id
    session, turn = service.handle_message(sid, "My brother James")

    assert session.state.executor_name.status == FieldStatus.MISSING
    assert session.state.executor_relationship.value == "brother"
    assert [r.field for r in turn.rejected] == ["executor_name"]


def test_contradiction_is_raised_with_user(make_service):
    service = make_service(ScriptedLLM([fixture("contradiction_children.json")]))
    sid = service.start_session().id
    session, turn = service.handle_message(sid, "I don't have children, but leave my watch to my son Tom")

    assert session.asked_field == "has_children"
    assert "Tom" in turn.reply


def test_malformed_response_is_retried_once_then_succeeds(make_service):
    llm = ScriptedLLM([fixture("malformed_prose.txt"), fixture("valid_multi_field.json")])
    service = make_service(llm)
    sid = service.start_session().id
    session, _ = service.handle_message(sid, "I'm Jane Smith, I live at 12 Oak Lane, Leeds LS1 4AB and have no children")

    assert session.state.full_name.value == "Jane Smith"
    assert len(llm.extraction_calls()) == 2
    assert "could not be used" in llm.extraction_calls()[1]["user"]


def test_repeated_malformed_response_commits_nothing(make_service):
    service = make_service(ScriptedLLM([fixture("malformed_prose.txt"), fixture("malformed_truncated.txt")]))
    sid = service.start_session().id

    with pytest.raises(TurnError) as err:
        service.handle_message(sid, "I'm Jane Smith")
    assert err.value.code == "malformed_model_response"

    session = service.get_session(sid)
    assert len(session.messages) == 1  # only the greeting; user can resend
    assert session.state.full_name.status == FieldStatus.MISSING


def test_provider_outage_commits_nothing(make_service):
    service = make_service(ScriptedLLM([LLMUnavailableError("503 from provider")]))
    sid = service.start_session().id
    with pytest.raises(TurnError) as err:
        service.handle_message(sid, "I'm Jane Smith")
    assert err.value.code == "llm_unavailable"
    assert len(service.get_session(sid).messages) == 1


def test_reply_failure_falls_back_to_template_without_losing_the_turn(make_service):
    llm = ScriptedLLM([fixture("executor_brother.json")], [LLMUnavailableError("timeout")])
    service = make_service(llm)
    sid = service.start_session().id
    session, turn = service.handle_message(sid, "My brother James")
    assert turn.reply_source == "template"
    assert session.state.executor_name.value == "James"


def test_missing_configuration_raises_configuration_error():
    service = InterviewService(SessionStore(), interviewer=None, config_problem="LLM_API_KEY is not set")
    sid = service.start_session().id
    with pytest.raises(LLMConfigurationError, match="LLM_API_KEY"):
        service.handle_message(sid, "hello")


def test_vague_answer_without_usable_value_is_explicitly_unconfirmed(make_service):
    service = make_service(ScriptedLLM([fixture("ambiguous_worldwide.json")]))
    sid = service.start_session().id
    session, turn = service.handle_message(sid, "Hmm, not sure, I have a flat in Spain")

    field = session.state.covers_worldwide_assets
    assert field.value is None and field.status == FieldStatus.UNCONFIRMED
    assert session.asked_field == "covers_worldwide_assets"
    assert "Spain" in turn.reply
