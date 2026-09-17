# Document Intake Assistant

A small web app that interviews a user, keeps the answers in an explicit structured
state, and produces a draft **Personal Wishes Document** (fictional, not legal advice).

Built for the Wenup engineering technical test. The LLM is **Google AI Studio (Gemini)**
or **NVIDIA NIM**, both through their OpenAI-compatible chat-completions APIs.

---

## Quick start

Requirements: Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # then edit .env: set LLM_PROVIDER and LLM_API_KEY
python -m scripts.check_llm # optional: one live call to confirm the key/model work
uvicorn app.main:app --reload
```

Open <http://localhost:8000>.

Run the tests (no API key or network needed):

```bash
pytest
```

### Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` (Google AI Studio) or `nvidia` (NVIDIA NIM) |
| `LLM_API_KEY` | — | **Required.** Get one at aistudio.google.com/apikey or build.nvidia.com |
| `LLM_MODEL` | `gemini-2.5-flash` / `meta/llama-3.3-70b-instruct` | Any chat model the provider serves |
| `LLM_BASE_URL` | provider preset | Override for other OpenAI-compatible gateways |
| `LLM_TIMEOUT_SECONDS` | `60` | Per request |

`.env` is git-ignored. If the key is missing or wrong, the server still starts: the UI shows a
banner explaining the problem and the API returns `503 llm_not_configured`.

---

## How it works

```
 Browser (frontend/)            FastAPI (app/api)          Application logic (app/services)
 ─────────────────── HTTP/JSON ───────────────────────────────────────────────────────────────
  chat + live preview  ──POST /messages──▶  routes  ──▶  InterviewService.handle_message
                                                           │
                    ┌──────────────────────────────────────┤
                    ▼                                      ▼
   LLM interaction (app/llm)                   Domain (app/domain)          Documents (app/documents)
   1. extract: prompt → raw JSON               validate_updates             generate_document(state)
      parse_extraction (tolerant, per-field)   apply_updates                (deterministic template)
   2. compose_reply: phrase next question      consistency rules
      (template fallback)                      outstanding_fields → next question
```

One turn:

1. **Extract.** The model gets the current state, the recent conversation, the field that was
   just asked about and the user's message. It returns proposed updates as JSON. Each update has
   a value, a status (`confirmed` or `unconfirmed`), a verbatim quote as evidence, and a note if
   anything is unclear.
2. **Parse.** Broken output (prose, truncated JSON, no `updates` object) gets one repair retry.
   Fields are checked one at a time, so a single wrongly typed field is dropped without losing
   the valid ones next to it.
3. **Validate** against what the user actually said:
   - The evidence must appear in the latest message.
   - Names and addresses can only contain words the user typed. This stops the model from
     adding a surname or postcode the user never gave ("My brother James" can't become
     "James Smith").
   - Length and item-count limits apply.
4. **Apply and check consistency.**
   - A correction replaces the old value and is recorded as `corrected`.
   - Naming children implies `has_children = true`.
   - "No children" plus a named child gets flagged for the user to resolve.
   - A later "actually, no children" clears the old names.
5. **Pick the next question in code.** Unconfirmed fields come first, then missing ones in
   order. Captured fields can't be asked again, because the model is only told about the next
   field.
6. **Phrase the reply** with the model. If that call fails, a template is used and the turn
   still succeeds.
7. **Render the document** from confirmed values only. Missing or unconfirmed values appear
   as `[not yet provided]` or `[awaiting confirmation]`, never as facts.

A turn is **atomic**. If extraction fails (timeout, rate limit, bad output twice), nothing is
saved, not even the user's message. The API returns an error and the UI puts the text back in
the input box so the user can resend.

### Structured state

Every field is tracked as `{value, status, note, evidence}`, where status is `missing`,
`unconfirmed` or `confirmed`. The API also returns `confirmed_state`, which holds confirmed
values only, in the shape shown in the brief:

```json
{
  "full_name": "Jane Smith",
  "home_address": null,
  "covers_worldwide_assets": true,
  "has_children": false,
  "children": [],
  "executor": { "name": "James", "relationship": "brother" },
  "specific_gifts": null,
  "additional_wishes": null
}
```

`null` means "not known yet". `[]` means the user confirmed there are none.

### API

