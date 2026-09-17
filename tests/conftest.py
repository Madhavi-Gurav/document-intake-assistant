from __future__ import annotations

from pathlib import Path

import pytest

from app.config import LLMSettings
from app.llm.interviewer import Interviewer
from app.services.interview import InterviewService
from app.services.sessions import SessionStore

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class ScriptedLLM:
    """Test double for `LLMClient`: returns queued raw outputs (or raises queued errors) in order.

    Extraction calls (json_output=True) and reply calls (json_output=False) have separate queues,
    so tests only script what they care about. An empty reply queue means "reply call fails",
    which exercises the template fallback.
    """

    provider = "scripted"
    model = "fixture"

    def __init__(self, extractions: list[str | Exception] | None = None, replies: list[str | Exception] | None = None):
        self.extractions = list(extractions or [])
        self.replies = list(replies or [])
        self.calls: list[dict] = []

    def complete(self, system: str, user: str, *, json_output: bool, max_tokens: int) -> str:
        self.calls.append({"system": system, "user": user, "json_output": json_output})
        queue = self.extractions if json_output else self.replies
        if not queue:
            if json_output:
                raise AssertionError("unexpected extraction call")
            from app.llm.base import LLMUnavailableError

            raise LLMUnavailableError("no scripted reply")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def extraction_calls(self) -> list[dict]:
        return [c for c in self.calls if c["json_output"]]


@pytest.fixture
def make_service():
    def _make(llm: ScriptedLLM) -> InterviewService:
        return InterviewService(SessionStore(), Interviewer(llm))

    return _make


TEST_LLM_SETTINGS = LLMSettings(
    provider="scripted", label="Scripted", base_url="", model="fixture", api_key=None, timeout_seconds=1, extra_body={}, problem=None
)
