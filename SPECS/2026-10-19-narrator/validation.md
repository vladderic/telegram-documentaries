# Validation — The Narrator (Phase 6)

Every box is evidence, not intent: the command or log line that proves it goes
in the report. Automated boxes are ticked by `./scripts/test` /
`./scripts/hooks`; live boxes need a real Telegram run.

Evidence base: `./scripts/hooks` green (ruff, ruff-format, mypy, pytest,
gitleaks) with the new tests passing. Commits recorded as task groups land.

## Automated

- [ ] `encode_mp3` returns non-empty MP3 bytes (frame sync / ID3 header) for a
      synthetic PCM buffer, and raises `ValueError` on empty input; the encoded
      duration is in the right ballpark for the input length.
      → `tests/unit/test_narrator_encode.py`.
- [ ] `VoiceNote` rejects empty/blank bytes (`ValidationError`) and pins
      `mime_type` to `audio/mpeg`.
      → `tests/unit/test_audio_contracts.py`.
- [ ] The PCM `mimeType` (`audio/l16; rate=24000; channels=1`) parses to
      `(24000, 1)`; a wrong encoding, non-mono channels, an unexpected rate and a
      garbled `mimeType` each raise `GeminiApiError` (fail loud, never guessed).
      → `tests/unit/test_audio_contracts.py`.
- [ ] `GeminiClient.generate_audio` posts the TTS request with the script
      verbatim, `responseModalities: ["AUDIO"]` and the configured voice; returns
      the decoded inline audio; HTTP 500 / non-JSON / missing inline audio raise
      `GeminiApiError`; the key is redacted.
      → `TestGenerateAudio` in `tests/unit/test_gemini_client.py`.
- [ ] `NarratorClient.speak` sends the script **with no style prefix or wrapper**
      (the live finding that a prefix is spoken), returns a typed non-empty
      `VoiceNote` with `mime_type == "audio/mpeg"`, and propagates
      `GeminiApiError` on a malformed inline part; `close()` works.
      → `tests/unit/test_narrator_client.py`.
- [ ] `TelegramClient.send_voice` uploads via multipart (`files={"voice": …}`),
      passes `duration`, and is **never retried**.
      → `tests/unit/test_telegram_client.py` (or the existing client test file).
- [ ] Happy-path gateway flow: reveal → portrait → narration text → voice note,
      **in that order**; session ends in `Phase.DONE` with `voice_note_sent`.
      → `tests/unit/test_narrator_gateway.py` (shared call log
      `["text", "text", "photo", "script", "text", "voice"]`).
- [ ] The voice note is synthesised from `session.script_text` (the words the
      user read), not a second model call.
      → `tests/unit/test_narrator_gateway.py`.
- [ ] TTS failure / encode failure / `sendVoice` failure each degrade
      in-character, leave the text narration standing, end in `DONE`, and never
      retry or crash. → `tests/unit/test_narrator_gateway.py`.
- [ ] Exactly one voice note per session; after `DONE` a message gets
      `LATER_PHASE_REPLY` and no second synthesis; a Phase 5 scripter failure
      (no `script_text`) never triggers a voice note.
      → `TestOneVoiceNotePerSession` in `tests/unit/test_narrator_gateway.py`.
- [ ] `SessionState.voice_note_sent` defaults `False` and `clear()` resets it.
      → `TestStoredScript` (or sibling) in `tests/unit/test_session_state.py`.
- [ ] `./scripts/test` and `./scripts/hooks` green.
      → record the test count; all five hooks pass.

## Live (real Telegram traffic)

- [ ] Live end-to-end: send a photo → finish the interview → portrait → narration
      text → **a playable voice note** arrives last.
- [ ] The voice note **plays** and the spoken audio matches the narration text
      (no injected style words read aloud; no truncation).
- [ ] `sendVoice` succeeds (`voice_delivered` in the log with `bytes`/
      `duration`); the audio is MP3 and Telegram renders it as a voice note, not
      a file.
- [ ] The gateway banner reads `(Phase 6 - Narrator)`.
- [ ] One voice note per session live: after it arrives, any further message gets
      `LATER_PHASE_REPLY` and no second `voice_delivered` line.
- [ ] Forced-failure check (optional, via a scratch call): a bad key / dead host
      on the TTS step yields the in-character line, keeps the text narration, and
      leaves no crash.

## Docs

- [ ] README updated: status block (Phase 6), pipeline step, "run it",
      repository layout (`narrator.py`), dependency note (`lameenc`).
- [ ] `SPECS/ROADMAP.md` Phase 6 has an `Implementation:` note and the status row
      updated.
- [ ] `SPECS/TECH.md` updated if the encoder or voice is worth pinning (the
      model is already named there).
- [ ] Differences between this spec and what was built are recorded (below).

## Differences between spec and implementation

_(filled in during validation)_

1. **The script is sent verbatim** — no persona, no style prefix, no
   `systemInstruction` (all rejected or spoken; see requirements finding 5). The
   `SCRIPTER_PERSONA` rules stay in Phase 5; the Narrator adds nothing to the
   words. This is a spec decision, not drift, but it is recorded because a reader
   may expect a narrator persona prompt.
2. **MP3, not OGG/Opus** — dictated by the model returning PCM and the user's
   decision to use `lameenc`. TECH.md's "(OGG/MP3)" already allows this.
3. **The Phase 5 narration text is kept** (user decision) — the voice note
   follows it rather than replacing it.
