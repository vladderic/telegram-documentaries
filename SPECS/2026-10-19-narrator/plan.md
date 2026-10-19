# Plan — The Narrator (Phase 6)

Red/Green TDD throughout: write the failing test first, then the minimum code
that makes it pass. Run checks with the dev scripts (`scripts/test`,
`scripts/hooks` — ground truth, see README / `SPECS/TECH.md`).

Keep the repo's ground-truth gates green at every step: run `./scripts/test`
after each task group and `./scripts/hooks` before committing.

## Task group 1 — Dependency & PCM encode (no network)

1. Add `lameenc` to `[project] dependencies` in `pyproject.toml` (pinned), and
   install with `pip install -e ".[dev]"`. Confirm the manylinux wheel imports
   on this box.
2. Red tests for `encode_mp3(pcm, sample_rate, channels) -> bytes`: a synthetic
   PCM buffer encodes to non-empty bytes beginning with an MP3 frame sync
   (`0xFF 0xFB`/`0xFF 0xF3`/`0xFF 0xF2`) or an ID3 tag; empty PCM raises
   `ValueError`; a sane input produces roughly the expected duration
   (bytes-per-second check, generous tolerance) so a broken encoder is caught.
3. Implement `encode_mp3` in `narrator.py` (the pure encode step, no HTTP).

## Task group 2 — Audio contracts

4. Red tests for `VoiceNote`: non-empty `data` required (empty/`b""` rejected
   with `ValidationError`), `mime_type` pinned to `audio/mpeg`, direct
   schema-shaped construction works.
5. Red tests for the PCM contract: a valid
   `audio/l16; rate=24000; channels=1` `mimeType` parses to
   `(24000, 1)`; a wrong encoding (`audio/wav`), a non-mono channel count, an
   unexpected sample rate and a missing/garbled `mimeType` each raise
   `GeminiApiError` (fail loud — never guessed).
6. Implement `VoiceNote` and the PCM/sample-format parsing.

## Task group 3 — TTS client

7. Red tests against `httpx.MockTransport` for `GeminiClient.generate_audio`:
   the request body carries `contents[0].parts[0].text` (the script verbatim),
   `generationConfig.responseModalities == ["AUDIO"]` and
   `speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName`; the returned part's
   base64 is decoded. HTTP 500 / non-JSON / no inline audio part raise
   `GeminiApiError`; the API key never appears in an error message.
8. Red tests for `NarratorClient.speak(script)`: builds the verbatim script into
   the prompt (**asserts no style prefix or wrapper is injected** — the live
   finding that prefixes are spoken), calls `generate_audio`, and returns a
   typed non-empty `VoiceNote` with `mime_type == "audio/mpeg"`; a malformed
   inline audio part raises `GeminiApiError`; `close()` delegates to the shared
   client.
9. Implement `NARRATOR_MODEL`, `DEFAULT_VOICE`, `NarratorClient.speak`,
   `close`, and `generate_audio` in `gemini.py`.

## Task group 4 — Gateway wiring (the state machine tail)

10. Red tests for `TelegramClient.send_voice`: multipart upload via `_request`
    with `files={"voice": (filename, data, mime_type)}` and `chat_id`, the
    `duration` field passed through, and the call **never retried**.
11. Red tests for the finish path (shared harness with the existing
    `FakeScripter`/`FakeNarrator`):
    - happy path: reveal → portrait → narration text → **voice note**, in that
      order; session ends in `Phase.DONE` with `voice_note_sent is True`.
    - the voice note is synthesised from the **text that was sent**
      (`session.script_text`), not from a second model call.
    - TTS failure → in-character line, phase `DONE`, the text narration stays,
      no voice note, no crash, no retry.
    - encode failure → same degrade path.
    - `sendVoice` failure → same degrade path.
    - exactly one voice note per session; after `DONE` any message gets
      `LATER_PHASE_REPLY` and no second synthesis.
    - a Phase 5 scripter failure (no `script_text`) never triggers a voice note.
12. Add `NARRATOR_FAILURE_MSG`, a `_narrate(...)` helper mirroring `_write_script`
    (claim `Phase.NARRATE` *before* the TTS call, then `Phase.DONE`), thread a
    `narrator` through `_finish_interview` / `_handle_answer` / `_handle_interview`
    / `_dispatch_update` / `run_bot` (construct + `close()`), update the banner
    to `(Phase 6 - Narrator)`, and update the existing converter/interview/scripter
    finish-path harnesses for the new tail.

## Task group 5 — State, docs & hygiene

13. Add `voice_note_sent` to `SessionState` (schema v1.3), reset it in `clear()`,
    and test both (`TestStoredScript`-style tests in
    `tests/unit/test_session_state.py`).
14. Update README (status → Phase 6, pipeline step, layout, "run it"), and
    `SPECS/ROADMAP.md`; check whether `SPECS/TECH.md` needs the encoder note.
15. Confirm `.env` and `.tmp/` remain untracked.

## Task group 6 — Full gate & live validation

16. `scripts/test` green (all new + existing, no regressions).
17. `scripts/hooks` green: ruff, ruff-format, mypy, gitleaks.
18. Live: a real Telegram run — finish an interview and confirm the voice note
    plays and matches the text. Record `narrator_speak` /
    `voice_delivered` lines (per `validation.md`).

## Out of scope

Voice selection, streaming, retries, audio persistence/caching, multi-speaker,
captions/transcription, re-synthesis commands, Phase 7 reset behaviour.
