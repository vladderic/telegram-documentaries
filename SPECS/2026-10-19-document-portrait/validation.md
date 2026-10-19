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

- [x] **The file path works** (2026-10-19 14:26): the same image reached the bot
      as a document, not a photo —
      `portrait_received kind=document has_photo=False has_document=True
      mime=image/jpeg declared_bytes=592016`. The photo route for the same
      picture had declared only 245840 bytes, so the uploads really do differ.
      (It was then correctly rejected: `NOT_A_REAL_PHOTO`, "a cartoon drawing of
      a smiling sun" — the vision gate judged the file's bytes.)
- [x] The upload kind is now observable in the log at all: this was the gap that
      made the first attempt ambiguous (a JPEG file and a compressed photo both
      stored as `portrait.jpg`), fixed in `e9bc08d`.
- [~] An **accepted** file portrait completing the interview → **deferred by the
      user (2026-10-19)**: the accept→interview→fusion chain for a document is
      covered by the automated flow test (hold line → one vision call →
      verdict → stored → five answers → `send_photo`) and was proven live up to
      the verdict; only the happy-path half with a real file was not repeated
      live. The gate rejected the file used for the live check, which is itself
      correct behaviour (`NOT_A_REAL_PHOTO`, a cartoon).
- [~] A PDF gets the photo nudge; an oversize image file gets the too-large line
      with no `getFile` in the log → **automated only** (both are asserted with
      mocked transport; no live PDF was sent).
- [x] A normal photo behaves exactly as before: live runs at 12:19, 13:38 and
      14:21 all show the compressed-photo route unchanged (verdict, interview,
      conversion, holding reply).
- [~] Quality note: **not measurable yet** — no live run has used a
      full-resolution *portrait* file. What is recorded: the same picture
      arrived as 245840 bytes (photo) and 592016 bytes (file), so a file upload
      genuinely carries more of the original; `converter_fuse portrait_bytes`
      is the metric to compare (150646 for the 12:19 compressed run). The model's
      quality ceiling is unchanged; Phase 4's prompt is variant B.
- [x] Docs updated: README (upload section, 10 MB cap), ROADMAP (Phase 4b
      entry), this file. `TECH.md` needs no edit: its session-state notes are
      phase-level, not field-level.
