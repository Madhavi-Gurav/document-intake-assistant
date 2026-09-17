"""Structured state for the Personal Wishes interview.

This model — not the chat transcript — is the application's source of truth.
Every field carries an explicit status so unknown and unconfirmed values are
never confused with real answers.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class FieldStatus(str, Enum):
    MISSING = "missing"  # nothing supplied yet
    UNCONFIRMED = "unconfirmed"  # supplied but unclear or contradictory; needs a follow-up
    CONFIRMED = "confirmed"  # clearly stated by the user


class Gift(BaseModel):
    item: str
    recipient: str


class TrackedField(BaseModel, Generic[T]):
    value: T | None = None
    status: FieldStatus = FieldStatus.MISSING
    note: str | None = None  # why a value is unconfirmed, shown to the user and the LLM
    evidence: str | None = None  # the user's words that support the value


class WishesState(BaseModel):
    full_name: TrackedField[str] = Field(default_factory=TrackedField[str])
    home_address: TrackedField[str] = Field(default_factory=TrackedField[str])
    covers_worldwide_assets: TrackedField[bool] = Field(default_factory=TrackedField[bool])
    has_children: TrackedField[bool] = Field(default_factory=TrackedField[bool])
    children: TrackedField[list[str]] = Field(default_factory=TrackedField[list[str]])
    executor_name: TrackedField[str] = Field(default_factory=TrackedField[str])
    executor_relationship: TrackedField[str] = Field(default_factory=TrackedField[str])
    specific_gifts: TrackedField[list[Gift]] = Field(default_factory=TrackedField[list[Gift]])
    additional_wishes: TrackedField[list[str]] = Field(default_factory=TrackedField[list[str]])

    def field(self, name: str) -> TrackedField[Any]:
        return getattr(self, name)


# Interview order. Also the order fields are listed in prompts and the UI.
FIELD_ORDER: list[str] = list(WishesState.model_fields)

FIELD_LABELS: dict[str, str] = {
    "full_name": "Full name",
    "home_address": "Home address",
    "covers_worldwide_assets": "Covers worldwide assets",
    "has_children": "Has children",
    "children": "Children's names",
    "executor_name": "Executor's name",
    "executor_relationship": "Executor's relationship to you",
    "specific_gifts": "Specific gifts",
    "additional_wishes": "Additional wishes",
}

FIELD_DESCRIPTIONS: dict[str, str] = {
    "full_name": "string. The user's own full legal name.",
    "home_address": "string. The user's full home address.",
    "covers_worldwide_assets": "boolean. true if the document covers assets worldwide, false if only some countries.",
    "has_children": "boolean. Whether the user has children.",
    "children": "array of strings. Names of the user's children. Only relevant if has_children is true.",
    "executor_name": "string. Name of the person appointed as executor.",
    "executor_relationship": "string. Executor's relationship to the user, e.g. 'brother', 'friend', 'solicitor'.",
    "specific_gifts": "array of {item, recipient}. [] if the user confirms there are none.",
    "additional_wishes": "array of strings. [] if the user confirms there are none.",
}

QUESTIONS: dict[str, str] = {
    "full_name": "To start, what is your full name?",
    "home_address": "What is your home address?",
    "covers_worldwide_assets": "Should this document cover your assets worldwide, or only in certain countries?",
    "has_children": "Do you have any children?",
    "children": "What are your children's names?",
    "executor_name": "Who would you like to appoint as your executor?",
    "executor_relationship": "What is your executor's relationship to you?",
    "specific_gifts": "Are there any specific gifts you'd like to leave to particular people? If not, just say none.",
    "additional_wishes": "Finally, do you have any additional wishes to include? If not, just say none.",
}


def is_required(state: WishesState, name: str) -> bool:
    """Children's names are only needed once the user has said they have children."""
    if name == "children":
        return state.has_children.value is True
    return True


def outstanding_fields(state: WishesState) -> list[str]:
    """Fields still needing input: unconfirmed ones first (resolve doubt before moving on), then missing."""
    unconfirmed = [
        n for n in FIELD_ORDER if is_required(state, n) and state.field(n).status == FieldStatus.UNCONFIRMED
    ]
    missing = [n for n in FIELD_ORDER if is_required(state, n) and state.field(n).status == FieldStatus.MISSING]
    return unconfirmed + missing


def confirmed_view(state: WishesState) -> dict[str, Any]:
    """Only confirmed values, in the nested shape from the brief. Unknowns are explicit nulls."""

    def v(name: str) -> Any:
        f = state.field(name)
        if f.status != FieldStatus.CONFIRMED:
            return None
        if isinstance(f.value, list):
            return [x.model_dump() if isinstance(x, BaseModel) else x for x in f.value]
        return f.value

    no_children = state.has_children.status == FieldStatus.CONFIRMED and state.has_children.value is False
    return {
        "full_name": v("full_name"),
        "home_address": v("home_address"),
        "covers_worldwide_assets": v("covers_worldwide_assets"),
        "has_children": v("has_children"),
        "children": [] if no_children else v("children"),
        "executor": {"name": v("executor_name"), "relationship": v("executor_relationship")},
        "specific_gifts": v("specific_gifts"),
        "additional_wishes": v("additional_wishes"),
    }
