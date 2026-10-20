# Requirements — Clear the chat on /start and /restart (Phase 7)

Roadmap phase: **7 — Resilience** (chat-clear slice). Constitution:
`SPECS/MISSION.md`, `SPECS/TECH.md`, `SPECS/ROADMAP.md`. Predecessor:
`SPECS/2026-10-20-reset/` (the state/temp-media reset this slice extends).

## Context

The reset slice (`SPECS/2026-10-20-reset/`) made `/start` and `/restart`
wipe the bot's **in-memory session state** and delete the chat's **temp media
on disk**. Its live test then surfaced a gap the user hit immediately:

> "the commands work but ... they only start fresh but the whole previous
> chat history is still kept."

The bot's own replies — interview questions, hold-lines, the generated hybrid
portrait, the narration text, the voice note — stay visible in the Telegram
conversation. Because the assignment is **graded by TAs who test the bot
repeatedly, back-to-back in the same chat**, the accumulated history makes
each run look like it never reset, and a TA cannot tell a fresh run from a
leftover one at a glance.

This slice makes `/start` and `/restart` **also delete every message the bot
itself sent** in that chat, so a reset visibly clears the bot's side of the
conversation.

## Facts established before writing this spec (code reconnaissance)

1. **Every outbound message passes through exactly three methods** on
   `TelegramClient` (`bot_gateway.py`):
   - `send_message` (`:382`),
   - `send_photo` (`:397`),
   - `send_voice` (`:430`).
   The shared helper `_send(...)` (`:690`) and every direct call site all
   route through these three. There are ~30 call sites (questions,
   confirmations, hold-lines, retries, nudges, the bouncer verdicts, the
   portrait, the narration, the voice note, the reset reply), but only three
   places actually perform a send — so **message-ID tracking has a single,
   small chokepoint** rather than needing edits at every call site.
2. All three methods return `await self._request(...)` — the **raw Telegram
   JSON payload**. A successful `sendMessage` / `sendPhoto` / `sendVoice`
   response carries `result.message_id`. The bot can therefore learn the ID
   of every message it sends, at the point it sends it.
3. `Message` and `message_id: int` already exist in `models.py` (`:130`,
   `:137`) — the shape needed to read `result.message_id` is already known to
   the codebase.
4. `SessionState` (`models.py:415`) is currently **v1.3**; `clear()`
   (`:448`) resets every field. A new tracked-ID list is a schema bump to
   **v1.4**, and `clear()` must reset it like every other field.
5. `deleteMessage` is a standard Bot API method: one call deletes one
   message. A bot may delete **its own** messages; deleting a **user's**
   incoming message in a private chat is not permitted (see Limits below).
6. The reset already has a handler `_handle_reset` and a first-branch
   interception point in `_dispatch_update` (added by the reset slice). This
   slice **extends** that handler: after the existing state/temp-media reset,
   it deletes the tracked messages, then sends the reset reply.

## Confirmed user decisions (fixed requirements)

1. **Delete everything the bot sent** on `/start` or `/restart`: interview
   questions, confirmations, hold-lines, retry/nudge lines, the generated
   hybrid portrait, the narration text, the voice note — every message the
   bot posted in that chat and still tracks.
2. **Best-effort, and honest about the limit:** the bot **cannot delete the
   user's own messages** (Telegram platform restriction). After a reset the
   chat will still show the user's sent photos/text, but **none of the bot's
   replies**. The user has explicitly accepted this.
3. **Only what the current in-memory session tracked is deleted.**
   MISSION.md forbids persistence (no database), so tracked IDs live on
   `SessionState` and are lost on a bot restart or a prior reset. Messages
   from prior runs / prior restarts cannot be deleted later. The user has
   accepted this; the behaviour is best-effort with a clear log.
4. `/start` and `/restart` remain **behaviourally identical aliases** (per
   the reset spec).
5. **The reset reply itself is not deleted by its own reset.** The
   confirmation+greeting the user is reading must survive; it becomes
   deletable by the *next* reset (i.e. it is tracked, so a subsequent reset
   clears it). Rationale: deleting the message you just sent to confirm the
   reset would leave the TA staring at an empty chat with no confirmation
   that anything happened.

## Behaviour

