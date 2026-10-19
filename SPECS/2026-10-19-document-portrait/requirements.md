# Requirements — Full-resolution portraits (Phase 4b)

## Goal

Let the user send their portrait **as a file (document)** so the Converter works
from Telegram's original, uncompressed bytes instead of the compressed
`PhotoSize` rendition. This is the single biggest quality lever found in the
Phase 4 live run.

## Context

- Phase 4 live evidence: `converter_fuse portrait_bytes=150646` — the stored
  portrait came from Telegram's largest `PhotoSize`, i.e. the *compressed*
  version (`~150 KB` for a full-frame photo). The prompt variants all hit that
  same ceiling.
- `Message` (`models.py:80`) parses `text`, `caption` and `photo` and ignores
  everything else (`extra="ignore"`), so a document never reaches the router;
  the user's original bytes are simply dropped.
- Worse, the `AWAITING_PHOTO` nudge actively tells users to send a *photo*
  — pointing them at the lossy path.
- The Bouncer already accepts bytes: `check_photo(image_bytes, mime_type)`, but
  the gateway never passes a media type, so the vision call always declares
  `image/jpeg` even for a PNG/HEIC.
- `_store_photo` hard-codes `portrait.jpg` and `_deliver_portrait` hard-codes
  `PORTRAIT_MIME_TYPE = "image/jpeg"`, so the Converter is told every portrait
  is a JPEG.

## Roadmap placement

Proposed: **Phase 4b — Full-resolution portrait input** (follow-up to Phase 4;
no new pipeline stage, no new dependency). ROADMAP.md itself is only edited at
verification, with your approval.

## Behaviour

| Event | Behaviour |
|---|---|
| Image document (`mime_type` starts `image/`) in `AWAITING_PHOTO` | Identical to a photo: hold line → download → Bouncer verdict → original bytes stored → interview opens |
| Document with a non-image mime (e.g. `application/pdf`) | Unchanged: the Bouncer's photo nudge |
| Document with no `mime_type` | Not a portrait (no filename heuristics — schemas over regexes) → photo nudge |
| Photo or document over 10 MB | Existing `PHOTO_TOO_LARGE_MSG`, before download when `file_size` is declared |
| Photo/document during `INTERVIEW` | Existing interview nudge: not an answer, the pending question stands |
| Document caption | Ignored — never an interview answer, never a caption on the portrait |
| Stored portrait | Original bytes at `media_root/<chat_id>/portrait.<ext>`, with `session.photo_mime_type` recorded |
| Converter call | Uses the recorded media type, never a constant |

## Decisions (user, 2026-10-19)

1. Photos keep working; image documents are accepted; non-image documents keep
   the nudge.
2. One 10 MB cap for both (`MAX_PHOTO_BYTES` in `bouncer.py`).
3. Store document bytes as-is and record the real mime type on the session; no
   re-encoding and **no new dependency** (no Pillow).
4. Captions are never answers; mid-interview documents behave exactly like
   mid-interview photos (nudge).

## Contracts

- `Document(BaseModel)` — same policy as `PhotoSize` (`extra="ignore"`):
  `file_id: str`, `file_unique_id: str`, `file_name: str | None = None`,
  `mime_type: str | None = None`, `file_size: int | None = None`.
- `Message.document: Document | None = None`.
- `PortraitSource(BaseModel)`: `file_id: str`, `mime_type: str`,
  `file_size: int | None = None` — the typed answer to "is this message a
  portrait, and what is it?".
- `portrait_source(message: Message) -> PortraitSource | None` — lives in
  `bouncer.py` next to `select_photo_size` (boundary logic), because the
  dispatcher, the interview guard and `build_reply` must all share one
  predicate: routing cannot disagree with the stage (the Phase 3/4 bug class).
  A photo takes precedence when both are present (Telegram never sends both).
- `SessionState.photo_mime_type: str = "image/jpeg"` — set whenever
  `photo_local_path` is set, consumed by `_deliver_portrait`.
- Stored filename: `portrait.jpg` for `image/jpeg`, otherwise
  `portrait.<subtype>` (`png`, `webp`, `heic`, …). One general rule, no mapping
  table to drift.

## Failure behaviour

- Loud in logs, in character for the user: download/`OSError` → existing
  `DISPATCH_FAILURE_MSG`; over cap → `PHOTO_TOO_LARGE_MSG`; Bouncer rejection
  unchanged; a malformed document payload is ignored by the schema, not
  crashed on.
- `_deliver_portrait` never falls back to a magic string: it uses
  `session.photo_mime_type`, whose default is itself part of the contract.

## Out of scope

- Transcoding HEIC → JPEG, resizing or re-compressing portraits.
- Accepting non-image documents (PDFs, archives).
- Raising the 10 MB cap or streaming large uploads.
- Captions as interview answers; multiple portraits per session; purging old
  portraits (that is Phase 7).
- Handling Telegram's 10 MB `sendPhoto` limit for *outgoing* generated images.
