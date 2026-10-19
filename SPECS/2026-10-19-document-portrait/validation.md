# Validation — Full-resolution portraits (Phase 4b)

Every box must be checked before merge. Automated checks run via the dev
scripts; live checks need real Telegram traffic against the running bot.

## Automated (`scripts/hooks` must be green)

- [x] Document contract: `Document` parses Telegram's document payload and
      ignores unknown fields; `Message.document` is populated
      (`test_parses_document_message_update`,
      `test_document_optional_fields_default_to_none`,
      `test_photo_message_has_no_document`).
- [x] `portrait_source` predicates: image doc → source; non-image doc → `None`;
      missing mime → `None` (never guessed from the file name); photo → source;
      photo+document → photo wins; text/caption only → `None` (6 tests,
      `TestPortraitSource`).
- [x] Storage: the file follows the media type (`portrait.jpg` / `.png` /
      `.webp` / `.heic`) and the bytes on disk are byte-identical to the upload
      (`TestStorePortrait`, 3 tests) — the flow test also reads back
      `portrait.png` and the exact `PORTRAIT_BYTES` the download returned.
- [x] `SessionState.photo_mime_type` defaults to `image/jpeg`, records a file
      portrait's type and resets with `clear()`
      (`TestStoredPortraitMediaType`, 3 tests); `_deliver_portrait` hands the
      **recorded** type to the Converter — the file-portrait flow asserts
      `converter.calls[0].mime_type == "image/png"`.
- [x] Routing equivalence: one flow test drives a `document` update through the
      whole pipeline (hold line → one vision call → verdict → stored → five
      answers → generated portrait uploaded).
- [x] Guards: PDF → `PHOTO_NUDGE` with **no** vision call; 10 MB+1 file →
      `PHOTO_TOO_LARGE_MSG` with no vision call (so no transfer); a document
      mid-interview → `INTERVIEW_NUDGE` + the pending question, and it does not
      consume an answer; a document after the portrait → `LATER_PHASE_REPLY`
      with no second vision call.
- [x] The Bouncer is told the truth: the flow test asserts the vision call was
      `check_photo(PORTRAIT_BYTES, "image/png")`.
- [x] No hard-coded JPEG assumptions left: `PORTRAIT_MIME_TYPE` deleted,
      `_store_photo` takes the media type, `fetch_photo` replaced by
      `fetch_portrait(PortraitSource)`, and the interview guard now asks
      `portrait_source` instead of `message.photo` (sweep).
- [x] `./scripts/test`: **252 passed** (233 at Phase 4's last run). Red evidence
      per task group: TG1 — 9 failures (3 model + 6 predicate) before the
      contracts existed; TG2+TG3 — the file-portrait flow and the oversize guard
      failed while the nudge/holding cases already passed because documents were
      being treated as plain text; the sweep — the interview-guard test covers
      it, and the rename turned `TestFetchPhoto` red (6 errors) until it was
      rewritten as `TestFetchPortrait`.
- [x] `./scripts/hooks` green: ruff, ruff-format, mypy, pytest, secret scan.

## Live (real Telegram traffic)

- [ ] Portrait sent **as a file** → hold line → verdict → interview opens; the
      log shows the document path and the stored file is `portrait.<ext>`.
- [ ] Interview finished → portrait generated; the log shows `converter_fuse`
      with the document's media type and a **larger** stored source than the
      compressed-photo run (`portrait_bytes` comparison recorded).
- [ ] A PDF gets the photo nudge; an oversize image file gets the too-large line
      with no `getFile` in the log.
- [ ] A normal photo behaves exactly as before (regression check).
- [ ] Quality note recorded honestly: how much did the full-resolution source
      actually help the fusion? (The model's ceiling remains; the Phase 4 prompt
      is variant B, "same rules + realism".)
- [ ] Docs updated: README (upload section, 10 MB cap), ROADMAP (Phase 4b
      entry), this file. `TECH.md` needs no edit: its session-state notes are
      phase-level, not field-level.
