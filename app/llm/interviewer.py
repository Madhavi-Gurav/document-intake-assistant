"""The two LLM tasks the application uses: extracting field updates and phrasing the next reply."""

from __future__ import annotations

import logging

from app.domain.updates import FieldUpdate, RejectedUpdate
from app.llm.base import LLMClient
from app.llm.contract import ExtractionRequest, MalformedResponseError, ReplyRequest, parse_extraction
from app.llm.prompts import EXTRACTION_REPAIR, EXTRACTION_SYSTEM, REPLY_SYSTEM

log = logging.getLogger(__name__)

MAX_REPLY_CHARS = 1000


class Interviewer:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def extract(self, request: ExtractionRequest) -> tuple[list[FieldUpdate], list[RejectedUpdate]]:
        """Ask the model for field updates. One repair retry on malformed output, then give up."""
        payload = request.model_dump_json(indent=2)
        raw = self.client.complete(EXTRACTION_SYSTEM, payload, json_output=True, max_tokens=4096)
        try:
            return parse_extraction(raw)
        except MalformedResponseError as exc:
            log.warning("Malformed extraction response (%s): %.300r", exc, raw)
            raw = self.client.complete(
                EXTRACTION_SYSTEM, payload + EXTRACTION_REPAIR.format(error=exc), json_output=True, max_tokens=4096
            )
            return parse_extraction(raw)  # a second failure propagates to the caller

    def compose_reply(self, request: ReplyRequest) -> str | None:
        """Return the model's phrasing, or None if it is unusable (caller falls back to a template)."""
        text = self.client.complete(REPLY_SYSTEM, request.model_dump_json(indent=2), json_output=False, max_tokens=2048)
        text = (text or "").strip()
        if not text or len(text) > MAX_REPLY_CHARS:
            log.warning("Discarding unusable reply of length %d", len(text))
            return None
        return text
