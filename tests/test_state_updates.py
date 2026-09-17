from app.domain.state import FieldStatus, WishesState, confirmed_view, outstanding_fields
from app.domain.updates import FieldUpdate, apply_updates, validate_updates


def upd(field, value, evidence, status="confirmed", note=None):
    return FieldUpdate(field=field, value=value, status=status, evidence=evidence, note=note)


# ---- validation -----------------------------------------------------------

def test_invented_surname_is_rejected():
    msg = "My brother James"
    accepted, rejected = validate_updates(
        [upd("executor_name", "James Smith", msg), upd("executor_relationship", "brother", msg)], msg, msg
    )
    assert [u.field for u in accepted] == ["executor_relationship"]
    assert "smith" in rejected[0].reason


def test_surname_mentioned_earlier_in_conversation_is_allowed():
    accepted, rejected = validate_updates(
        [upd("full_name", "Jane Smith", "yes")], "yes", all_user_text="I'm Jane. Smith is my surname. yes"
    )
    assert rejected == [] and accepted[0].value == "Jane Smith"


def test_evidence_must_come_from_latest_message():
    accepted, rejected = validate_updates([upd("has_children", True, "I have two kids")], "no", "no")
    assert accepted == [] and "evidence" in rejected[0].reason


def test_evidence_match_ignores_case_and_punctuation():
    accepted, _ = validate_updates([upd("has_children", False, "NO children.")], "I have no children!", "")
    assert len(accepted) == 1


def test_empty_list_requires_the_user_to_say_none():
    msg = "my watch to Tom and my car to Sarah"
    accepted, rejected = validate_updates([upd("additional_wishes", [], "my watch to Tom")], msg, msg)
    assert accepted == [] and "none" in rejected[0].reason

    for said in ["none", "No, nothing else", "I don't have any", "I haven't got children"]:
        accepted, _ = validate_updates([upd("additional_wishes", [], said)], said, said)
        assert len(accepted) == 1, said


def test_overlong_text_is_rejected():
    long = "x" * 400
    _, rejected = validate_updates([upd("executor_relationship", long, long)], long, long)
    assert "longer than" in rejected[0].reason


def test_unconfirmed_update_without_note_gets_a_default_question():
    accepted, _ = validate_updates([upd("home_address", "Leeds", "Leeds", status="unconfirmed")], "Leeds", "Leeds")
    assert accepted[0].note


# ---- applying -------------------------------------------------------------

def test_multi_field_update_and_outstanding_order():
    state, changes = apply_updates(
        WishesState(),
        [upd("full_name", "Jane Smith", "Jane Smith"), upd("has_children", False, "no"), upd("children", [], "no")],
    )
    assert {c.field for c in changes} == {"full_name", "has_children", "children"}
    assert all(c.kind == "added" for c in changes)
    # children not required when has_children is false; captured fields are never asked again
    assert outstanding_fields(state) == [
        "home_address", "covers_worldwide_assets", "executor_name", "executor_relationship",
        "specific_gifts", "additional_wishes",
    ]


def test_correction_replaces_value_and_is_recorded():
    state, _ = apply_updates(WishesState(), [upd("executor_name", "James", "James")])
    state, changes = apply_updates(state, [upd("executor_name", "Sarah", "Sarah")])
    assert state.executor_name.value == "Sarah"
    assert changes[0].kind == "corrected" and changes[0].old_value == "James"


def test_repeating_same_value_is_a_no_op():
    state, _ = apply_updates(WishesState(), [upd("full_name", "Jane Smith", "Jane Smith")])
    _, changes = apply_updates(state, [upd("full_name", "Jane Smith", "Jane Smith")])
    assert changes == []


def test_confirming_an_unconfirmed_value():
    state, _ = apply_updates(WishesState(), [upd("full_name", "Jane", "Jane", status="unconfirmed", note="Surname?")])
    assert outstanding_fields(state)[0] == "full_name"
    state, changes = apply_updates(state, [upd("full_name", "Jane", "yes")])
    assert state.full_name.status == FieldStatus.CONFIRMED and state.full_name.note is None
    assert changes[0].kind == "confirmed"


def test_unconfirmed_values_are_excluded_from_confirmed_view():
    state, _ = apply_updates(WishesState(), [upd("full_name", "Jane", "Jane", status="unconfirmed", note="?")])
    assert confirmed_view(state)["full_name"] is None


# ---- consistency rules ----------------------------------------------------

def test_naming_children_implies_has_children():
    state, changes = apply_updates(WishesState(), [upd("children", ["Tom", "Amy"], "Tom and Amy")])
    assert state.has_children.value is True and state.has_children.status == FieldStatus.CONFIRMED
    assert "has_children" in {c.field for c in changes}


def test_contradiction_between_no_children_and_named_child_is_flagged():
    state, _ = apply_updates(WishesState(), [upd("has_children", False, "no children"), upd("children", ["Tom"], "Tom")])
    assert state.has_children.status == FieldStatus.UNCONFIRMED
    assert "Tom" in state.has_children.note
    assert outstanding_fields(state)[0] == "has_children"


def test_later_correction_to_no_children_clears_stale_names():
    state, _ = apply_updates(WishesState(), [upd("has_children", True, "yes"), upd("children", ["Tom"], "Tom")])
    state, changes = apply_updates(state, [upd("has_children", False, "actually no children")])
    assert state.children.value == [] and state.has_children.status == FieldStatus.CONFIRMED
    assert any(c.field == "children" and c.kind == "corrected" for c in changes)


def test_has_children_with_empty_names_is_flagged():
    state, _ = apply_updates(WishesState(), [upd("has_children", True, "yes"), upd("children", [], "none")])
    assert state.children.status == FieldStatus.UNCONFIRMED