| Situation | Behaviour |
|---|---|
| `/start` or `/restart`, bot has sent messages this session | Delete every tracked bot message (best-effort), purge state + temp media, send `RESET_REPLY` |
| `/start` in a brand-new chat (no session, nothing sent) | No messages to delete; state cleared; `RESET_REPLY` sent; no error |
| Repeat reset immediately after one | The previous `RESET_REPLY` (tracked) is deleted, a new one is sent — chat keeps exactly one reset reply at a time; `deleted=1`-style log |
| Some tracked messages already gone / too old (48 h window) | Those deletions fail; each failure logged; the rest are deleted; state still cleared; `RESET_REPLY` still sent; `deleted=N of M` |
| `deleteMessage` transport/HTTP failure | Loud `logger.exception`; reset continues; state cleared; `RESET_REPLY` sent; never raises into the conversation |
| User's own messages (photos/text) | **Untouched** — Telegram forbids a bot deleting them; not attempted, not logged as failure |
| Same session, second full run after a reset | Works in-process; each run's messages are tracked and cleared by the next reset |
| Message sent while session is mid-flight | Tracked at send time (the three send methods record it); a reset that arrives later deletes it |

### Typed contracts (at the boundary — TECH.md)

- `SessionState.sent_message_ids: list[int] = Field(default_factory=list)`
  — schema **v1.4**. IDs are appended in send order; `clear()` resets it to
  empty. Never persisted (in-memory only, MISSION).
- Tracking happens **inside the three send methods** (or a thin helper they
  share), not at the ~30 call sites: after a successful send, parse
  `result.message_id` and append it to the session — but the send methods do
  **not** currently receive the session. Design choice to specify in the
  plan: pass the session (or a tracker callback) into the send path, or have
  the send methods return the ID and the caller record it. **Recommendation:
  the send methods return the payload as today; a small `_record_sent(...)`
  helper appends the ID to the session at the existing `_send` chokepoint
  plus the three direct `send_photo`/`send_voice`/`send_message` sites.**
  The plan must pick ONE approach and apply it everywhere; no send may be
  missed.
- `TelegramClient.delete_message(chat_id: int, message_id: int) -> None` —
  calls `deleteMessage`; failures raise `TelegramApiError` (token-free).
  **Never retried** (a retry cannot make a gone message deletable; the reset
  is best-effort and logs failures).
- `_handle_reset` extends to: log recognition → **delete tracked messages
  (best-effort)** → purge temp media → `session.clear()` → `RESET_REPLY`.
  Order rationale: delete first so IDs are still on the session, then clear
  (which empties the list), then reply (which is tracked for the next reset).

### Failure & logging

- INFO event lines (house style): `chat_clear chat_id=%s deleted=%s of=%s`
  after the deletion pass; failures via `logger.exception("failed to delete
  message chat_id=%s message_id=%s")`.
- **Never logged:** secrets, message text, file bytes. Message IDs and
  counts are fine.
- Every failure degrades: the reset still completes (state cleared, reply
  sent). No bare `except`; nothing swallowed unlogged (TECH.md).

### Limits (stated honestly, accepted by the user)

1. **A bot cannot delete a user's incoming messages** in a private chat —
   the user's own photos/text always remain. Platform restriction.
2. **Messages from before the last reset / before a restart are untracked**
   (in-memory only) and cannot be deleted later.
3. **Telegram's delete window** (~48 h) may make very old tracked messages
   undeletable; those failures are logged and skipped.

### Scope cuts (YAGNI)

- No persistence of message IDs; no database (MISSION).
- No cross-chat deletion; no deletion outside the requesting `chat_id`.
- No scheduled/retroactive cleanup, no sweeper, no TTL.
- No message editing, no `deleteMessages` batch optimisation (single-message
  calls only; note the batch form as a future option).
- No new commands; `/start` and `/restart` only.

## Success criteria (maps to ROADMAP Phase 7)

- After `/start` or `/restart`, **no message the bot sent earlier is left in
  the chat** (best-effort within Telegram's rules); the user's own messages
  remain, by design.
- The reset reply is always delivered, exactly one at a time, and never lost
  to the deletion pass.
- Failures (gone/too-old/transport) never break the reset and are logged.
- Idempotent and safe from every phase; no process restart ever required.
- `./scripts/test` and `./scripts/hooks` fully green; secrets never logged.

## Documentation deltas (contract for implementation)

1. **README.md** — the reset description gains "and deletes the messages the
   bot sent in that chat (best-effort; a bot cannot delete your own
   messages)". Keep the status honest (still a Phase 7 slice).
2. **SPECS/ROADMAP.md** — Phase 7 `Implementation:` note extended with this
   slice (message tracking, `delete_message`, chat-clear on reset); status
   row updated to reflect both reset slices done, timeout & wrong-payload
   guards still pending.
3. **SPECS/TECH.md`** — record the v1.4 `SessionState` field and the
   `deleteMessage` boundary method.
