# Plan — /start and /restart full reset (Phase 7)

Red/Green TDD throughout: write the failing test first, then the minimum
code that makes it pass. Run checks with the dev scripts (`scripts/test`,
`scripts/hooks` — ground truth, see README / `SPECS/TECH.md`).

Keep the repo's ground-truth gates green at every step: run `./scripts/test`
after each task group and `./scripts/hooks` before committing.

## Task group 1 — Typed command contract (`models.py`)

1. Red tests in `tests/unit/test_models.py` (`TestMessageEntities`):
   - `MessageEntity` parses Telegram's shape
     (`{"type": "bot_command", "offset": 0, "length": 6}`) and ignores
     extra fields (`username`, `language`); negative `offset`/`length`
     rejected with `ValidationError`.
   - `Message` accepts `entities` from a raw dict, defaults to `None`
     when absent, and **every existing entity-less payload still
     validates** (no fixture regressions — build a `Message` exactly as
     the current fixtures do).
2. Implement `MessageEntity` and `Message.entities: list[MessageEntity] |
   None = None` in `src/telegram_documentaries/models.py`.

## Task group 2 — Recognition & purge helpers (pure, no network)

3. Red tests in a new `tests/unit/test_reset.py`, first
   `TestParseResetCommand` — parametrised over `Update`/`Message`
   fixtures, covering **both paths explicitly**:
   - **Entity path:** `/start`, `/restart`, `/start@MyBot`, `/RESTART`,
     `/start with args` (entity covers only the command) → the correct
     `Literal`; `Message` built with `entities=[{offset:0, length:…,
     type:"bot_command"}]`.
   - **Text fallback (no entities at all):** `/start`, `/restart`,
     `/start@MyBot more text`, trailing whitespace (`"/restart   "`),
     mixed case (`"/Start"`) → recognised.
   - **Rejects (both paths):** `start`, `//start`, `/starting`,
     `/restarts`, `/foo`, `hello /start`, `""`, `"   "`,
     `text is None` (photo-only and caption-only messages) → `None`.
   - **Entity edge rows:** a `bot_command` entity at `offset != 0`
     (fallback then only sees the first token → `None` for
     `"say /start"`); an **out-of-bounds entity slice** (hostile payload)
     is ignored — a logged warning records numeric lengths only, never
     message text — and the fallback still recognises a valid `/restart`
     in that same text.
4. Red tests, same file, `TestPurgeChatMedia`:
   - seeds `tmp_path/555/portrait.jpg` (+ a second file) → directory gone,
     returns `2`;
   - missing directory → returns `0`, never raises (idempotent path);
   - a sibling chat directory (`tmp_path/777`) and the media root itself
     are untouched;
   - **containment guard:** `tmp_path/555` as a symlink pointing outside
     the root → `ValueError` raised, outside target intact;
   - monkeypatched `shutil.rmtree` raising `OSError` (other than
     `FileNotFoundError`) propagates — fail loud, the handler degrades.
5. Implement in `src/telegram_documentaries/bot_gateway.py`:
   `parse_reset_command(message) -> Literal["start", "restart"] | None`
   (entity-first, narrow first-token fallback, one shared normaliser, no
   regex) and `purge_chat_media(media_root, chat_id) -> int` (resolve +
   strict-child containment guard, `FileNotFoundError` → `0`), placed next
   to `_store_photo`.

## Task group 3 — Reset handler & interception (the state machine head)

