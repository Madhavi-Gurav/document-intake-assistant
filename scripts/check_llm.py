"""Live check of the configured LLM: runs one real extraction and prints what would be applied.

Usage:  python -m scripts.check_llm "My brother James will be my executor"
"""

from __future__ import annotations

import sys

from app.config import get_settings
from app.domain.state import WishesState
from app.domain.updates import validate_updates
from app.llm.base import LLMError
from app.llm.contract import ChatMessage, ExtractionRequest, MalformedResponseError
from app.llm.interviewer import Interviewer
from app.llm.openai_compatible import OpenAICompatibleClient


def main() -> int:
    message = " ".join(sys.argv[1:]) or "I'm Jane Smith, my brother James will be my executor and I have no children."
    llm = get_settings().llm()
    print(f"Provider: {llm.label} | model: {llm.model} | base URL: {llm.base_url}")
    if llm.problem:
        print(f"NOT CONFIGURED: {llm.problem}")
        return 2

    interviewer = Interviewer(OpenAICompatibleClient(llm))
    request = ExtractionRequest(
        current_state=WishesState().model_dump(mode="json"),
        recent_messages=[ChatMessage(role="user", content=message)],
        latest_user_message=message,
        last_question_field="full_name",
    )
    try:
        proposed, parse_rejected = interviewer.extract(request)
    except (LLMError, MalformedResponseError) as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1

    accepted, invalid = validate_updates(proposed, message, message)
    print(f"\nUser message: {message!r}\n")
    for u in accepted:
        print(f"  ACCEPT  {u.field:<24} {u.status:<12} {u.value!r}")
    for r in parse_rejected + invalid:
        print(f"  REJECT  {r.field:<24} {r.reason}")
    print("\nOK - the model is reachable and returns usable structured output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
