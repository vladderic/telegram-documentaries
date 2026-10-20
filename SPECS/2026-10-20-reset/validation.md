# Validation — /start and /restart full reset (Phase 7)

Every box is evidence, not intent: the command or log line that proves it
goes in the report when the box is ticked. Automated boxes are ticked by
`./scripts/test` / `./scripts/hooks`; live boxes need a real Telegram run.
This file is written before implementation — boxes are ticked at validation
time, and any drift between spec and build is recorded at the bottom (house
practice, see `SPECS/2026-10-19-narrator/validation.md`).

Validated on `feature/2026-10-20-reset` at commit `f91c635`. Gate evidence:
`./scripts/test` → **449 passed in 15.02s**; `./scripts/hooks` → ruff,
ruff-format, mypy, pytest, gitleaks all **Passed**. Live Telegram validation
could not be performed from the validation environment (no bot credentials /
live traffic available) — live boxes are left unticked with that reason; the
two TA-relevant no-restart boxes carry automated evidence notes instead.

## Automated

### Contracts (`models.py`)

- [x] `MessageEntity` parses Telegram's shape with `type`/`offset`/`length`
      and ignores extras (`username`); negative offsets/lengths raise
      `ValidationError`; `Message.entities` defaults `None`; entity-less
      payloads validate exactly as before.
      → `tests/unit/test_models.py::TestMessageEntities` (6 tests: shape
      parse, extras ignored, negative offset/length rejected, raw-payload
      entities, default `None`, entity-less fixture payloads unchanged).

### Command recognition (`parse_reset_command`)

- [x] **Entity path:** `/start`, `/restart`, `/start@MyBot`, `/RESTART`,
      `/start with args` (entity covers only the command) all return the
      correct `Literal`.
      → `tests/unit/test_reset.py::TestParseResetCommandEntityPath` (6
      parametrised rows, plus `/Start` mixed case; the box's
      `TestParseResetCommand` reference resolves to these three actual
      classes — see Differences).
- [x] **Text-fallback path (no entities):** `/start`, `/restart`,
      `/start@MyBot …`, trailing whitespace and mixed case are recognised;
      a valid reset command is never silently missed.
      → `tests/unit/test_reset.py::TestParseResetCommandTextFallback`
      (7 recognised rows + `test_a_recognised_command_is_never_missed_when_the_client_sends_no_entities`).
- [x] **Narrowness:** `start`, `//start`, `/starting`, `/restarts`,
      `/foo`, `hello /start`, empty/whitespace text, `text is None`
      (photo-only, caption-only) all return `None` — they fall through to
      today's generic path unchanged.
      → `tests/unit/test_reset.py::TestParseResetCommandEntityPath::test_a_non_reset_command_returns_none`
      (5 rows),
      `TestParseResetCommandTextFallback::test_anything_else_returns_none`
      (8 rows),
      `TestParseResetCommandEdgeCases::test_a_message_without_text_returns_none`
      (photo-only, photo-with-entities, caption-only).
- [x] **Hostile input:** a `bot_command` entity at `offset != 0` and an
      out-of-bounds entity slice are handled without crash — the bad
      entity is ignored (warning logs numeric lengths only, never message
      text) and the fallback still decides.
      → `tests/unit/test_reset.py::TestParseResetCommandEdgeCases` —
      `test_a_bot_command_entity_not_at_offset_zero_falls_back_narrowly`,
      `test_an_out_of_range_entity_is_ignored_and_the_fallback_still_decides`
      (asserts exactly one warning containing `offset=0` / `length=999`
      and `"/restart" not in caplog.text`; the fallback still returns
      `restart`),
      `test_an_out_of_range_entity_over_text_that_is_not_a_command_returns_none`,
      `test_an_out_of_range_entity_on_empty_text_is_ignored`,
      `test_a_non_bot_command_entity_at_offset_zero_is_not_consulted`,
      `test_a_bot_command_entity_on_textless_message_is_safe`,
      `test_a_photo_or_file_with_a_command_caption_is_never_a_reset`.

