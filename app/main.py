"""FastAPI entry point: wires configuration, LLM client, services, API and static UI together."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import LLMSettings, get_settings
from app.llm.base import LLMClient, LLMConfigurationError
from app.llm.interviewer import Interviewer
from app.llm.openai_compatible import OpenAICompatibleClient
from app.services.interview import InterviewService, TurnError
from app.services.sessions import SessionNotFoundError, SessionStore

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

log = logging.getLogger("app")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(llm_settings: LLMSettings | None = None, llm_client: LLMClient | None = None) -> FastAPI:
    """Build the app. Tests inject `llm_client`; normally it's built from environment settings."""
    llm_settings = llm_settings or get_settings().llm()

    interviewer = None
    if llm_client is None and llm_settings.problem is None:
        llm_client = OpenAICompatibleClient(llm_settings)
    if llm_client is not None:
        interviewer = Interviewer(llm_client)
    else:
        # Start anyway so the UI can explain what's wrong instead of the server crashing.
        log.error("LLM not configured: %s", llm_settings.problem)

    app = FastAPI(title="Document Intake Assistant", version="1.0.0")
    app.state.llm_settings = llm_settings
    app.state.interview = InterviewService(SessionStore(), interviewer, llm_settings.problem)
    app.include_router(router)

    @app.exception_handler(SessionNotFoundError)
    def _not_found(_: Request, exc: SessionNotFoundError) -> JSONResponse:
        return _error(404, "session_not_found", "This session no longer exists. Please start a new one.")

    @app.exception_handler(LLMConfigurationError)
    def _not_configured(_: Request, exc: LLMConfigurationError) -> JSONResponse:
        log.error("LLM configuration error: %s", exc)
        return _error(503, exc.code, exc.user_message)

    @app.exception_handler(TurnError)
    def _turn_failed(_: Request, exc: TurnError) -> JSONResponse:
        log.warning("Turn failed [%s]: %s", exc.code, exc)
        return _error(502, exc.code, exc.user_message)

    @app.exception_handler(RequestValidationError)
    def _invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid request"}
        return _error(422, "invalid_request", str(first.get("msg")))

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
