"""Application logic for one conversational turn.

Flow: model proposes updates -> parse -> validate -> apply + consistency rules ->
application picks the next question -> model phrases the reply (template fallback).

A turn is atomic: if extraction fails, neither the user's message nor any state
change is committed, so the user can simply resend.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.domain.state import FIELD_LABELS, QUESTIONS, FieldStatus, WishesState, outstanding_fields
from app.domain.updates import AppliedChange, RejectedUpdate, apply_updates, validate_updates
from app.llm.base import LLMConfigurationError, LLMError
from app.llm.contract import ChatMessage, ExtractionRequest, MalformedResponseError, ReplyRequest
from app.llm.interviewer import Interviewer
from app.services.sessions import Session, SessionStore

log = logging.getLogger(__name__)

HISTORY_WINDOW = 12

GREETING = (
    "Hello! I'll help you put together a draft Personal Wishes Document. "
    "This is a fictional exercise and not legal advice. " + QUESTIONS["full_name"]
)


class TurnError(Exception):
    """A turn that could not be completed. Nothing was committed."""

    def __init__(self, code: str, user_message: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.user_message = user_message


class TurnResult(BaseModel):
    reply: str
    changes: list[AppliedChange]
    rejected: list[RejectedUpdate]
    reply_source: str  # "llm" or "template"


class InterviewService:
    def __init__(self, store: SessionStore, interviewer: Interviewer | None, config_problem: str | None = None) -> None:
        self.store = store
        self.interviewer = interviewer
        self.config_problem = config_problem

    def start_session(self) -> Session:
        session = Session()
        session.messages.append(ChatMessage(role="assistant", content=GREETING))
        session.asked_field = "full_name"
        self.store.save(session)
        return session

    def get_session(self, session_id: str) -> Session:
        return self.store.get(session_id)

    def handle_message(self, session_id: str, text: str) -> tuple[Session, TurnResult]:
        session = self.store.get(session_id)
        if self.interviewer is None:
            raise LLMConfigurationError(self.config_problem or "LLM is not configured")

        text = text.strip()
        history = [*session.messages, ChatMessage(role="user", content=text)]
        request = ExtractionRequest(
            current_state=session.state.model_dump(mode="json"),
            recent_messages=history[-HISTORY_WINDOW:],
            latest_user_message=text,
            last_question_field=session.asked_field,
        )

        try:
            proposed, parse_rejected = self.interviewer.extract(request)
        except MalformedResponseError as exc:
            raise TurnError(
                "malformed_model_response",
                "The assistant produced an answer I couldn't use. Nothing was saved — please send your message again, "
                "perhaps rephrased.",
                str(exc),
            ) from exc
        except LLMConfigurationError:
            raise
        except LLMError as exc:
            raise TurnError(exc.code, exc.user_message, str(exc)) from exc

        all_user_text = " ".join(m.content for m in history if m.role == "user")
        accepted, invalid = validate_updates(proposed, text, all_user_text)
        rejected = parse_rejected + invalid
        for r in rejected:
            log.info("Rejected update for %s: %s", r.field, r.reason)

        new_state, changes = apply_updates(session.state, accepted)
        next_field = next(iter(outstanding_fields(new_state)), None)
        reply, source = self._reply(new_state, changes, rejected, next_field, text)

        session.state = new_state
        session.messages = [*history, ChatMessage(role="assistant", content=reply)]
        session.asked_field = next_field
        self.store.save(session)
        return session, TurnResult(reply=reply, changes=changes, rejected=rejected, reply_source=source)

    def _reply(
        self,
        state: WishesState,
        changes: list[AppliedChange],
        rejected: list[RejectedUpdate],
        next_field: str | None,
        user_message: str,
    ) -> tuple[str, str]:
        note = state.field(next_field).note if next_field else None
        request = ReplyRequest(
            changes=[_describe(c) for c in changes],
            rejected=[RejectedUpdate(field=r.field, reason=r.reason) for r in rejected],
            next_field=next_field,
            next_question=QUESTIONS.get(next_field) if next_field else None,
            next_field_note=note,
            is_complete=next_field is None,
            latest_user_message=user_message,
        )
        try:
            text = self.interviewer.compose_reply(request) if self.interviewer else None
        except LLMError as exc:
            # Extraction already succeeded, so don't lose the turn over phrasing.
            log.warning("Reply generation failed, using template: %s", exc)
            text = None
        if text:
            return text, "llm"
        return template_reply(changes, next_field, note), "template"


def _describe(change: AppliedChange) -> dict[str, Any]:
    return {
        "field": change.field,
        "label": FIELD_LABELS[change.field],
        "kind": change.kind,
        "old_value": change.old_value,
        "new_value": change.new_value,
        "status": change.status.value,
    }


def template_reply(changes: list[AppliedChange], next_field: str | None, note: str | None) -> str:
    parts: list[str] = []
    corrected = [FIELD_LABELS[c.field].lower() for c in changes if c.kind == "corrected"]
    saved = [c for c in changes if c.status == FieldStatus.CONFIRMED and c.kind != "corrected"]
    if corrected:
        parts.append(f"Thanks, I've updated your {', '.join(corrected)}.")
    elif saved:
        parts.append("Thanks, I've noted that.")
    if next_field is None:
        parts.append(
            "That's everything I need — your draft document is in the preview. "
            "You can still correct any detail by telling me."
        )
    else:
        parts.append(note or QUESTIONS[next_field])
    return " ".join(parts)
