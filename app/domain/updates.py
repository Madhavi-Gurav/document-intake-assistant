"""Validating proposed updates and applying them to the structured state.

Pure functions with no LLM or HTTP dependencies, so the rules that protect the
state are easy to test on their own.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel

from app.domain.state import FIELD_LABELS, FieldStatus, TrackedField, WishesState

MAX_TEXT_LENGTH = 300
MAX_LIST_ITEMS = 20

# Values the user must have literally said: a model "helpfully" completing a surname
# or postcode is exactly the kind of invented fact we must not store.
VERBATIM_FIELDS = {"full_name", "home_address", "children", "executor_name"}

# "None" answers must actually be negative. Seen live: a model recorded additional_wishes=[]
# from "my watch to Tom and my car to Sarah", quoting genuine words that didn't say "none".
LIST_FIELDS = {"children", "specific_gifts", "additional_wishes"}
_NEGATION = re.compile(r"\b(no|none|nothing|not|nope|nil|zero|neither|nor|without|dont)\b|n['’]t\b", re.IGNORECASE)

# Words that describe someone as the user's own child. Used to catch a contradiction spread
# across fields: "no children", then an executor or gift recipient described as a son or
# daughter. Word boundaries exclude "grandson" and "stepdaughter"; the lookahead excludes
# "son-in-law" and "daughter in law", who are not the user's children.
_CHILD_RELATION = re.compile(
    r"\b(son|daughter|child|children|kid|kids)\b(?!\s*[-\s]in[-\s]law)", re.IGNORECASE
)


class FieldUpdate(BaseModel):
    field: str
    value: Any
    status: Literal["confirmed", "unconfirmed"]
    evidence: str
    note: str | None = None


class RejectedUpdate(BaseModel):
    field: str
    reason: str
    proposed: Any = None


class AppliedChange(BaseModel):
    field: str
    kind: Literal["added", "corrected", "flagged", "confirmed"]
    old_value: Any = None
    new_value: Any = None
    status: FieldStatus
    note: str | None = None


def _normalise(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def _text_values(update: FieldUpdate) -> list[str]:
    value = update.value
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str):
        return [value]
    out: list[str] = []
    for item in value:
        out.extend([item.item, item.recipient] if hasattr(item, "item") else [item])
    return out


def validate_updates(
    updates: list[FieldUpdate], latest_user_message: str, all_user_text: str
) -> tuple[list[FieldUpdate], list[RejectedUpdate]]:
    """Accept only updates grounded in what the user actually said."""
    accepted: list[FieldUpdate] = []
    rejected: list[RejectedUpdate] = []
    latest = _normalise(latest_user_message)
    known_tokens = _tokens(all_user_text)

    def reject(u: FieldUpdate, reason: str) -> None:
        rejected.append(RejectedUpdate(field=u.field, reason=reason, proposed=u.model_dump(mode="json")))

    for u in updates:
        evidence = _normalise(u.evidence or "")
        if not evidence:
            reject(u, "no supporting quote from the user")
            continue
        if evidence not in latest:
            reject(u, "quoted evidence does not appear in the user's message")
            continue

        if u.field in LIST_FIELDS and u.value == [] and u.status == "confirmed" and not _NEGATION.search(u.evidence):
            reject(u, "an empty answer needs the user to have said none")
            continue

        texts = [t.strip() for t in _text_values(u)]
        if isinstance(u.value, list) and len(u.value) > MAX_LIST_ITEMS:
            reject(u, f"more than {MAX_LIST_ITEMS} items")
            continue
        if any(not t for t in texts):
            reject(u, "empty text value")
            continue
        if any(len(t) > MAX_TEXT_LENGTH for t in texts):
            reject(u, f"text longer than {MAX_TEXT_LENGTH} characters")
            continue
        if u.field in VERBATIM_FIELDS:
            invented = sorted({tok for t in texts for tok in _tokens(t)} - known_tokens)
            if invented:
                reject(u, f"value contains words the user never said: {', '.join(invented)}")
                continue
        if u.status == "unconfirmed" and not (u.note or "").strip():
            u = u.model_copy(update={"note": f"Please confirm your {FIELD_LABELS[u.field].lower()}."})
        accepted.append(u)
    return accepted, rejected


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _dump(value: Any) -> Any:
    if isinstance(value, list):
        return [v.model_dump() if isinstance(v, BaseModel) else v for v in value]
    return value


def apply_updates(state: WishesState, updates: list[FieldUpdate]) -> tuple[WishesState, list[AppliedChange]]:
    """Return a new state with validated updates applied, plus a record of what changed."""
    new_state = state.model_copy(deep=True)
    changes: list[AppliedChange] = []

    for u in updates:
        current: TrackedField[Any] = new_state.field(u.field)
        status = FieldStatus.CONFIRMED if u.status == "confirmed" else FieldStatus.UNCONFIRMED
        value = _clean(u.value) if u.value is not None else current.value
        same_value = _dump(value) == _dump(current.value)

        if same_value and status == current.status:
            continue  # nothing new; don't churn state or the transcript
        if current.status == FieldStatus.MISSING:
            kind = "added" if status == FieldStatus.CONFIRMED else "flagged"
        elif status == FieldStatus.UNCONFIRMED:
            kind = "flagged"
        elif same_value:
            kind = "confirmed"
        else:
            kind = "corrected"

        changes.append(
            AppliedChange(
                field=u.field,
                kind=kind,
                old_value=_dump(current.value),
                new_value=_dump(value),
                status=status,
                note=u.note if status == FieldStatus.UNCONFIRMED else None,
            )
        )
        setattr(
            new_state,
            u.field,
            type(current)(
                value=value,
                status=status,
                note=u.note if status == FieldStatus.UNCONFIRMED else None,
                evidence=u.evidence,
            ),
        )

    changes.extend(enforce_consistency(new_state, updated={u.field for u in updates}, previous=state))
    return new_state, changes


def _child_mentions(state: WishesState) -> list[str]:
    """People described as the user's child somewhere other than the children field.

    The extraction model reports relationships as the user phrased them, so "my son Tom" as
    executor arrives as executor_relationship="son" — which the children list never sees.
    """
    mentions: list[str] = []
    rel = state.executor_relationship
    if rel.status == FieldStatus.CONFIRMED and rel.value and _CHILD_RELATION.search(str(rel.value)):
        name = state.executor_name
        who = str(name.value) if name.status == FieldStatus.CONFIRMED and name.value else "your executor"
        mentions.append(f"{who} (your {rel.value})")

    gifts = state.specific_gifts
    if gifts.status == FieldStatus.CONFIRMED and gifts.value:
        mentions += [g.recipient for g in gifts.value if _CHILD_RELATION.search(g.recipient)]
    return mentions


def enforce_consistency(
    state: WishesState,
    updated: set[str] | frozenset[str] = frozenset(),
    previous: WishesState | None = None,
) -> list[AppliedChange]:
    """Cross-field rules. Mutates `state`; returns any changes it made.

    `previous` is the state before this turn's updates, used to tell a first answer apart from
    a re-answer so a contradiction is raised once rather than on every turn.
    """
    changes: list[AppliedChange] = []
    has, kids = state.has_children, state.children
    kid_names = kids.value or []

    # Correction "actually, I have no children" made after names were given: drop the stale names.
    if (
        "has_children" in updated
        and "children" not in updated
        and has.value is False
        and has.status == FieldStatus.CONFIRMED
        and kid_names
    ):
        state.children = TrackedField[list[str]](value=[], status=FieldStatus.CONFIRMED, evidence=has.evidence)
        changes.append(AppliedChange(field="children", kind="corrected", old_value=kid_names, new_value=[], status=FieldStatus.CONFIRMED))
        kids, kid_names = state.children, []

    # Naming children entails having children — record it rather than asking again.
    if kid_names and kids.status == FieldStatus.CONFIRMED and has.status == FieldStatus.MISSING:
        state.has_children = TrackedField[bool](value=True, status=FieldStatus.CONFIRMED, evidence=kids.evidence)
        changes.append(AppliedChange(field="has_children", kind="added", new_value=True, status=FieldStatus.CONFIRMED))

    # Contradiction: "no children" but children were named.
    elif kid_names and has.value is False and has.status == FieldStatus.CONFIRMED:
        note = (
            f"You said you have no children, but also mentioned {', '.join(kid_names)}. "
            "Do you have children?"
        )
        state.has_children = TrackedField[bool](value=False, status=FieldStatus.UNCONFIRMED, note=note, evidence=has.evidence)
        changes.append(AppliedChange(field="has_children", kind="flagged", old_value=False, new_value=False, status=FieldStatus.UNCONFIRMED, note=note))

    # Contradiction: has children, but confirmed an empty list of names.
    if state.has_children.value is True and kids.status == FieldStatus.CONFIRMED and not kid_names:
        note = "You said you have children, but no names were given. What are their names?"
        state.children = TrackedField[list[str]](value=[], status=FieldStatus.UNCONFIRMED, note=note, evidence=kids.evidence)
        changes.append(AppliedChange(field="children", kind="flagged", old_value=[], new_value=[], status=FieldStatus.UNCONFIRMED, note=note))

    # Contradiction spread across fields: "no children", but someone in the document is described
    # as the user's son or daughter. Raised when the child mention is new, or when the user is
    # answering "do you have children?" for the first time with a mention already on record.
    # A later re-answer is taken at face value, so the question is never asked twice.
    current_has = state.has_children
    if current_has.status == FieldStatus.CONFIRMED and current_has.value is False:
        mentions = _child_mentions(state)
        new_mention = bool(set(updated) & {"executor_relationship", "specific_gifts"})
        first_answer = "has_children" in updated and (
            previous is None or previous.has_children.status == FieldStatus.MISSING
        )
        if mentions and (new_mention or first_answer):
            note = (
                f"You said you have no children, but {' and '.join(mentions)} is described as your child. "
                "Do you have children?"
            )
            state.has_children = TrackedField[bool](
                value=False, status=FieldStatus.UNCONFIRMED, note=note, evidence=current_has.evidence
            )
            changes.append(
                AppliedChange(
                    field="has_children", kind="flagged", old_value=False, new_value=False,
                    status=FieldStatus.UNCONFIRMED, note=note,
                )
            )

    return changes
