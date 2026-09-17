import pytest

from app.domain.state import Gift
from app.llm.contract import MalformedResponseError, parse_extraction
from tests.conftest import fixture


def test_valid_multi_field_response_is_parsed_in_field_order():
    updates, rejected = parse_extraction(fixture("valid_multi_field.json"))
    assert rejected == []
    assert [u.field for u in updates] == ["full_name", "home_address", "has_children", "children"]
    assert updates[2].value is False


def test_code_fenced_json_is_accepted():
    updates, _ = parse_extraction(fixture("fenced_valid.txt"))
    assert updates[0].value == "Jane Smith"


def test_gifts_are_typed():
    updates, _ = parse_extraction(fixture("gifts_and_wishes_none.json"))
    assert updates[0].value == [Gift(item="my watch", recipient="Tom")]
    assert updates[1].value == []


@pytest.mark.parametrize("name", ["malformed_prose.txt", "malformed_truncated.txt", "malformed_no_updates.json"])
def test_structurally_malformed_responses_raise(name):
    with pytest.raises(MalformedResponseError):
        parse_extraction(fixture(name))


def test_bad_fields_are_rejected_individually_without_losing_good_ones():
    updates, rejected = parse_extraction(fixture("wrong_types.json"))
    assert [u.field for u in updates] == ["children"]
    reasons = {r.field: r.reason for r in rejected}
    assert "wrong value type" in reasons["has_children"]  # "yes" is not a boolean
    assert reasons["favourite_colour"] == "unknown field"
    assert "invalid update" in reasons["executor_name"]  # bad status literal


def test_confirmed_update_without_value_is_rejected():
    raw = '{"updates": {"full_name": {"value": null, "status": "confirmed", "evidence": "x"}}}'
    updates, rejected = parse_extraction(raw)
    assert updates == [] and rejected[0].reason == "confirmed update has no value"
