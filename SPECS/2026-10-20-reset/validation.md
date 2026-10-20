# Validation — /start and /restart full reset (Phase 7)

Every box is evidence, not intent: the command or log line that proves it
goes in the report when the box is ticked. Automated boxes are ticked by
`./scripts/test` / `./scripts/hooks`; live boxes need a real Telegram run.
This file is written before implementation — boxes are ticked at validation
time, and any drift between spec and build is recorded at the bottom (house
practice, see `SPECS/2026-10-19-narrator/validation.md`).

## Automated

### Contracts (`models.py`)

- [ ] `MessageEntity` parses Telegram's shape with `type`/`offset`/`length`
      and ignores extras (`username`); negative offsets/lengths raise
      `ValidationError`; `Message.entities` defaults `None`; entity-less
      payloads validate exactly as before.
      → `tests/unit/test_models.py::TestMessageEntities`

### Command recognition (`parse_reset_command`)

- [ ] **Entity path:** `/start`, `/restart`, `/start@MyBot`, `/RESTART`,
      `/start with args` (entity covers only the command) all return the
      correct `Literal`.
      → `tests/unit/test_reset.py::TestParseResetCommand` (entity rows)
- [ ] **Text-fallback path (no entities):** `/start`, `/restart`,
      `/start@MyBot …`, trailing whitespace and mixed case are recognised;
      a valid reset command is never silently missed.
      → `tests/unit/test_reset.py::TestParseResetCommand` (fallback rows)
- [ ] **Narrowness:** `start`, `//start`, `/starting`, `/restarts`,
      `/foo`, `hello /start`, empty/whitespace text, `text is None`
      (photo-only, caption-only) all return `None` — they fall through to
      today's generic path unchanged.
      → `tests/unit/test_reset.py::TestParseResetCommand` (reject rows)
- [ ] **Hostile input:** a `bot_command` entity at `offset != 0` and an
      out-of-bounds entity slice are handled without crash — the bad
      entity is ignored (warning logs numeric lengths only, never message
      text) and the fallback still decides.
      → `tests/unit/test_reset.py::TestParseResetCommand` (entity-edge rows)

### Media purge (`purge_chat_media`)

- [ ] Purges `media_root/<chat_id>/` entirely and returns the file count;
      a **missing directory returns `0` without raising** (idempotent).
      → `tests/unit/test_reset.py::TestPurgeChatMedia`
- [ ] **Scoping:** a sibling chat directory and the media root itself
      survive; a symlinked chat directory pointing outside the root is
      refused (`ValueError`) with the outside target intact.
      → `tests/unit/test_reset.py::TestPurgeChatMedia` (scoping rows)
- [ ] A non-`FileNotFoundError` `OSError` propagates (fail loud — the
      handler is what degrades).
      → `tests/unit/test_reset.py::TestPurgeChatMedia` (failure row)

### Gateway behaviour

- [ ] **First-ever `/start` (no session, no media) sends exactly one
      message `== RESET_REPLY`, byte-identical to `/restart`'s reply**;
      session afterwards at `AWAITING_PHOTO`.
      → `tests/unit/test_reset_gateway.py` (alias/first-run rows)
