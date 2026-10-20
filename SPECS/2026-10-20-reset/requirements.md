# Requirements — /start and /restart full reset (Phase 7)

Roadmap phase: **7 — Resilience** (reset slice only). Constitution:
`SPECS/MISSION.md`, `SPECS/TECH.md`, `SPECS/ROADMAP.md`.

## Context

MISSION.md promises it ("`/start` or `/restart` at any point purges all
session state and temp media, and the conversation begins fresh"), README.md
(line 38) and ROADMAP Phase 7 claim it works — **but nothing in the code
implements it.** No `/start` or `/restart` handler exists
(`bot_gateway.py`), `SessionState.clear()` (`models.py:448`) resets
in-memory state but is only wired to Bouncer rejections/failures, and there
is **no temp-media deletion anywhere in the codebase**. This is a
documentation/reality gap, and ROADMAP Phase 7's first acceptance clause
("*reset mid-every-phase starts clean*") is currently unimplemented.

Why it matters beyond the spec sheet: this is closed-source-graded
coursework that **teaching assistants will test repeatedly and
back-to-back**. A stuck or interrupted session must be recoverable by the
user, **in-process** — never by restarting the server between tests. Reset
must work from every phase, idempotently, with no process restart ever
required.

## Facts established before writing this spec (code reconnaissance)

1. **`/start` and `/restart` do not exist.** The only mentions of either
   are docstrings referring to them aspirationally
   (`bot_gateway.py:551`, `bot_gateway.py:1001`). `Message` has no
   `entities` field (`models.py:112`), so Telegram's own command markup is
   currently discarded at the boundary (`extra="ignore"`).
2. **Session state is in-memory only** (MISSION: no database), keyed by
   `chat_id` in `run_bot`'s `sessions` dict. `SessionState.clear()`
   (`models.py:448`) already resets *every* field — phase,
   `photo_file_id`, `photo_local_path`, `photo_mime_type`,
   `temp_media_path`, `script_text`, `voice_note_sent`, and the whole
   `interview` buffer (answers, pending question, profile,
   `question_sent_at`). It needs wiring and tests, not changes.
3. **Only one file is ever written to disk in the entire pipeline:**
   `_store_photo` (`bot_gateway.py:541-557`) writes the accepted portrait's
   original bytes to `.tmp/media/<chat_id>/portrait.jpg|png|webp|gif`
   (`DEFAULT_MEDIA_ROOT = ".tmp/media"`, `bot_gateway.py:76`).
   - The **hybrid portrait** (Converter output) is uploaded to Telegram
     from in-memory `GeneratedImage` bytes — never written to disk.
   - The **voice note** is uploaded from in-memory `VoiceNote` bytes;
     `VOICE_FILENAME = "narration.mp3"` (`bot_gateway.py:202`) is only the
     multipart upload filename, not a path on disk. No OGG file exists
     anywhere in this pipeline (Phase 6 encodes MP3 via `lameenc`).
   - Reconciliation with the feature request's "converted PNG, generated
     OGG/MP3 voice files": those files **genuinely never exist on disk**,
     so there is nothing to delete for them. Reset therefore purges the
     chat's whole temp-media directory — the rule that covers every file
     that can ever live there (today and any added later) — and this spec
     states plainly that the hybrid portrait and voice note are memory-only.
4. **Command recognition conflicts (why interception point matters):**
   text at `AWAITING_PHOTO` gets `PHOTO_NUDGE` via `build_reply`
   (`bot_gateway.py:497-516`); text inside `INTERVIEW_PHASES` is routed to
   the Interviewer and would be **recorded as an interview answer**
   (`_handle_interview`, `bot_gateway.py:919`). `/start`/`/restart` must be
   intercepted before both, otherwise a reset command mid-interview
   corrupts the dossier.
5. **Dispatch is strictly sequential:** `run_bot` `await`s
   `_dispatch_update` for each update and only polls Telegram again after
   the batch has drained (`bot_gateway.py:1259-1272`). Two updates are
   never handled concurrently; no locking exists or is needed.
6. **No greeting exists.** The only fixed awaiting-photo copy is
   `PHOTO_NUDGE` (a nudge, not a welcome). The spec defines the greeting.
7. **Gateway tests drive `run_bot`** with mocked
   `TelegramClient.get_updates` batch side-effects and
   `media_root=str(tmp_path)`; `_dispatch_update` is a module-level
   function taking an explicit `sessions` dict (direct state assertions
   are possible).

## Confirmed user decisions (fixed requirements)

1. **`/start` and `/restart` are behaviourally identical aliases** — both
   perform a full reset. There is no separate "welcome-only" path.
2. **A first-ever `/start` (no prior session) replies identically** to
   `/restart` — same copy, same single message, whether or not a session
   or media existed.
3. **The reply is ONE message: reset confirmation followed by the normal
   greeting**, so a TA immediately sees the reset worked and what to do
   next. Exact copy fixed below (no greeting existed; it is defined here).
4. **Full reset** = terminate any pending interview/waiting state for that
   `chat_id`, delete that `chat_id`'s temp media directory on disk, clear
   conversation/interview buffers, and return the session to
   `Phase.AWAITING_PHOTO`.
5. **Works from any phase, idempotently** — including brand-new chats,
   missing directories, already-deleted files, and repeated resets; never
   errors the user.
6. **Recognition is entity-based first, with a narrow text fallback**
   (both specified precisely below). Rationale (user decision): a silent
   miss is exactly the "stuck state" failure this feature exists to
   prevent, and TAs must never hit it — but the fallback stays narrow so
   `/starting`, `start`, `//start`, `/foo` fall through unchanged.
7. **The gateway banner is bumped to `(Phase 7 - Resilience)`** as part of
   this slice.
8. **No process restart is ever required** for reset, recovery, or a
   second run — enforced by the live checklist.

## Greeting & confirmation copy (contract — exact strings)

```python
RESET_CONFIRM_MSG = (
    "Very well - the cutting room floor is clear. Every previous take, "
    "note and frame of you has been struck from the archive."
)
GREETING_MSG = (
    "A fresh subject! The crew has been ready. Send me a photograph and "
    "we will get you on camera."
)
#: The reply to /start and /restart: one message, always this exact text.
RESET_REPLY = f"{RESET_CONFIRM_MSG}\n\n{GREETING_MSG}"
```

Both are module-level constants in `bot_gateway.py`, in the existing
`*_MSG` style and voice (wildlife-documentary persona). The reply is always
`RESET_REPLY` — confirmation and greeting travel together, never split.

## Behaviour

| Situation | Behaviour |
|---|---|
| `/start` or `/restart` in a **brand-new chat** (no session ever created, no media) | Identical `RESET_REPLY` (one message) as any other reset; session exists afterwards at `AWAITING_PHOTO`; `session_reset … media_files=0`; no error, no special-casing |
| `/restart` mid-interview (`AWAITING_INTERVIEW` / `INTERVIEW`) | Intercepted **before** the interview routing — never recorded as an answer, never shown to the Interviewer; pending question, answers and profile discarded; media purged; `RESET_REPLY` |
| `/start` at `AWAITING_PHOTO` (already at greeting, e.g. after a Bouncer rejection) | Idempotent: identical `RESET_REPLY`, purge no-op if no media, no error |
| `/start` at `BOUNCER_CHECK`, `CONVERT`, `SCRIPT`, `NARRATE` | Reset works from every `Phase` value (table-driven in tests); normally these phases only exist while an update is being handled — a command typed then waits behind the in-flight update (see "In-flight work" below) |
| `/start` after `DONE` | Purge + clear + `RESET_REPLY`; the next photo starts a completely clean run in the same process |
| **Repeat reset** (second `/start` immediately after one) | Byte-identical `RESET_REPLY`, `media_files=0`, no error (idempotent) |
| Command variants: `/start`, `/restart`, `/start@MyBot`, `/RESTART`, trailing whitespace, `/start with args` | All recognised (entity slice covers the command; name compared case-insensitively; `@suffix` and arguments ignored) |
| Text fallback: `/start` or `/restart` as `text.strip()`'s first token with **no command entity present** | Recognised by the narrow fallback (below) |
| **Not** commands: `start`, `//start`, `/starting`, `/restarts`, `/foo`, leading text before the slash, empty/whitespace-only text, a photo/file with a command *caption* | Unchanged existing behaviour — `PHOTO_NUDGE` at the greeting phase, answer/nudge mid-interview; never a reset |
| Hostile/garbled entity (offset/length out of range for the text) | Entity ignored with a logged warning (lengths only, never message content); the narrow text fallback still decides — a malformed payload can never produce a crash or a silent miss |
| Media directory missing / file already gone | Purge no-op (`media_files=0`), still `RESET_REPLY`, no error |
| Purge fails (`OSError`) or containment guard trips (`ValueError`) | Loud `logger.exception`; **state is still cleared and `RESET_REPLY` still sent** — the conversation path degrades, never raises |
| Confirmation message send fails | Reset (state + media) is already complete; the send failure is logged loudly by `_send`; no raise, no retry |
| Another chat's session/media | Untouched — deletion is scoped to `media_root/<this chat_id>/` only; the media root itself and sibling chat directories survive |
| Stale messages queued before the reset (arrive after it) | Handled at the greeting phase: text gets `PHOTO_NUDGE`, a photo starts a fresh run — never recorded as interview answers (the interview buffer and phase are gone) |

### Command recognition (precise — schemas first, one narrow fallback)

**Primary — the typed entity path (schema, no parsing):**

- New `MessageEntity(BaseModel)` in `models.py`: `type: str`,
  `offset: int`, `length: int` (both validated `>= 0`), `extra="ignore"`
  (Telegram sends `username`, `language`, …). `Message` gains
  `entities: list[MessageEntity] | None = None` — payloads without
  entities keep validating exactly as before.
- If `message.entities` contains an entity with `type == "bot_command"`
  **and** `offset == 0`, its name is the slice
  `text[offset : offset + length]`, **bounds-checked first**: an
  out-of-range slice means the entity is treated as absent (logged
  warning, lengths only).
- The slice (or fallback token, below) normalises to a command name by:
  requiring a leading `/`, dropping exactly that one `/`, cutting at the
  first `@` or whitespace, and lowercasing. The result must be exactly
  `start` or `restart` — so `//start` → `/start` (rejected), `/starting`
  → `starting` (rejected), `/start@MyBot` → `start` (accepted).

**Fallback — narrow first-token text match (user decision):**

- Consulted whenever the entity path yields no recognised name (no
  entities, none at offset 0, out-of-bounds, or not a reset command).
- `text.strip()`'s first whitespace-delimited token, normalised by the
  same rule above, must be `/start` or `/restart`. Anything else
  (`start`, `//start`, `/starting`, `/foo`, `hello /start`) is **not** a
  command.
- Both paths read the same leading token of the same text, so they cannot
  disagree about a valid command; the fallback exists only to catch
  entity-less clients.

**Boundary function:**

```python
def parse_reset_command(message: Message) -> Literal["start", "restart"] | None
```

Pure, synchronous, regex-free; returns `None` for any message that is not
a reset command (including `text is None`). The returned name is used only
for the log line — both values mean the same action.

### Media purge contract

```python
def purge_chat_media(media_root: str | Path, chat_id: int) -> int
```

- Deletes the tree at `Path(media_root) / str(chat_id)`; returns the
  number of files removed.
- **Absent directory (or a file already gone) → returns `0`, never
  raises** (`FileNotFoundError` handled explicitly — the common idempotent
  path).
- **Containment guard (defence in depth):** both paths are resolved and
  deletion is refused unless the target resolves to a *strict child* of
  the media root and is not the root itself; a violation raises
  `ValueError` after an error log (test: a symlinked chat directory
  pointing outside the root is left intact). Note `chat_id` is a typed
  `int` from `Chat.id`, so traversal via chat_id is structurally
  impossible — the guard protects against a poisoned/symlinked directory,
  not against string injection.
- **Deletion is derived from the typed `chat_id` + `media_root` only.**
  `session.temp_media_path` (a free `str`) is never used as a deletion
  target — schemas over heuristics (TECH.md).
- Any other `Filesystem` failure raises `OSError` upward (fail loud);
  `_handle_reset` catches it, logs loudly, and completes the reset anyway.

### Reset handler & interception point

- `_handle_reset(client, session, media_root, command) -> None` — mirrors
  `_handle_portrait`'s contract: **never raises**. Flow: log
  `reset_command` → purge (catch `(OSError, ValueError)` →
  `logger.exception`) → `session.clear()` → `log_phase_transition` when
  the phase actually changed → log `session_reset` → `_send(RESET_REPLY)`.
- Interception is the **first branch of `_dispatch_update` after the
  `update.message is None` guard and the session fetch**
  (`bot_gateway.py:1144`), i.e. **before** the `INTERVIEW_PHASES` branch,
  before `portrait_source`, and before `build_reply`. A reset command can
  therefore never become an interview answer or a portrait nudge.

### In-flight work (chosen approach — stated honestly)

**Approach: sequential dispatch, no locks, no generation counter.**
`run_bot` awaits `_dispatch_update` for every update and only calls
`getUpdates` again once the batch has drained (`bot_gateway.py:1259-1272`),
so no two updates are ever handled concurrently in this single-process,
in-memory bot. Correctness follows directly: when `_handle_reset`
returns, no later-running code for that chat holds a session reference, so
the *next* update is guaranteed to see the clean session.

**Limits (accepted, not bugs):**

1. A reset typed while a slow stage is in flight **waits its turn**: the
   in-flight stage finishes (its portrait/narration messages may land
   *before* the reset reply) and only then is the reset processed. The
   state and media are clean afterwards regardless.
2. Reset **cannot cancel** an in-flight stage — cancellation would require
   structured task tracking the codebase does not have, and no
   requirement needs it (the reset still lands cleanly behind it).
3. No distributed/async locking scheme is invented here (explicitly out of
   scope for an in-memory single-process bot).

### Failure & logging

- Structured INFO event lines (existing house style, e.g.
  `voice_note_delivered`):
  - `reset_command chat_id=%s command=%s from_phase=%s` — at recognition,
    before the purge.
  - `session_reset chat_id=%s media_files=%s` — after clear; on purge
    failure the count is `media_files=unavailable`.
  - `phase_transition chat_id=%s <old> -> AWAITING_PHOTO` via the
    existing `log_phase_transition` helper, only when the phase changed.
- Purge failure: `logger.exception("temp media purge failed chat_id=%s")`
  (loud; the OSError/ValueError message may name the chat's own path —
  never a secret). Out-of-range entity: one `logger.warning` with numeric
  lengths only.
- **Never logged:** secrets, message text, photo/voice bytes, file
  contents (TECH.md). No new decorator is needed — reset makes no network
  calls; cross-cutting async-call logging already lives on the API
  clients.
- On the conversation path every failure degrades in-character: state is
  cleared and `RESET_REPLY` is sent even when the purge or the send fails
  (TECH.md error policy; no bare `except`, nothing swallowed unlogged).

### Scope cuts (YAGNI)

- No command registry/extensible command framework — two hard-wired
  aliases, one handler.
- No argument or deep-link parsing (`/start payload` arguments are
  ignored); no bot-username configuration (`@suffix` accepted as-is —
  Telegram only routes an addressed command to that bot anyway).
- No commands in captions (`caption_entities` ignored); photo captions
  remain portraits.
- No in-flight cancellation, no locks, no generation counters.
- No media TTL/GC sweeper, no cross-chat cleanup, no deletion outside the
  chat's own directory.
- No persistence, no database, no multi-user concurrency architecture
  (MISSION).
- No API-timeout fallbacks or general wrong-payload-at-wrong-stage guards
  — **separate Phase 7 slices** (ROADMAP).
- Deployment/hosting — Phase 8.
- No backward-compatibility shims (fresh feature; no legacy behaviour to
  preserve).

## Success criteria (maps to ROADMAP Phase 7 acceptance)

- **"Reset mid-every-phase starts clean"** (Phase 7's first acceptance
  clause, this slice): `/start` and `/restart` purge state + that chat's
  temp media from every `Phase`, including a brand-new chat and repeated
  resets, in-process — **no server restart, ever** — and a second full run
  works immediately afterwards.
- The reply is always the single in-character `RESET_REPLY` (confirmation
  + greeting); a reset command is never mistaken for an answer or a
  portrait nudge.
- Deletion is scoped to the chat's own directory; missing directories and
  deleted files are idempotent no-ops; failures degrade loudly without
  breaking the conversation.
- Docs match reality: README/ROADMAP describe what is actually built (the
  current overclaim is removed by this slice's implementation), the banner
  reads `(Phase 7 - Resilience)`.
- `./scripts/test` and `./scripts/hooks` fully green; secrets never logged.

The remaining Phase 7 acceptance clauses (timeout fallbacks, no phase
crashes on unexpected input) belong to the other Phase 7 slices and are
**out of scope here**.

## Documentation deltas (contract for what implementation must change)

Described here; performed by the implementer per `plan.md`:

1. **README.md** — the line-38 claim becomes true; reword it to match the
   shipped behaviour (one in-process reset command, purge scoped to the
   chat, confirmation+greeting reply). Mention the reset in the
   "Run the bot" stage list; keep the status block honest (Phase 7 slice,
   not the whole phase).
2. **SPECS/ROADMAP.md** — Phase 7 gains an `Implementation:` note for this
   slice (constants, `parse_reset_command`, `purge_chat_media`,
   `_handle_reset`, interception point, banner); the status row reflects
   *only* this slice (reset done; timeout & wrong-payload slices pending).
3. **Gateway banner** — `run_bot` logs
   `Starting Telegram gateway (Phase 7 - Resilience)` (user decision).
4. **Stale comments** — refresh the aspirational references at
   `bot_gateway.py:551` and `bot_gateway.py:1001` to describe the
   now-real behaviour.
