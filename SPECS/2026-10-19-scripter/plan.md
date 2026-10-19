# Plan — The Scripter (Phase 5)

Red/Green TDD throughout: every task group starts with failing tests and ends
with the dev scripts from the README (`scripts/test`, `scripts/hooks`).

Dev-box note: the sandbox sits at ~95–97% of its 1 GB cgroup limit, so commands
stay small (targeted `pytest` inside a group; one full `scripts/hooks` per group,
not per edit). Environment policy, not product behaviour.

## Task group 1 — Boundary contract: a script is one paragraph

1. Red (`tests/unit/test_script_contracts.py`, `tests/unit/test_session_state.py`):
   - `Script(text="A\n\nB   C ")` → `text == "A B C"` (whitespace runs, including
     line breaks, collapse to one space; ends stripped).
   - `Script.model_validate({"text": "One line."})` round-trips (schema key =
     model field).
   - empty / whitespace-only / non-string / missing `text` → `ValidationError`.
   - `SessionState.script_text` defaults to `None`; `clear()` resets it to
     `None` after it was set.
2. Green: `Script` in `models.py` (validator does the normalising, documented as
   the boundary guarantee), `SessionState.script_text`, `clear()` update.

## Task group 2 — Let a stage speak through the system instruction

1. Red (`tests/unit/test_gemini_client.py`): `generate(contents, schema,
   system_instruction="…")` sends `systemInstruction.parts[0].text` in the
   request body; omitting it leaves the field **out** of the body entirely (no
   empty `systemInstruction` sent to the API); the existing call signature still
   works unchanged.
2. Green: keyword-only `system_instruction: str | None = None` on
   `GeminiClient.generate`, added to the payload only when set.

## Task group 3 — The Scripter

1. Red (`tests/unit/test_scripter_client.py`, `MockTransport` like the
   Converter/Interviewer client tests):
   - `build_prompt(profile)` carries the dossier text, the species and the
     one-line description, and nothing interactive (pure function, asserted
     directly).
   - `SCRIPTER_PERSONA`/`SCRIPT_RULES` content checks: British-documentary
     narrator; one paragraph; "90 words"; no markdown; plain prose for the
     microphone; name the species.
   - `write_script` posts to `…/gemini-3.1-flash-lite:generateContent` with the
     persona+rules as `systemInstruction`, the schema as `responseSchema`, and
     the dossier in the user turn; returns a normalised `Script`.
   - Multi-line model output comes back as one paragraph.
   - HTTP 500 / non-JSON / `{}` / empty `text` → `GeminiApiError` (typed, key
     never in the message).
   - 91-word narration → returned **and** a warning logged (`caplog`), count in
     the message; ≤90 words → no warning.
2. Green: `scripter.py` — `SCRIPTER_MODEL`, `MAX_WORDS`, persona, rules,
   `build_prompt`, `SCRIPT_SCHEMA`, `ScripterClient.write_script` with the
   `log_async_call` decorator plus the word-count warning.

## Task group 4 — Wire the narration into the pipeline

1. Red (`tests/unit/test_scripter_gateway.py`, same harness as
   `test_converter_gateway.py`):
   - Fastest happy path: the fixed 5-answer interview ends → hold line →
     `sendPhoto` → **then** the narration as one `sendMessage` with the script
     text; the session ends in `Phase.SCRIPT` with `script_text` stored.
   - Order matters: the narration must not be sent before the portrait.
   - Run-once: the scripter is called **exactly once** in the flow, and a
     message after the narration gets `LATER_PHASE_REPLY` with no further
     scripter call (also after a photo sent post-narration).
   - Scripter failure (`GeminiApiError`) → `SCRIPT_FAILURE_MSG`, phase stays
     `SCRIPT`, `script_text` stays `None`, and the delivered portrait is not
     undone (no extra `sendPhoto`).
   - Independence: a failing portrait upload still yields the narration.
   - Non-text/unexpected input after the narration never re-enters the flow.
2. Green: `_write_script(client, scripter, session, profile)` (hold-free, never
   raises, holds the phase transition + `script_text` + in-character failure
   line) called by `_finish_interview` after `_deliver_portrait`; `scripter`
   built once in `run_bot` and closed on shutdown.
3. Doc accuracy from the same sweep: `LATER_PHASE_REPLY`'s comment ("Converter
   onwards") and the `bot_gateway` module docstring must describe the stage that
   now exists; the startup banner becomes `(Phase 5 - Scripter)` (live
   validation reads it back).

## Task group 5 — Docs, gates, PR

1. README: pipeline description gains the narration step (text after the
   portrait); `SPECS/TECH.md` needs no change (Scripter already specified).
2. ROADMAP: Phase 5 `Implementation:` note + status table row → Complete
   (pending the live checks in `validation.md`).
3. `./scripts/test` and `./scripts/hooks` green; commit on
   `feature/2026-10-19-scripter`, push, open the PR against `main`.
4. Live run by the user: portrait → narration paragraph in the chat; tick
   `validation.md` with the real log lines.