### Media purge (`purge_chat_media`)

- [x] Purges `media_root/<chat_id>/` entirely and returns the file count;
      a **missing directory returns `0` without raising** (idempotent).
      → `tests/unit/test_reset.py::TestPurgeChatMedia` —
      `test_purges_the_chat_directory_and_returns_the_file_count` (2 files,
      dir gone), `test_a_missing_directory_returns_zero_and_never_raises`
      (twice), `test_an_empty_chat_directory_is_removed_and_returns_zero`,
      `test_a_file_not_found_from_the_filesystem_returns_zero`,
      `test_the_deletion_is_derived_from_chat_id_not_a_session_path`.
- [x] **Scoping:** a sibling chat directory and the media root itself
      survive; a symlinked chat directory pointing outside the root is
      refused (`ValueError`) with the outside target intact.
      → `tests/unit/test_reset.py::TestPurgeChatMedia` —
      `test_a_sibling_chat_directory_and_the_media_root_survive`,
      `test_a_symlinked_chat_directory_escaping_the_root_is_refused`,
      `test_a_chat_directory_symlinked_to_the_root_itself_is_refused`,
      `test_a_refusal_logs_an_error_before_raising`.
- [x] A non-`FileNotFoundError` `OSError` propagates (fail loud — the
      handler is what degrades).
      → `tests/unit/test_reset.py::TestPurgeChatMedia::test_a_non_file_not_found_oserror_propagates`.

### Gateway behaviour

- [x] **First-ever `/start` (no session, no media) sends exactly one
      message `== RESET_REPLY`, byte-identical to `/restart`'s reply**;
      session afterwards at `AWAITING_PHOTO`.
      → `tests/unit/test_reset_gateway.py::TestFirstContactAndAliases` —
      `test_first_ever_start_replies_once_and_leaves_a_clean_session`
      (exactly `[EXPECTED_REPLY]`, `media_files=0`, phase
      `AWAITING_PHOTO`) and
      `test_start_and_restart_replies_are_byte_identical`. The copy itself
      is locked verbatim to `requirements.md` by `TestResetCopy`.
