from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.api.schemas import (
    ErrorResponse,
    FieldInfo,
    HealthResponse,
    Progress,
    SendMessageRequest,
    SendMessageResponse,
    SessionView,
    TurnView,
)
from app.config import LLMSettings
from app.documents.generator import generate_document
from app.domain.state import FIELD_LABELS, FIELD_ORDER, FieldStatus, confirmed_view, is_required, outstanding_fields
from app.services.interview import InterviewService
from app.services.sessions import Session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

ERRORS = {404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


def _service(request: Request) -> InterviewService:
    return request.app.state.interview


def to_view(session: Session) -> SessionView:
    state = session.state
    required = [f for f in FIELD_ORDER if is_required(state, f)]
    outstanding = outstanding_fields(state)
    return SessionView(
        session_id=session.id,
        messages=session.messages,
        state=state,
        confirmed_state=confirmed_view(state),
        fields=[FieldInfo(name=f, label=FIELD_LABELS[f], required=is_required(state, f)) for f in FIELD_ORDER],
        progress=Progress(
            confirmed=sum(state.field(f).status == FieldStatus.CONFIRMED for f in required),
            required=len(required),
            outstanding=outstanding,
            next_field=outstanding[0] if outstanding else None,
        ),
        document=generate_document(state),
    )


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    llm: LLMSettings = request.app.state.llm_settings
    return HealthResponse(
        status="ok",
        llm_provider=llm.label,
        llm_model=llm.model,
        llm_configured=_service(request).interviewer is not None,
        llm_problem=llm.problem,
    )


@router.post("/sessions", response_model=SessionView, status_code=201)
def create_session(request: Request) -> SessionView:
    return to_view(_service(request).start_session())


@router.get("/sessions/{session_id}", response_model=SessionView, responses=ERRORS)
def get_session(session_id: str, request: Request) -> SessionView:
    return to_view(_service(request).get_session(session_id))


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse, responses=ERRORS)
def send_message(session_id: str, body: SendMessageRequest, request: Request) -> SendMessageResponse:
    session, turn = _service(request).handle_message(session_id, body.message)
    return SendMessageResponse(session=to_view(session), turn=TurnView(**turn.model_dump()))
