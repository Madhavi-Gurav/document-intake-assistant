"""Request/response contract for the model interaction, plus tolerant parsing of raw output.

The model never writes to state directly. It proposes `FieldUpdate`s in the JSON
shape below; the parser turns raw text into typed proposals, dropping (and
reporting) anything that does not fit, and domain validation decides what is applied.

Response shape the model must return:

    {
      "updates": {
        "<field name>": {
          "value": <typed value or null>,
          "status": "confirmed" | "unconfirmed",
          "evidence": "<verbatim quote from the user's latest message>",
          "note": "<what is unclear, when unconfirmed>" | null
        }
      }
    }
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from app.domain.state import FIELD_ORDER, Gift, WishesState
from app.domain.updates import FieldUpdate, RejectedUpdate


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ExtractionRequest(BaseModel):
    """Everything the model needs to interpret the user's latest message."""

    current_state: dict[str, Any]
    recent_messages: list[ChatMessage]
    latest_user_message: str
    last_question_field: str | None


class ReplyRequest(BaseModel):
    """Context for phrasing the assistant's next turn. The application decides what to ask."""

    changes: list[dict[str, Any]]
    rejected: list[RejectedUpdate]
    next_field: str | None
    next_question: str | None
    next_field_note: str | None
    is_complete: bool
    latest_user_message: str


class MalformedResponseError(ValueError):
    """The model's output could not be interpreted as an extraction response at all."""


_VALUE_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "full_name": TypeAdapter(str),
    "home_address": TypeAdapter(str),
    "covers_worldwide_assets": TypeAdapter(bool),
    "has_children": TypeAdapter(bool),
    "children": TypeAdapter(list[str]),
    "executor_name": TypeAdapter(str),
    "executor_relationship": TypeAdapter(str),
    "specific_gifts": TypeAdapter(list[Gift]),
    "additional_wishes": TypeAdapter(list[str]),
}
assert set(_VALUE_ADAPTERS) == set(WishesState.model_fields)

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _load_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Some models wrap JSON in prose; take the outermost object if there is one.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise MalformedResponseError("response is not JSON") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise MalformedResponseError(f"response is not valid JSON: {exc.msg}") from None
    if not isinstance(data, dict):
        raise MalformedResponseError("response JSON is not an object")
    return data


def parse_extraction(raw: str) -> tuple[list[FieldUpdate], list[RejectedUpdate]]:
    """Parse raw model output. Structural failure raises; per-field problems are rejected individually,
    so one badly typed field doesn't throw away the rest of a valid answer."""
    data = _load_json_object(raw)
    updates = data.get("updates")
    if not isinstance(updates, dict):
        raise MalformedResponseError("response has no 'updates' object")

    accepted: list[FieldUpdate] = []
    rejected: list[RejectedUpdate] = []
    for name, proposal in updates.items():
        if name not in _VALUE_ADAPTERS:
            rejected.append(RejectedUpdate(field=str(name), reason="unknown field", proposed=proposal))
            continue
        if proposal is None:
            continue  # model explicitly says "nothing for this field"
        if not isinstance(proposal, dict):
            rejected.append(RejectedUpdate(field=name, reason="update is not an object", proposed=proposal))
            continue
        try:
            update = FieldUpdate(field=name, **{k: v for k, v in proposal.items() if k != "field"})
        except (ValidationError, TypeError) as exc:
            rejected.append(RejectedUpdate(field=name, reason=f"invalid update: {_first_error(exc)}", proposed=proposal))
            continue
        if update.value is not None:
            try:
                update.value = _VALUE_ADAPTERS[name].validate_python(update.value, strict=True)
            except ValidationError as exc:
                rejected.append(RejectedUpdate(field=name, reason=f"wrong value type: {_first_error(exc)}", proposed=proposal))
                continue
        elif update.status == "confirmed":
            rejected.append(RejectedUpdate(field=name, reason="confirmed update has no value", proposed=proposal))
            continue
        accepted.append(update)

    accepted.sort(key=lambda u: FIELD_ORDER.index(u.field))
    return accepted, rejected


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        err = exc.errors()[0]
        loc = ".".join(str(p) for p in err["loc"])
        return f"{loc}: {err['msg']}" if loc else err["msg"]
    return str(exc)
