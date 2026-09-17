"""HTTP API contract. These models are what the frontend (or any client) relies on."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.documents.generator import DraftDocument
from app.domain.state import WishesState
from app.domain.updates import AppliedChange, RejectedUpdate
from app.llm.contract import ChatMessage


class FieldInfo(BaseModel):
    name: str
    label: str
    required: bool


class Progress(BaseModel):
    confirmed: int
    required: int
    outstanding: list[str]
    next_field: str | None


class SessionView(BaseModel):
    session_id: str
    messages: list[ChatMessage]
    state: WishesState  # full tracked state: value + status + note per field
    confirmed_state: dict[str, Any]  # confirmed values only, in the brief's nested shape
    fields: list[FieldInfo]
    progress: Progress
    document: DraftDocument


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class TurnView(BaseModel):
    reply: str
    reply_source: str
    changes: list[AppliedChange]
    rejected: list[RejectedUpdate]


class SendMessageResponse(BaseModel):
    session: SessionView
    turn: TurnView


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str
    llm_provider: str
    llm_model: str
    llm_configured: bool
    llm_problem: str | None