| Method | Path | Result |
|---|---|---|
| `GET` | `/api/health` | Provider, model, whether the LLM is configured |
| `POST` | `/api/sessions` | `201` `SessionView` (with the assistant's greeting) |
| `GET` | `/api/sessions/{id}` | `SessionView` |
| `POST` | `/api/sessions/{id}/messages` `{"message": "..."}` | `{session: SessionView, turn: {reply, reply_source, changes, rejected}}` |

`SessionView` = `messages`, `state` (tracked), `confirmed_state`, `fields`, `progress`, `document`.
Errors always use the shape `{"error": {"code", "message"}}`:
`404 session_not_found`, `422 invalid_request`, `502 llm_unavailable | malformed_model_response`,
`503 llm_not_configured`. Interactive docs: <http://localhost:8000/docs>.

### Project layout

```
app/
  api/          routes + request/response schemas (HTTP contract)
  services/     InterviewService (turn orchestration), in-memory SessionStore
  domain/       state schema, validation, update + consistency rules (no I/O)
  llm/          provider interface, OpenAI-compatible client, prompts, response contract/parser
  documents/    deterministic document generator
  config.py     env settings and provider presets
frontend/       static HTML/CSS/JS (no build step)
scripts/        check_llm.py: live smoke test for your key/model
tests/          pytest suite + fixtures/ (recorded valid, ambiguous and malformed model outputs)
```

### Tests

The tests run against **recorded model outputs** in `tests/fixtures/`, using a `ScriptedLLM`
test double. This keeps them deterministic and offline. They cover:

- Parsing valid, code-fenced, prose, truncated and wrongly typed responses, including rejecting
  bad fields one at a time
- Validation that blocks invented values and checks evidence
- Multi-field answers, corrections, confirming a vague answer, and each consistency rule
- The turn flow: retry on bad output, nothing saved when a turn fails, template reply fallback,
  missing config
- Document rendering: disclaimer present, unconfirmed values never shown as fact, "no children"
  handled
- The HTTP contract and error codes, the provider client's request shape, the JSON-mode
  fallback, and how HTTP errors are classified

`python -m scripts.check_llm` is the live counterpart: one real call to the configured model.

---

## Design decisions and trade-offs

- **Two model calls per turn (extract, then reply).** One combined call would be faster, but
  its reply could ask for something already captured, or claim a value was saved when
  validation rejected it. Splitting them lets code decide *what* to ask and leaves the model to
  decide *how* to phrase it.
- **Code-generated document, not LLM-generated.** The draft is legal-style text. Rendering it
  from a template guarantees it matches the preview and contains no invented clauses.
- **Evidence and verbatim checks.** A cheap, explainable guard against hallucinated facts. The
  cost: if a model paraphrases its evidence, a correct update can be rejected and the user gets
  asked again. That failure is safe and visible (a "Not saved" chip in the UI), which is better
  than silently storing a wrong fact.
- **JSON mode is a nice extra, not something we depend on.** Not every model on NIM supports
  `response_format`. The client turns it off automatically after a `400` and relies on the
  prompt and the parser instead.
- **In-memory sessions.** Enough for the exercise. The store has a two-method interface, so it
  can be swapped for a real database.

## What I would improve for production

- **Persistence and auth.** Store sessions in Postgres or Redis with an expiry. Add user accounts,
  and encrypt stored data at rest, since it is personal.
- **An evaluation set.** A few dozen scripted conversations (multi-field, corrections,
  contradictions, prompt-injection attempts) run against each real model, scoring extraction
  accuracy and false rejections. Every prompt or model change would be checked against it.
- **Structured outputs using the provider's JSON schema.** Where the model supports it,
  instead of plain JSON mode. Also try a smaller, faster model for the reply call.
- **Streaming replies**, plus retries with backoff and a circuit breaker for provider outages.
- **Editing fields directly in the preview**, using the same validation path as chat edits.
- **Observability.** Structured logs of each turn with personal data redacted, token and latency
  metrics, and a count of rejected updates as a hallucination signal.
- **A final "review and confirm" step** before a document is marked final, plus PDF export.
- **Security.** Rate limiting, CSRF/CORS settings, a content-security policy, and secrets from a
  managed vault.

See [AI_LOG.md](AI_LOG.md) for how AI tools were used.