- [ ] **Reset from every phase:** parametrised over all eight `Phase`
      values → media purged on disk, all session fields cleared, phase
      `AWAITING_PHOTO`, `RESET_REPLY` sent — covering
      mid-interview and post-`DONE` at minimum (ROADMAP's "reset
      mid-every-phase starts clean").
      → `tests/unit/test_reset_gateway.py` (phase sweep)
- [ ] **Mid-interview `/restart` is never an answer:** the Interviewer
      fake never receives the command text, no answer is recorded, the
      pending question/profile are discarded, media is purged — and a new
      photo dispatched **in the same process** starts a fresh Bouncer
      run.
      → `tests/unit/test_reset_gateway.py` (mid-interview row + same-process
      row)
- [ ] **Idempotency:** a second reset right after the first returns the
      identical `RESET_REPLY` with `media_files=0` and no error.
      → `tests/unit/test_reset_gateway.py` (repeat row)
- [ ] **No routing regression:** `/starting`, `start`, `//start`, `/foo`
      and plain text still get `PHOTO_NUDGE` at the greeting phase; plain
      text mid-interview is still recorded as an answer.
      → `tests/unit/test_reset_gateway.py` (non-command rows) +
      existing `tests/unit/test_bot_gateway.py::TestBuildReply`,
      `tests/unit/test_interview_gateway.py`
- [ ] **Interception order:** the reset command never reaches
      `_handle_interview` / `build_reply` (asserted via fakes/logs).
      → `tests/unit/test_reset_gateway.py`
- [ ] **Purge failure degrades:** monkeypatched `rmtree` `OSError` →
      `RESET_REPLY` still sent, state still cleared, loud
      `temp media purge failed` exception line in caplog; the conversation
      never breaks.
      → `tests/unit/test_reset_gateway.py` (failure row)
- [ ] **Cross-chat isolation:** another chat's media directory and
      session untouched by a reset.
      → `tests/unit/test_reset_gateway.py` (scoping row)
- [ ] **Log contract:** `reset_command … from_phase=…`,
      `session_reset … media_files=…` and `phase_transition … ->
      AWAITING_PHOTO` lines appear; no message text, no file bytes and no
      secret-looking values in the log.
      → `tests/unit/test_reset_gateway.py` (caplog rows)
- [ ] **Full loop:** `run_bot` processes `[run, reset, second run]` batch
      side-effects to completion with no restart needed.
      → `tests/unit/test_reset_gateway.py` (`run_bot` row)
- [ ] `SessionState.clear()` lock-in: every field (phase, photo refs,
      `temp_media_path`, `script_text`, `voice_note_sent`, full interview
      buffer) reset; sessions do not share state.
      → `tests/unit/test_session_state.py::TestClearResetsEverything`
- [ ] Banner reads `(Phase 7 - Resilience)`.
      → caplog assertion in a `run_bot` test
- [ ] `./scripts/test` green (whole suite, no regressions).
- [ ] `./scripts/hooks` green: ruff, ruff-format, mypy, pytest, gitleaks.

## Live (real Telegram traffic, **no process restart**)

Drive everything from one Telegram chat against one running bot process.
The decisive evidence for the whole feature: **exactly one**
`Starting Telegram gateway (Phase 7 - Resilience)` line in the entire log
for the session — the PID never changes.

- [ ] **First contact:** `/start` in a fresh chat → exactly one message
      with the contract copy (confirmation + greeting, in that order).
      → observed text vs `requirements.md` copy
- [ ] **Aliases identical:** `/restart` at the greeting phase →
      byte-identical message to the `/start` reply.
- [ ] **Reset mid-interview:** answer a couple of questions, send
      `/restart` → reset reply; the command was **not** consumed as an
      answer (the log shows no `interview_answer` for `/restart`); the
      next photo starts at Q1 of a brand-new interview — no restart.
      → `reset_command` / `session_reset` lines + fresh `interview_question`
- [ ] **Reset during conversion/narration:** send `/start` the moment the
      interview closes (darkroom/narrator in flight) → the queued command
      is processed behind the in-flight stage (spec's in-flight rule);
      once messages settle, state is clean and a new photo works — still
      no restart.
      → log ordering + subsequent clean run
- [ ] **Reset after `DONE`:** full run to the voice note, then `/start`
      → reset reply; shell check shows `.tmp/media/<chat_id>/` is **gone**
      (`ls .tmp/media/`); a new photo begins a clean run.
- [ ] **TA-relevant — reset at every reachable phase leaves a clean state
      with NO process restart:** one pass covering awaiting-photo,
      mid-interview, mid-conversion/narration and post-`DONE`, all within
      a single bot process (one gateway-start line; PID unchanged).
- [ ] **TA-relevant — a second full run works immediately afterwards:**
      after the last reset, one complete happy path (photo → bouncer →
      interview → portrait → narration → playable voice note) with no
      server restart at any point.
- [ ] **Back-to-back runs:** at least two full runs separated only by
      `/restart`, zero gateway restarts in the log.
- [ ] **Non-command text still nudged live:** plain text at the greeting
      phase gets the portrait nudge, not a reset.
- [ ] **Log hygiene:** `reset_command`, `session_reset` and the
      `… -> AWAITING_PHOTO` transition present; no tracebacks containing
      secrets; no message text/bytes logged.

## Docs

- [ ] README's reset claim (line ~38) now matches shipped behaviour (no
      overclaim beyond this slice); reset is documented in the "Run the
      bot" section; status block honest.
- [ ] `SPECS/ROADMAP.md` Phase 7 has an `Implementation:` note for the
      reset slice and a status row reflecting only this slice (timeout &
      wrong-payload slices still pending).
- [ ] `.env` and `.tmp/` confirmed untracked (`git check-ignore`).

## Differences between spec and implementation

_Filled at validation time: record every deviation found between this spec
and what was built (house practice — see
`SPECS/2026-10-19-narrator/validation.md`), with the reason and whether the
user approved it._
