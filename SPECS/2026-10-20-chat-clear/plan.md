# Plan — Clear the chat on /start and /restart (Phase 7)

Red/Green TDD throughout: write the failing test first, then the minimum code
that makes it pass. Run checks with the dev scripts (`scripts/test`,
`scripts/hooks` — ground truth, see README / `SPECS/TECH.md`).

Keep the repo's gates green at every step: run `./scripts/test` after each
task group and `./scripts/hooks` before committing.

## Task group 1 — Message-ID tracking contract (no network)

1. Red tests in `tests/unit/test_session_state.py`: `SessionState.v1.4` has
   `sent_message_ids: list[int]` defaulting to `[]`; it appends in send
   order; `clear()` resets it to empty; two sessions do not share the list
   (mutable-default safety).
2. Implement the field in `models.py` (schema comment → v1.4) and reset it in
   `clear()`.

## Task group 2 — `deleteMessage` boundary

3. Red tests in `tests/unit/test_telegram_client.py` (httpx MockTransport):
   `delete_message(chat_id, message_id)` POSTs `deleteMessage` with
   `chat_id` + `message_id`; a 200 returns cleanly; an HTTP error raises
   `TelegramApiError`; the token never appears in the message; the call is
   **never retried** (one attempt recorded).
4. Implement `TelegramClient.delete_message`.
5. Red tests for `_delete_sent_messages(client, session) -> tuple[int, int]`
   (or equivalent best-effort helper): deletes each tracked ID, returns
   `(deleted, attempted)`; a failure on one ID does not stop the others; the
   failure is logged loudly; never raises.
6. Implement the helper.

## Task group 3 — Track every send

7. Red tests proving that after a send, the message ID is recorded on the
   session. Cover **all three chokepoints**: `_send` (text), the direct
   `send_message` sites, `send_photo`, and `send_voice`. A send that fails
   records nothing (no phantom IDs). Choose ONE mechanism (recommended:
   the send methods return the payload as today and a small
   `_record_sent(session, payload)` helper is called at the `_send`
   chokepoint plus the direct `send_photo`/`send_voice`/`send_message` call
   sites) and apply it everywhere — enumerate every site from
   `bot_gateway.py` so none is missed.
8. Implement tracking. Verify with a gateway-level test that a full
   pipeline records a message ID for the portrait and the voice note (the
   multipart sends), not just text.

## Task group 4 — Wire chat-clear into the reset

9. Red tests in `tests/unit/test_reset_gateway.py` (extend the existing
   harness):
   - happy path: a run that sent messages, then `/restart` → every tracked
     bot message is deleted (`delete_message` awaited once per ID), in the
     right order (delete → purge → clear → reply).
   - the reset reply is sent **after** deletion and is **not** among the
     deleted IDs (it is tracked for the *next* reset).
   - a repeat reset deletes the previous reset reply (tracked) and sends a
     new one.
   - a `deleteMessage` failure on one ID: others still deleted, state
     cleared, `RESET_REPLY` sent, loud log, no raise.
   - brand-new chat: nothing to delete, `deleted=0 of 0`, still replies.
   - `chat_clear chat_id=… deleted=… of=…` log line present; no message text
     / secrets logged.
10. Extend `_handle_reset` to call the deletion helper before the purge, and
    emit the `chat_clear` log line. Ensure `session.clear()` (which empties
    the list) happens **after** deletion.

## Task group 5 — Docs & hygiene

11. Update README (reset now clears the bot's own messages; user's messages
    remain), `SPECS/ROADMAP.md` (Phase 7 Implementation note + status row),
    `SPECS/TECH.md` (v1.4 field + `deleteMessage`).
12. Confirm `.env` and `.tmp/` remain untracked.

## Task group 6 — Full gate & live validation

13. `scripts/test` green (all new + existing, no regressions).
14. `scripts/hooks` green: ruff, ruff-format, mypy, gitleaks.
15. Live: run a full pipeline (photo → interview → portrait → narration →
    voice note), then `/restart`; confirm every message **the bot** posted
    disappears and the user's own messages remain. Record the `chat_clear`
    line.

## Out of scope

Persistence of IDs, cross-chat cleanup, scheduled/retroactive sweeps,
message editing, `deleteMessages` batching, the other Phase 7 slices
(timeout fallbacks, wrong-payload guards), Phase 8 deployment.
