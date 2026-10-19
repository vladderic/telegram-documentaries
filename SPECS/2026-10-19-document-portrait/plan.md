# Plan — Full-resolution portraits (Phase 4b)

Red/Green TDD throughout: every task group starts with failing tests and runs
the dev scripts described in the README (`scripts/test`, `scripts/hooks`).

Dev-box note: the sandbox sits at ~95–97% of its 1 GB cgroup limit, so commands
are kept small (targeted `pytest` during the loop; one full `scripts/hooks` run
per task group, not per edit). Environment policy, not product behaviour.

## Task group 1 — Boundary contract: a document can be a portrait

1. Red: contract tests for `Document` (parses Telegram's document payload; extra
   fields ignored) and `portrait_source` — image document → source with the
   document's `file_id`, mime and size; photo → source; photo **and** document →
   photo wins; non-image mime → `None`; missing mime → `None`; text-only and
   caption-only messages → `None`.
2. Green: add `Document` + `PortraitSource` to `models.py`, `Message.document`,
   and `portrait_source` to `bouncer.py` beside `select_photo_size`.

## Task group 2 — Store the bytes as-is and remember what they are

1. Red: `_store_photo(media_root, chat_id, bytes, mime_type)` writes
   `portrait.jpg` / `portrait.png` / `portrait.heic` for the matching mime, and
   the bytes on disk are byte-identical to the input; `_handle_photo` records
   `session.photo_mime_type`; `_deliver_portrait` sends the **recorded** type
   (a PNG portrait must produce `mimeType == "image/png"` in the Gemini
   `inlineData` part, read from the stored path).
2. Green: new `_store_photo` signature, the new session field, and
   `_deliver_portrait` reading `session.photo_mime_type`.
3. Delete `PORTRAIT_MIME_TYPE` after `grep` confirms no other user (no legacy
   constant kept for compatibility).

## Task group 3 — Route documents through the same door

1. Red: dispatcher tests — an image document in `AWAITING_PHOTO` runs the whole
   happy path (hold line → `getFile` → download → one Bouncer call → stored →
   interview opens); a PDF in `AWAITING_PHOTO` gets the photo nudge; a 12 MB
   document gets `PHOTO_TOO_LARGE_MSG` **without** a `getFile` call; a document
   in `INTERVIEW` gets the interview nudge and does not become an answer; a
   document after the portrait gets the holding reply.
2. Green: `_dispatch_update`, the interview guard and `_handle_photo` all ask
   `portrait_source(message)` instead of `message.photo`; `build_reply` uses the
   same predicate for its photo branch.
3. Sweep — category **"route predicate disagrees with the stage"**: every
   `message.photo` check in `bot_gateway.py` is re-expressed through
   `portrait_source`, and a parametrised test runs the identical body for a
   photo and an image document so the two paths cannot drift.

## Task group 4 — Tell the Bouncer the truth about the format

1. Red: `check_photo(bytes, mime_type="image/png")` puts `image/png` in the
   vision call's `inline_data`; the gateway passes the source's mime type
   instead of relying on the default.
2. Green: thread the mime through from `PortraitSource` to `check_photo`.

## Task group 5 — Docs, gates, PR

1. README: how to send a portrait (photo *or* file), the 10 MB cap, and the
   quality note.
2. ROADMAP: Phase 4b row (pending user approval); `validation.md` ticked with
   real evidence.
3. `./scripts/test` and `./scripts/hooks` green; commit on
   `feature/2026-10-19-document-portrait`, push, open the PR against
   `feature/2026-10-19-converter` (auto-retargets to `main` as #2/#3 merge).
