from app.documents.generator import DISCLAIMER, generate_document
from app.domain.state import FieldStatus, Gift, TrackedField, WishesState


def confirmed(value):
    return {"value": value, "status": FieldStatus.CONFIRMED}


def complete_state(**overrides) -> WishesState:
    data = {
        "full_name": confirmed("Jane Smith"),
        "home_address": confirmed("12 Oak Lane, Leeds"),
        "covers_worldwide_assets": confirmed(True),
        "has_children": confirmed(True),
        "children": confirmed(["Tom", "Amy"]),
        "executor_name": confirmed("James"),
        "executor_relationship": confirmed("brother"),
        "specific_gifts": confirmed([Gift(item="my watch", recipient="Tom")]),
        "additional_wishes": confirmed([]),
    }
    data.update(overrides)
    return WishesState(**data)


def test_document_is_labelled_fictional_and_not_legal_advice():
    doc = generate_document(WishesState())
    assert "FICTIONAL" in doc.disclaimer and "NOT LEGAL ADVICE" in doc.disclaimer
    assert DISCLAIMER in doc.markdown


def test_complete_state_renders_all_facts():
    doc = generate_document(complete_state())
    text = doc.markdown
    assert doc.is_complete and doc.outstanding == []
    for expected in ["I, Jane Smith, of 12 Oak Lane, Leeds", "all of my assets worldwide", "Tom and Amy",
                     "I appoint James (relationship to me: brother)", "I give my watch to Tom.", "no additional wishes"]:
        assert expected in text


def test_unconfirmed_and_missing_values_are_never_rendered_as_fact():
    state = complete_state(
        full_name=TrackedField[str](value="Jane", status=FieldStatus.UNCONFIRMED, note="Surname?"),
        executor_name=TrackedField[str](),
    )
    doc = generate_document(state)
    assert "Jane," not in doc.markdown
    assert "[awaiting confirmation]" in doc.markdown and "[not yet provided]" in doc.markdown
    assert not doc.is_complete
    assert doc.outstanding == ["Full name", "Executor's name"]


def test_no_children_is_stated_and_names_not_required():
    doc = generate_document(complete_state(has_children=confirmed(False), children=TrackedField[list[str]]()))
    assert "I have no children." in doc.markdown
    assert doc.is_complete