6. Red tests in a new `tests/unit/test_reset_gateway.py`. Drive
   `_dispatch_update` directly (explicit `sessions` dict → direct state
   assertions) for most rows, plus at least one `run_bot` batch-side-effect
   test for the full loop; reuse the existing harness patterns from
   `tests/unit/test_scripter_gateway.py` / `test_bouncer_gateway.py`
   (`FakeInterviewer`-style fakes, `media_root=str(tmp_path)`, placeholder
   secrets only):
   - **First-ever `/start`** (no session, no media) → exactly one send,
     `text == RESET_REPLY`; **byte-identical** to what `/restart` sends in
     the same situation (user's locked decision: no separate welcome
     path); session afterwards at `Phase.AWAITING_PHOTO`.
   - **Reset from every `Phase`:** parametrised over all eight `Phase`
     values (session preset, plus seeded `tmp_path/<chat_id>/` media) →
     media gone, `session.clear()`-equivalent state (phase
     `AWAITING_PHOTO`, `photo_local_path`/`temp_media_path`/`script_text`
     `None`, `voice_note_sent` `False`, empty interview buffer),
     `RESET_REPLY` sent.
   - **Mid-interview `/restart`:** drive photo → accepted → Q1 → answers
     until `Phase.INTERVIEW` with a pending question; dispatch `/restart`
     → `FakeInterviewer` never receives the command text, no answer
     recorded, media purged, reply sent; then dispatch a **new photo in
     the same process** → the Bouncer flow runs again from a clean
     session (the no-restart guarantee, unit-level).
   - **Idempotency:** second reset immediately after the first →
     byte-identical `RESET_REPLY`, `media_files=0`, no exception.
   - **Not-command rows:** `"/starting"`, `"start"`, `"//start"`, `"/foo"`
     at `AWAITING_PHOTO` still get `PHOTO_NUDGE`; non-command text
     mid-interview is still recorded as an answer (no routing
     regression); text-fallback `/start` (entity-less payload) resets.
   - **Interception order:** the command never reaches `_handle_interview`
     or `build_reply` (assert on the fakes/log).
   - **Purge failure degrades:** monkeypatched `shutil.rmtree` raising
     `OSError` → `RESET_REPLY` still sent, state still cleared, caplog
     carries `logger.exception("temp media purge failed …")`.
   - **Scoping:** another chat's seeded media directory and session are
     untouched.
   - **Log contract:** caplog has `reset_command … command=… from_phase=…`,
     `session_reset … media_files=…`, and `phase_transition … -> AWAITING_PHOTO`;
     no message text and no secret-looking values appear in the log.
   - **Full-loop row:** `run_bot` with batch side-effects
     `[run, reset, second run]` → completes without restart, second run's
     photo reaches the Bouncer (mirrors
     `test_scripter_gateway.py`'s harness style).
7. Implement in `bot_gateway.py`: `RESET_CONFIRM_MSG`, `GREETING_MSG`,
   `RESET_REPLY` (exact strings from `requirements.md`), `_handle_reset`
   (never raises; purge failure caught `(OSError, ValueError)`) and the
   **first-branch interception** in `_dispatch_update` right after the
   session fetch (`bot_gateway.py:1144`), before the `INTERVIEW_PHASES`
   branch.

## Task group 4 — State lock-in & regressions

8. Add `TestClearResetsEverything` to
   `tests/unit/test_session_state.py`: `clear()` resets **every** field
   (phase, photo refs, `photo_mime_type`, `temp_media_path`,
   `script_text`, `voice_note_sent`, interview answers/pending
   question/profile/`question_sent_at`); sessions do not share state.
   No production change expected — this is a lock-in test.
9. Run the whole suite; fix any routing regression (Bouncer nudge,
   interview answer recording, later-phase holding reply must be
   untouched for non-command input).

## Task group 5 — Docs & hygiene (per `requirements.md`'s documentation deltas)

10. **README.md** — reword line 38 so it matches shipped behaviour exactly
    (in-process reset, purge scoped to the chat's own temp media, one
    confirmation+greeting message); add the command to the "Run the bot"
    stage list; keep the status block honest about this being one Phase 7
    slice.
11. **SPECS/ROADMAP.md** — Phase 7 gains an `Implementation:` note naming
    the reset slice's real pieces; the status row reflects only this slice
    (reset implemented; timeout & wrong-payload slices pending).
12. **Banner** — `run_bot` logs `Starting Telegram gateway (Phase 7 -
    Resilience)`; assert it in a test (extend an existing `run_bot` test
    with a caplog check).
13. **Stale comments** — refresh `bot_gateway.py:551` ("purged on
    `/restart`" — now true, point at the mechanism) and
    `bot_gateway.py:1001` ("no `/start`" — reword to the real behaviour).
14. Confirm `.env` and `.tmp/` remain untracked (`git check-ignore`).

## Task group 6 — Full gate & live validation

15. `scripts/test` green (all new + existing, no regressions).
16. `scripts/hooks` green: ruff, ruff-format, mypy, pytest, gitleaks.
17. Live Telegram validation per `validation.md` — the TA scenario:
    reset mid-every-phase with **zero process restarts** (single
    gateway-start line in the log), then a second full run immediately
    afterwards; record log lines and evidence.

## Out of scope

API-timeout fallbacks and wrong-payload-at-wrong-stage guards (other
Phase 7 slices), deployment (Phase 8), session persistence, multi-user
concurrency, in-flight cancellation/locking, media TTL sweepers.
