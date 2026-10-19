# Plan — The Interviewer (Phase 3)

Red/Green TDD throughout: write the failing test first, then the minimum code
that makes it pass. Run checks with the dev scripts (`scripts/test`,
`scripts/hooks` — ground truth, see README / `SPECS/TECH.md`).

Keep the repo's ground-truth gates green at every step: run `./scripts/test`
after each task group and `./scripts/hooks` before committing.

## Task group 1 — Shared Gemini client extraction

1. Red tests for a shared client module that (re)uses the existing httpx
   behaviour: request build (prompt + optional inline image + response schema),
   `x-goog-api-key` header auth, sanitised typed errors on transport/HTTP
   failure, typed error on a malformed model response, `close()` releases the
   pool. Reuse the existing `httpx.MockTransport` style, never the real API.
2. Extract `gemini.py`: base client with `generate(model, contents, schema) ->
   dict` plumbing plus `GeminiApiError` and `close()`.
3. Rewire the Bouncer onto the shared client (keep `GeminiVisionClient`,
   `check_photo`, `fetch_photo`, `select_photo_size` behaviour identical);
   update imports in the gateway and tests. Existing Bouncer tests must stay
   green — that is the regression guard for this refactor.

## Task group 2 — Interview contracts & session state

4. Red tests for `InterviewAnswer`, `InterviewStep` (ASK/FINISH consistency:
   ASK requires a question and forbids profile fields; FINISH requires profile +
   animal and forbids a question; junk payloads fail loudly) and
   `BehaviouralProfile` (non-empty fields).
5. Red tests for `InterviewState` nested in `SessionState`: derived question
   number (`len(answers) + 1`), finished derivation, `clear()` resets the
   interview, per-`chat_id` isolation.
6. Implement the models and the `SessionState` extension.

## Task group 3 — Interviewer client (prompts & schemas)

7. Red tests for the three typed calls against a mocked transport:
   - `next_question(transcript) -> str` — non-empty question, one question mark
     in the prompt guardrail text;
   - `next_step(transcript) -> InterviewStep` — both ASK and FINISH payloads
     parse, malformed payload raises `GeminiApiError`;
   - `distil(transcript) -> BehaviouralProfile` — profile + real-animal field;
   - transcript building caps very long answers and includes every Q/A pair;
   - the prompt text carries the voice rules (single question, no repeats,
     third-person profile) — asserted, not just hoped.
8. Implement `interviewer.py`: `INTERVIEWER_MODEL`, `MIN_QUESTIONS = 5`,
   `MAX_QUESTIONS = 7`, prompts, JSON schemas, the three methods.

## Task group 4 — Gateway wiring (the state machine)

9. Red tests for routing by phase with mocked client + interview client:
   - accept → confirmation then **Q1**, phase `INTERVIEW`;
   - text answer → next question; answer count advances; numbering correct;
   - answer at 5→6 answers may finish; at 7 answers the profile is forced
     (`distil` called, `next_step` not);
   - below 5 answers `next_step` is never allowed to finish (only
     `next_question` is called);
   - finish → case-file reveal sent, `BehaviouralProfile` stored, phase
     `CONVERT`, further input gets the holding reply;
   - non-text / empty / whitespace text → nudge + repeat pending question, no
     advance, no model call;
   - model failure → answers preserved, pending question unchanged, in-character
     retry, no duplication when the user resends;
   - text with no pending question → re-ask, text not recorded;
   - two chats never share answers (per-`chat_id` isolation).
10. Implement the routing + handlers (`_start_interview`, `_handle_answer`,
    in-character nudge/retry constants) with decorator logging; wire the
    interview client into `run_bot` (constructor + `close()`).

## Task group 5 — Docs & hygiene

11. Update README (phase status, pipeline description, layout) and
    `SPECS/ROADMAP.md` status after validation passes; record any drift.
12. Confirm `.env` and `.tmp/` remain untracked.

## Task group 6 — Full gate

13. `scripts/test` green (all new + existing tests, no regressions).
14. `scripts/hooks` green: ruff, ruff-format, mypy, pytest, gitleaks.

## Out of scope

Converter behaviour, full `/start` // `/restart` purge (Phase 7), persistent
dossiers, re-interviews, slash-command handling mid-interview.
