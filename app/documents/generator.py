"""Deterministic document generation from structured state.

The document is rendered by code, not by the LLM, so it can only ever contain
confirmed values and always matches the preview.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.domain.state import FIELD_LABELS, FieldStatus, Gift, WishesState, outstanding_fields

DOCUMENT_TITLE = "Personal Wishes Document (Draft)"
DISCLAIMER = (
    "FICTIONAL DOCUMENT — NOT LEGAL ADVICE. This draft was generated for a software exercise. "
    "It is not a will, has no legal effect and must not be relied upon."
)
MISSING = "[not yet provided]"
UNCONFIRMED = "[awaiting confirmation]"


class DocumentSection(BaseModel):
    heading: str
    paragraphs: list[str]


class DraftDocument(BaseModel):
    title: str
    disclaimer: str
    is_complete: bool
    outstanding: list[str]
    sections: list[DocumentSection]
    markdown: str


def _text(state: WishesState, name: str) -> str:
    f = state.field(name)
    if f.status == FieldStatus.CONFIRMED and f.value is not None:
        return str(f.value)
    return UNCONFIRMED if f.status == FieldStatus.UNCONFIRMED else MISSING


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def generate_document(state: WishesState) -> DraftDocument:
    name = _text(state, "full_name")
    sections: list[DocumentSection] = []

    sections.append(
        DocumentSection(
            heading="1. Declaration",
            paragraphs=[f"I, {name}, of {_text(state, 'home_address')}, set out my personal wishes in this document."],
        )
    )

    scope = state.covers_worldwide_assets
    if scope.status == FieldStatus.CONFIRMED:
        scope_text = (
            "This document is intended to cover all of my assets worldwide."
            if scope.value
            else "This document is not intended to cover my assets worldwide; it applies only to assets in the "
            "countries I have specified elsewhere."
        )
    else:
        scope_text = f"Whether this document covers my assets worldwide: {_text(state, 'covers_worldwide_assets')}"
    sections.append(DocumentSection(heading="2. Scope", paragraphs=[scope_text]))

    has, kids = state.has_children, state.children
    if has.status == FieldStatus.CONFIRMED and has.value is False:
        family = "I have no children."
    elif has.status == FieldStatus.CONFIRMED and has.value is True:
        if kids.status == FieldStatus.CONFIRMED and kids.value:
            family = f"I have {len(kids.value)} {'child' if len(kids.value) == 1 else 'children'}: {_join(kids.value)}."
        else:
            family = f"I have children. Their names: {_text(state, 'children')}"
    else:
        family = f"Children: {_text(state, 'has_children')}"
    sections.append(DocumentSection(heading="3. Family", paragraphs=[family]))

    relationship = _text(state, "executor_relationship")
    sections.append(
        DocumentSection(
            heading="4. Executor",
            paragraphs=[
                f"I appoint {_text(state, 'executor_name')} (relationship to me: {relationship}) as the executor "
                "of these wishes."
            ],
        )
    )

    gifts = state.specific_gifts
    if gifts.status == FieldStatus.CONFIRMED:
        gift_list: list[Gift] = gifts.value or []
        gift_paras = [f"I give {g.item} to {g.recipient}." for g in gift_list] or ["I make no specific gifts."]
    else:
        gift_paras = [f"Specific gifts: {_text(state, 'specific_gifts')}"]
    sections.append(DocumentSection(heading="5. Specific gifts", paragraphs=gift_paras))

    wishes = state.additional_wishes
    if wishes.status == FieldStatus.CONFIRMED:
        wish_paras = list(wishes.value or []) or ["I have no additional wishes."]
    else:
        wish_paras = [f"Additional wishes: {_text(state, 'additional_wishes')}"]
    sections.append(DocumentSection(heading="6. Additional wishes", paragraphs=wish_paras))

    sections.append(
        DocumentSection(heading="Signature", paragraphs=[f"Signed: ______________________  ({name})", "Date: ____________"])
    )

    outstanding = [FIELD_LABELS[f] for f in outstanding_fields(state)]
    md_lines = [f"# {DOCUMENT_TITLE}", "", f"> **{DISCLAIMER}**", ""]
    if outstanding:
        md_lines += [f"_Incomplete draft — outstanding: {', '.join(outstanding)}._", ""]
    for s in sections:
        md_lines += [f"## {s.heading}", "", *[p + "\n" for p in s.paragraphs]]

    return DraftDocument(
        title=DOCUMENT_TITLE,
        disclaimer=DISCLAIMER,
        is_complete=not outstanding,
        outstanding=outstanding,
        sections=sections,
        markdown="\n".join(md_lines).rstrip() + "\n",
    )