- [x] **Reset from every phase:** parametrised over all eight `Phase`
      values → media purged on disk, all session fields cleared, phase
      `AWAITING_PHOTO`, `RESET_REPLY` sent — covering
      mid-interview and post-`DONE` at minimum (ROADMAP's "reset
      mid-every-phase starts clean").
      → `tests/unit/test_reset_gateway.py::TestResetFromEveryPhase::test_reset_clears_every_field_purges_media_and_replies[AWAITING_PHOTO|BOUNCER_CHECK|AWAITING_INTERVIEW|INTERVIEW|CONVERT|SCRIPT|NARRATE|DONE]`
      — all 8 params collected and passing; `assert_session_cleared`
      checks every `SessionState` field (phase, photo refs, mime type,
      `temp_media_path`, `script_text`, `voice_note_sent`, full interview
      buffer) and the seeded media dir is asserted gone.
- [x] **Mid-interview `/restart` is never an answer:** the Interviewer
      fake never receives the command text, no answer is recorded, the
      pending question/profile are discarded, media is purged — and a new
      photo dispatched **in the same process** starts a fresh Bouncer
      run.
      → `tests/unit/test_reset_gateway.py::TestMidInterviewReset` —
      `test_restart_mid_interview_is_never_an_answer` (interviewer call
      count unchanged, no call carries `"/restart"`, no `interview_answer`
      log line, buffer cleared, media purged) and
      `test_a_new_photo_in_the_same_process_starts_a_fresh_run`
      (`vision.check_photo.await_count == 2`, fresh interview with empty
      answers and a new portrait on disk — no-restart guarantee at unit
      level). Also
      `test_a_stale_message_queued_before_the_reset_is_nudged` for the
      stale-update row of the behaviour table.
- [x] **Idempotency:** a second reset right after the first returns the
      identical `RESET_REPLY` with `media_files=0` and no error.
      → `tests/unit/test_reset_gateway.py::TestIdempotency::test_a_second_reset_returns_the_identical_reply_with_zero_media`
      (both replies `== EXPECTED_REPLY`, caplog `media_files=1` then
      `media_files=0`).
- [x] **No routing regression:** `/starting`, `start`, `//start`, `/foo`
      and plain text still get `PHOTO_NUDGE` at the greeting phase; plain
      text mid-interview is still recorded as an answer.
      → `tests/unit/test_reset_gateway.py::TestNotCommandsKeepExistingBehaviour`
      (6 nudge rows,
      `test_plain_text_mid_interview_is_still_recorded_as_an_answer`,
      `test_an_entity_less_command_still_resets`,
      `test_a_photo_with_a_command_caption_follows_the_portrait_path`);
      `tests/unit/test_bot_gateway.py::TestBuildReply` and
      `tests/unit/test_interview_gateway.py` still pass in the full suite
      (449 green).
- [x] **Interception order:** the reset command never reaches
      `_handle_interview` / `build_reply` (asserted via fakes/logs).
      → `tests/unit/test_reset_gateway.py::TestInterceptionOrder::test_the_command_never_reaches_the_interviewer_or_build_reply`
      — both functions patched and `assert_not_called()`; only
      `RESET_REPLY` is sent. (Test read in full at validation time; it
      genuinely patches the module-level functions the router would call.)
- [x] **Purge failure degrades:** monkeypatched `rmtree` `OSError` →
      `RESET_REPLY` still sent, state still cleared, loud
      `temp media purge failed` exception line in caplog; the conversation
      never breaks.
      → `tests/unit/test_reset_gateway.py::TestPurgeFailureDegrades` —
      `test_an_oserror_from_the_purge_still_clears_and_replies` (asserts
      `"temp media purge failed chat_id=555"` in caplog) and
      `test_a_containment_guard_trip_still_clears_and_replies`.
- [x] **Cross-chat isolation:** another chat's media directory and
      session untouched by a reset.
      → `tests/unit/test_reset_gateway.py::TestCrossChatScoping::test_another_chats_media_and_session_are_untouched`
      (chat 777 keeps phase, answers, pending question, script and its
      portrait bytes).
- [x] **Log contract:** `reset_command … from_phase=…`,
      `session_reset … media_files=…` and `phase_transition … ->
      AWAITING_PHOTO` lines appear; no message text, no file bytes and no
      secret-looking values in the log.
      → `tests/unit/test_reset_gateway.py::TestLogContract` —
      `test_the_three_log_lines_appear_in_order` (exact strings, ordering
      asserted),
      `test_no_transition_is_logged_when_already_awaiting_photo`,
      `test_no_message_text_or_secrets_are_ever_logged` (command args and
      placeholder secrets absent from caplog at DEBUG).
- [x] **Full loop:** `run_bot` processes `[run, reset, second run]` batch
      side-effects to completion with no restart needed.
      → `tests/unit/test_reset_gateway.py::TestFullLoop::test_run_reset_and_a_second_full_run_need_no_restart`
      — one `run_bot` invocation with batches `[photo, 5 answers,
      /restart, photo, 5 answers]`; asserts `vision.check_photo == 2`,
      `send_photo == 2`, `send_voice == 2` (two complete pipelines
      including the narration stage), exactly one `RESET_REPLY`, and
      **exactly one** `Starting Telegram gateway (Phase 7 - Resilience)`
      line — the same-process evidence the live TA scenario demands,
      at mock level. (Voice-note *playability* is live-only.)
- [x] `SessionState.clear()` lock-in: every field (phase, photo refs,
      `temp_media_path`, `script_text`, `voice_note_sent`, full interview
      buffer) reset; sessions do not share state.
      → The plan's single `TestClearResetsEverything` class was not created
      as named; the same coverage exists, verified by reading:
      `tests/unit/test_session_state.py` —
      `test_clear_resets_to_awaiting_photo_and_purges_media`,
      `TestInterviewState::test_clear_resets_the_question_send_time`,
      `test_clear_resets_the_interview`, `test_sessions_do_not_share_interview_state`,
      `TestStoredPortraitMediaType::test_clear_resets_it_along_with_the_media_path`,
      `TestStoredScript::test_clear_resets_it`, `test_sessions_do_not_share_a_script`,
      `TestVoiceNoteSent::test_clear_resets_it`,
      `test_sessions_do_not_share_the_flag` — plus the every-field
      `assert_session_cleared` helper in `test_reset_gateway.py`, applied
      after real resets across all 8 phases (see Differences, item d).
- [x] Banner reads `(Phase 7 - Resilience)`.
      → `tests/unit/test_reset_gateway.py::TestFullLoop::test_the_banner_names_phase_7`
      (caplog equality check); the full-loop test additionally asserts the
      banner appears **exactly once** across the whole multi-batch run.
      Code: `bot_gateway.py:1425`.
- [x] `./scripts/test` green (whole suite, no regressions).
      → Ran at validation time: **`449 passed in 15.02s`**.
- [x] `./scripts/hooks` green: ruff, ruff-format, mypy, pytest, gitleaks.
      → Ran at validation time: ruff Passed, ruff-format Passed, mypy
      Passed, pytest Passed, "Detect hardcoded secrets" Passed.

## Live (real Telegram traffic, **no process restart**)

Drive everything from one Telegram chat against one running bot process.
The decisive evidence for the whole feature: **exactly one**
`Starting Telegram gateway (Phase 7 - Resilience)` line in the entire log
for the session — the PID never changes.

> **Not performed:** the validation environment has no Telegram bot
> credentials and cannot exchange live traffic, so no live box is ticked
> and no live evidence is fabricated. The two TA-relevant boxes below carry
> notes pointing at the automated tests that cover the same guarantee at
> mock level; a live pass remains outstanding for the whole section.

- [ ] **First contact:** `/start` in a fresh chat → exactly one message
      with the contract copy (confirmation + greeting, in that order).
      → *Not performed (no live bot). Automated coverage:*
      `TestFirstContactAndAliases` + `TestResetCopy` *(copy verified
      byte-for-byte against this spec's contract block).*
- [ ] **Aliases identical:** `/restart` at the greeting phase →
      byte-identical message to the `/start` reply.
      → *Not performed (no live bot). Automated coverage:*
      `test_start_and_restart_replies_are_byte_identical`.
- [ ] **Reset mid-interview:** answer a couple of questions, send
      `/restart` → reset reply; the command was **not** consumed as an
      answer (the log shows no `interview_answer` for `/restart`); the
      next photo starts at Q1 of a brand-new interview — no restart.
      → *Not performed (no live bot). Automated coverage:*
      `TestMidInterviewReset::test_restart_mid_interview_is_never_an_answer`
      *(no `interview_answer` log assertion included)*
      `+ test_a_new_photo_in_the_same_process_starts_a_fresh_run`.
- [ ] **Reset during conversion/narration:** send `/start` the moment the
      interview closes (darkroom/narrator in flight) → the queued command
      is processed behind the in-flight stage (spec's in-flight rule);
      once messages settle, state is clean and a new photo works — still
      no restart.
      → *Not performed (no live bot). The in-flight rule follows from the
      sequential-dispatch design (`_poll_updates`/`run_bot` drain one
      batch at a time); no automated test simulates real stage latency.*
- [ ] **Reset after `DONE`:** full run to the voice note, then `/start`
      → reset reply; shell check shows `.tmp/media/<chat_id>/` is **gone**
      (`ls .tmp/media/`); a new photo begins a clean run.
      → *Not performed (no live bot). Automated coverage: reset from
      `DONE` incl. disk purge — `TestResetFromEveryPhase[DONE]`.*
- [ ] **TA-relevant — reset at every reachable phase leaves a clean state
      with NO process restart:** one pass covering awaiting-photo,
      mid-interview, mid-conversion/narration and post-`DONE`, all within
      a single bot process (one gateway-start line; PID unchanged).
      → *Automated evidence provided; live run pending.*
      `TestResetFromEveryPhase` (all 8 `Phase` values, media purged +
      every field cleared) and `TestFullLoop::test_run_reset_and_a_second_full_run_need_no_restart`
      (one `run_bot` process; caplog asserts the gateway-start line
      appears exactly once across two full runs and a reset). A real
      Telegram pass with the PID/gateway-line check is still outstanding.
- [ ] **TA-relevant — a second full run works immediately afterwards:**
      after the last reset, one complete happy path (photo → bouncer →
      interview → portrait → narration → playable voice note) with no
      server restart at any point.
      → *Automated evidence provided; live run pending.*
      `TestFullLoop::test_run_reset_and_a_second_full_run_need_no_restart`
      drives `photo → interview (5 answers, FINISH) → portrait → script →
      voice`, then `/restart`, then the same complete path again in the
      same process (`send_photo.await_count == 2`, `send_voice.await_count
      == 2`, one gateway-start line). Voice-note *playability* against the
      real Telegram API can only be confirmed live.
- [ ] **Back-to-back runs:** at least two full runs separated only by
      `/restart`, zero gateway restarts in the log.
      → *Not performed (no live bot). Automated coverage:* `TestFullLoop`
      *(exactly one gateway-start line asserted).*
- [ ] **Non-command text still nudged live:** plain text at the greeting
      phase gets the portrait nudge, not a reset.
      → *Not performed (no live bot). Automated coverage:*
      `TestNotCommandsKeepExistingBehaviour`.
- [ ] **Log hygiene:** `reset_command`, `session_reset` and the
      `… -> AWAITING_PHOTO` transition present; no tracebacks containing
      secrets; no message text/bytes logged.
      → *Not performed (no live bot). Automated coverage:*
      `TestLogContract` *(exact-line, ordering and no-secrets/no-text
      assertions at unit level).*

## Docs

- [x] README's reset claim (line ~38) now matches shipped behaviour (no
      overclaim beyond this slice); reset is documented in the "Run the
      bot" section; status block honest.
      → Verified by reading `README.md`: status block says "Phase 7 (reset
      slice)" and describes the shipped in-process reset (lines 8, 21–24);
      the post-pipeline paragraph (lines 41–45) matches the implementation
      (back to awaiting-photo, chat-scoped purge, one reply, any phase, no
      restart); "Run the bot" stage 6 documents the reset incl.
      interception-before-stages and loud-logged failed purges; line 130
      tells the user to recover with `/start` instead of restarting.
- [x] `SPECS/ROADMAP.md` Phase 7 has an `Implementation:` note for the
      reset slice and a status row reflecting only this slice (timeout &
      wrong-payload slices still pending).
      → Verified: Phase 7 `Implementation (reset slice, 2026-10-20):`
      names `MessageEntity`/`entities`, `parse_reset_command`,
      `purge_chat_media`, the copy constants, `_handle_reset`, the
      interception point and the banner, and states the other slices
      remain pending; status row reads "In progress — reset slice
      (`/start` / `/restart`) complete; timeout & wrong-payload slices
      pending".
- [x] `.env` and `.tmp/` confirmed untracked (`git check-ignore`).
      → `git check-ignore -v` → `.gitignore:5:.env` and
      `.gitignore:24:.tmp/`; `git ls-files` shows no tracked `.env` or
      `.tmp` path. Also confirmed `SPECS/2026-10-20-reset/` (all three
      files) **is tracked** — committed in `7dcde04`.

## Differences between spec and implementation

_Filled at validation time (2026-10-20) against commit `f91c635`._

a. **Copy dash: ASCII " - " vs the em dash in the original requirements
   block.** The requirements copy contract originally showed an em dash in
   `RESET_CONFIRM_MSG`; every other `*_MSG` constant in `bot_gateway.py`
   uses the house ASCII form " - ". The spec file was amended to the
   ASCII-hyphen form before the spec folder was committed (commit
   `7dcde04`: "with the copy contract amended to the house ASCII-dash
   form"); the code matches the amended contract exactly and
   `TestResetCopy::test_the_confirmation_keeps_the_house_ascii_dash_convention`
   locks the convention in. Approved: user-directed house convention.

b. **Hybrid portrait and voice note are never on disk.** The original
   feature request's purge wording ("converted PNG, generated OGG/MP3
   voice files") presumes those artefacts exist on disk. They do not: only
   the *original* accepted portrait is written (`_store_photo`); the
   hybrid portrait is uploaded from in-memory `GeneratedImage` bytes and
   the voice note from in-memory `VoiceNote` bytes (`VOICE_FILENAME` is a
   multipart upload name only; Phase 6 encodes MP3, and no OGG exists in
   this pipeline). There is therefore nothing of theirs to delete.
   Reconciliation (stated in requirements.md recon item 3): the purge
   deletes the chat's **whole** per-chat temp-media directory — the rule
   that covers every file that can ever live there — and the hybrid/voice
   artefacts are memory-only by design. Behaviour matches the spec; only
   the original request's wording was imprecise.

c. **This slice is only part of Phase 7.** Timeout fallbacks and
   wrong-payload-at-wrong-stage guards are separate Phase 7 slices and are
   **not** implemented. README status ("Phase 7 (reset slice)"), ROADMAP's
   Implementation note and the status row all say so explicitly — no
   overclaim remains.

d. **`TestClearResetsEverything` was not created as a single named class.**
   Plan task 8 asked for one lock-in class in `tests/unit/test_session_state.py`;
   instead the identical field-by-field coverage lives in the pre-existing
   per-field tests there (phase, photo refs, mime type, `temp_media_path`,
   `script_text`, `voice_note_sent`, interview answers/pending
   question/profile/`question_sent_at`, non-shared session state — each
   listed in the box above), reinforced by `assert_session_cleared` in
   `test_reset_gateway.py`, which is applied after **real** resets from all
   8 phases. Behaviourally equivalent lock-in; only the class name/placement
   differs from the plan. The validation box's evidence reference was
   reworded to the tests that actually exist.

e. **Stale-comment line numbers shifted.** Requirements item 4 pointed at
   `bot_gateway.py:551` and `:1001`; after the implementation those
   docstrings live at ~555 (`_store_photo` — now truthfully says the
   directory is deleted by `purge_chat_media` on `/start`/`/restart`) and
   ~1126 (`_handle_portrait` — now says "or send `/start` / `/restart` to
   reset the whole session outright (Phase 7)"). Both aspirational
   references were refreshed to describe real behaviour; no residual "no
   `/start`" wording remains.

f. **Validation-box evidence references adjusted to real test names.**
   The pre-written boxes cited `tests/unit/test_reset.py::TestParseResetCommand`
   and `tests/unit/test_reset_gateway.py` "(row)" phrasing; the actual
   classes are `TestParseResetCommandEntityPath` /
   `TestParseResetCommandTextFallback` / `TestParseResetCommandEdgeCases`
   and the named gateway classes recorded per box above. Tick references
   were reworded at validation time to point at the tests as written.

g. **Live validation not performed.** The whole Live section is pending a
   real Telegram run (no credentials/traffic in the validation
   environment). The two TA-relevant no-restart boxes have substantial
   automated evidence (`TestMidInterviewReset` same-process second run;
   `TestFullLoop` two full pipelines + reset in one `run_bot` process with
   exactly one gateway-start line) and are annotated "automated evidence
   provided; live run pending" rather than ticked. Voice-note playability
   against the real Telegram API is live-only by nature.
