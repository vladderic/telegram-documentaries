# Validation — Clear the chat on /start and /restart (Phase 7)

Every box is evidence, not intent: the command or log line that proves it goes
in the report when the box is ticked. Automated boxes are ticked by
`./scripts/test` / `./scripts/hooks`; live boxes need a real Telegram run.
Drift between spec and build is recorded at the bottom (house practice, see
`SPECS/2026-10-19-narrator/validation.md`).

## Automated

- [ ] `SessionState.sent_message_ids` defaults to `[]`, appends in send order,
      resets on `clear()`, and is not shared between sessions.
      → `tests/unit/test_session_state.py`.
- [ ] `TelegramClient.delete_message` POSTs `deleteMessage` with `chat_id` +
      `message_id`, returns cleanly on 200, raises `TelegramApiError` on HTTP
      error, redacts the token, and is never retried.
      → `tests/unit/test_telegram_client.py`.
- [ ] The best-effort deletion helper deletes every tracked ID, continues
      past a failure, logs loudly, returns `(deleted, attempted)`, and never
      raises. → `tests/unit/test_reset_gateway.py` (or a sibling).
- [ ] Every send path records the message ID: `_send` text sends, direct
      `send_message` sites, `send_photo`, `send_voice`. A failed send records
      nothing. → `tests/unit/test_reset_gateway.py` /
      `tests/unit/test_telegram_client.py`.
- [ ] On `/start`/`/restart`: tracked bot messages are deleted **before** the
      state/temp-media purge and before the reply; order asserted.
      → `tests/unit/test_reset_gateway.py`.
- [ ] The reset reply is sent after deletion and is **not** itself deleted;
      a repeat reset deletes the previous reply. → `tests/unit/test_reset_gateway.py`.
- [ ] A `deleteMessage` failure degrades: other messages still deleted, state
      cleared, `RESET_REPLY` sent, loud log, no raise.
      → `tests/unit/test_reset_gateway.py`.
- [ ] Brand-new chat: `deleted=0 of 0`, still replies, no error.
      → `tests/unit/test_reset_gateway.py`.
- [ ] `chat_clear chat_id=… deleted=… of=…` logged; no message text/secrets.
      → `tests/unit/test_reset_gateway.py`.
- [ ] `./scripts/test` and `./scripts/hooks` green.
      → record the test count; all five hooks pass.

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

- [ ] README updated: reset also clears the bot's own messages (user's remain).
- [ ] `SPECS/ROADMAP.md` Phase 7 Implementation note + status row updated.
- [ ] `SPECS/TECH.md` records the v1.4 field and `deleteMessage`.
- [ ] Differences between spec and implementation recorded (below).

## Differences between spec and implementation

_(filled in during validation)_
