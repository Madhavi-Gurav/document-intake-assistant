"""In-memory session storage. Swappable for Redis/Postgres behind the same two methods."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.domain.state import WishesState
from app.llm.contract import ChatMessage


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    state: WishesState = Field(default_factory=WishesState)
    messages: list[ChatMessage] = Field(default_factory=list)
    asked_field: str | None = None  # the field the assistant's latest message asked about


class SessionNotFoundError(KeyError):
    pass


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            return session.model_copy(deep=True)

    def save(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.id] = session.model_copy(deep=True)
