# Validation — Clear the chat on /start and /restart (Phase 7)

Every box is evidence, not intent: the command or log line that proves it goes
in the report when the box is ticked. Automated boxes are ticked by
`./scripts/test` / `./scripts/hooks`; live boxes need a real Telegram run.
Drift between spec and build is recorded at the bottom (house practice, see
`SPECS/2026-10-19-narrator/validation.md`).

## Automated

- [x] `SessionState.sent_message_ids` defaults to `[]`, appends in send order,
      resets on `clear()`, and is not shared between sessions.
      → `tests/unit/test_session_state.py::TestSentMessageIds` (4 passed).
- [x] `TelegramClient.delete_message` POSTs `deleteMessage` with `chat_id` +
      `message_id`, returns cleanly on 200, raises `TelegramApiError` on HTTP
      error, redacts the token, and is never retried.
      → `tests/unit/test_telegram_client.py::TestDeleteMessage` (3 passed).
- [x] The best-effort deletion helper deletes every tracked ID, continues
      past a failure, logs loudly, returns `(deleted, attempted)`, and never
      raises. → `tests/unit/test_reset_gateway.py::TestDeleteSentMessages`
      (4 passed).
- [x] Every send path records the message ID: `_send` text sends, direct
      `send_message` sites, `send_photo`, `send_voice`. A failed send records
      nothing. → `tests/unit/test_reset_gateway.py::TestSentMessageRecording`
      (6 passed).
- [x] On `/start`/`/restart`: tracked bot messages are deleted **before** the
      state/temp-media purge and before the reply; order asserted.
      → `tests/unit/test_reset_gateway.py::TestChatClear`.
- [x] The reset reply is sent after deletion and is **not** itself deleted;
      a repeat reset deletes the previous reply. → `tests/unit/test_reset_gateway.py::TestChatClear`.
- [x] A `deleteMessage` failure degrades: other messages still deleted, state
      cleared, `RESET_REPLY` sent, loud log, no raise.
      → `tests/unit/test_reset_gateway.py::TestChatClear`.
- [x] Brand-new chat: `deleted=0 of 0`, still replies, no error.
      → `tests/unit/test_reset_gateway.py::TestChatClear`.
- [x] `chat_clear chat_id=… deleted=… of=…` logged; no message text/secrets.
      → `tests/unit/test_reset_gateway.py::TestChatClear`.
- [x] `./scripts/test` and `./scripts/hooks` green.
      → `472 passed`; all five hooks pass (ruff, ruff-format, mypy, pytest,
      gitleaks).

## Live (real Telegram traffic)

- [ ] Run a full pipeline (photo → interview → portrait → narration → voice
      note), then send `/restart`: **every message the bot posted disappears**
      from the chat.
- [ ] The user's own sent photo/text **remain** (Telegram forbids a bot
      deleting them) — expected, not a bug.
- [ ] The `RESET_REPLY` arrives as the only recent bot message and is not
      deleted by its own reset.
- [ ] A second `/restart` deletes the previous reset reply and posts a fresh
      one.
- [ ] `chat_clear` appears in the log with a sensible `deleted` count.
- [ ] Two full runs back-to-back in one process, with only `/restart`
      between (no gateway restart line) — the TA scenario.

## Docs

- [x] README updated: reset also clears the bot's own messages (user's remain).
- [x] `SPECS/ROADMAP.md` Phase 7 Implementation note + status row updated.
- [x] `SPECS/TECH.md` records the v1.4 field and `deleteMessage`.
- [x] Differences between spec and implementation recorded (below).

## Differences between spec and implementation

- The spec suggested the helper return `(deleted, attempted)`; kept as written.
- The failure log line is emitted at `WARNING` (not `ERROR`/`exception`) and
  logs only the exception **type name** — `logger.exception` would echo the
  exception message, which the spec forbids (no secrets/text in logs). This is
  a deliberate tightening, not drift.
- All direct text `send_message` sites in `_handle_portrait` and the outer
  dispatch error path record their IDs too (the spec grouped them under "direct
  `send_message` sites"); no site was missed.
- `SessionState` schema bumped v1.3 → v1.4 for the new `sent_message_ids` field.
