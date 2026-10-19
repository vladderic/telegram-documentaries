# Validation — The Narrator (Phase 6)

Every box is evidence, not intent: the command or log line that proves it goes
in the report. Automated boxes are ticked by `./scripts/test` /
`./scripts/hooks`; live boxes need a real Telegram run.

Evidence base (re-run at validation time): `./scripts/test` → **361 passed**
(12.83s, then 13.73s on the final re-run); `./scripts/hooks` → ruff, ruff-format, mypy, pytest, gitleaks —
**all five checks pass**. Plan hygiene: task groups 1–5 fully delivered;
task group 6 delivered except task 18 (the live Telegram run — see Live
section). `.env` and `.tmp/` confirmed untracked via `git check-ignore`.

## Automated

- [x] `encode_mp3` returns non-empty MP3 bytes (frame sync / ID3 header) for a
      synthetic PCM buffer, and raises `ValueError` on empty input; the encoded
      duration is in the right ballpark for the input length.
      → `tests/unit/test_narrator_encode.py::TestEncodeMp3`
      (frame-sync prefix, 1s-vs-2s size ratio, empty and odd-length PCM
      both raise `ValueError`).
- [x] `VoiceNote` rejects empty/blank bytes (`ValidationError`) and pins
      `mime_type` to `audio/mpeg`.
      → `tests/unit/test_audio_contracts.py::TestVoiceNote`
      (empty bytes rejected; `audio/ogg` rejected; duration ≥ 1 enforced).
- [x] The PCM `mimeType` (`audio/l16; rate=24000; channels=1`) parses to
      `(24000, 1)`; a wrong encoding, non-mono channels, an unexpected rate and a
      garbled `mimeType` each raise `GeminiApiError` (fail loud, never guessed).
      → `tests/unit/test_audio_contracts.py::TestParsePcmFormat`
      (parse + 10 parametrised failure cases, all `GeminiApiError`).
- [x] `GeminiClient.generate_audio` posts the TTS request with the script
      verbatim, `responseModalities: ["AUDIO"]` and the configured voice; returns
      the decoded inline audio; HTTP 500 / missing inline audio / malformed or
      empty inline parts raise `GeminiApiError`; the key is redacted.
      → `tests/unit/test_gemini_client.py::TestGenerateAudio`
      (verbatim body + modality + voice asserted; HTTP 500 with key redaction;
      missing inline audio; text-only response; leading-text-part scan;
      snake_case tolerance; missing fields; empty base64 payload).
      Note: the *non-JSON body* branch lives in the shared `_call` transport
      (`gemini.py`) and has no dedicated test for `generate_audio`; the typed
      shape failures above are the directly tested paths.
- [x] `NarratorClient.speak` sends the script **with no style prefix or wrapper**
      (the live finding that a prefix is spoken), returns a typed non-empty
      `VoiceNote` with `mime_type == "audio/mpeg"`, and propagates
      `GeminiApiError` on a malformed inline part; `close()` works.
      → `tests/unit/test_narrator_client.py::TestSpeak`
      (verbatim body + no `systemInstruction` asserted; typed MP3 note;
      unsupported format, truncated payload and raw encoder `RuntimeError`
      all surface as `GeminiApiError`; duration rounds up; `close()` used
      throughout; HTTP 500 never leaks the key).
- [x] `TelegramClient.send_voice` uploads via multipart (`files={"voice": …}`),
      passes `duration`, and is **never retried**.
      → `tests/unit/test_telegram_client.py::TestSendVoice`
      (multipart body contains `chat_id`, `duration`, `narration.mp3`,
      `audio/mpeg` and the bytes; a 500 records exactly one attempt).
- [x] Happy-path gateway flow: reveal → portrait → narration text → voice note,
      **in that order**; session ends in `Phase.DONE` with `voice_note_sent`.
      → `tests/unit/test_scripter_gateway.py` (the gateway harness lives in
      this shared file — there is no `test_narrator_gateway.py`):
      `TestTheVoiceNoteClosesTheDocumentary::test_the_voice_note_is_the_very_last_thing`
      asserts the shared call log ends `["text", "photo", "script", "text",
      "voice"]` and the uploaded note is `audio/mpeg` with a duration;
      `TestNarrate::test_speaks_the_delivered_text_and_sends_one_voice_note`
      asserts `Phase.DONE` and `voice_note_sent is True`.
- [x] The voice note is synthesised from `session.script_text` (the words the
      user read), not a second model call.
      → `tests/unit/test_scripter_gateway.py::TestNarrate::test_speaks_the_delivered_text_and_sends_one_voice_note`
      (the narrator's call list is exactly `[NARRATION]` — the delivered text).
- [x] TTS failure / encode failure / `sendVoice` failure each degrade
      in-character, leave the text narration standing, end in `DONE`, and never
      retry or crash.
      → `tests/unit/test_scripter_gateway.py`:
      `TestNarrate::test_a_failed_tts_degrades_keeps_the_words_and_ends_done`,
      `TestNarrate::test_a_raw_encoder_failure_still_degrades_in_character`
      (`ValueError` and `RuntimeError` parametrised; `send_voice` never
      awaited), `TestNarrate::test_a_failed_upload_never_sends_a_second_note`,
      and end-to-end:
      `TestTheVoiceNoteClosesTheDocumentary::test_a_failed_voice_does_not_stop_the_words`
      plus
      `::test_a_raw_encoder_failure_never_crashes_the_loop`
      (narration text still sent, `NARRATOR_FAILURE_MSG` sent, no retry).
- [x] Exactly one voice note per session; after `DONE` a message gets
      `LATER_PHASE_REPLY` and no second synthesis; a Phase 5 scripter failure
      (no `script_text`) never triggers a voice note.
      → `tests/unit/test_scripter_gateway.py`:
      `TestOneVoiceNotePerSession::test_a_message_after_done_gets_the_holding_reply_and_no_second_note`
      (one `send_voice`, one narrator call, `LATER_PHASE_REPLY`), and
      `TestNarrate::test_a_session_with_no_script_is_closed_without_speaking`
      (no `script_text` → no synthesis, no upload, session closed `DONE`).
- [x] `SessionState.voice_note_sent` defaults `False` and `clear()` resets it.
      → `tests/unit/test_session_state.py::TestVoiceNoteSent`
      (defaults `False`; `clear()` resets; sessions do not share the flag).
- [x] `./scripts/test` and `./scripts/hooks` green.
      → `./scripts/test`: **361 passed** (12.83s, then 13.73s on the final re-run).
      `./scripts/hooks`: ruff, ruff-format, mypy, pytest, gitleaks — all five
      pass.

## Live (real Telegram traffic)

**None of these boxes was performed.** No live bot traffic can be generated
from this environment, and no live evidence exists on this branch; each box
stays unticked pending a real Telegram run (plan task 18). ROADMAP's status
row — "Implementation complete — live validation pending" — is the
authoritative wording; nothing here or in the README claims otherwise.

- [ ] Live end-to-end: send a photo → finish the interview → portrait → narration
      text → **a playable voice note** arrives last. — *not performed (no live
      bot traffic available).*
- [ ] The voice note **plays** and the spoken audio matches the narration text
      (no injected style words read aloud; no truncation). — *not performed
      (no live bot traffic available).*
- [ ] `sendVoice` succeeds (`voice_delivered` in the log with `bytes`/
      `duration`); the audio is MP3 and Telegram renders it as a voice note, not
      a file. — *not performed (no live bot traffic available); unit evidence
      only (`TestSendVoice`, `TestNarrate`).*
- [ ] The gateway banner reads `(Phase 6 - Narrator)`. — *not verified live;
      the code log line exists (`bot_gateway.py`: "Starting Telegram gateway
      (Phase 6 - Narrator)") but no live log was captured.*
- [ ] One voice note per session live: after it arrives, any further message gets
      `LATER_PHASE_REPLY` and no second `voice_delivered` line. — *not
      performed (no live bot traffic available); unit evidence only
      (`TestOneVoiceNotePerSession`).*
- [ ] Forced-failure check (optional, via a scratch call): a bad key / dead host
      on the TTS step yields the in-character line, keeps the text narration, and
      leaves no crash. — *not performed (no live bot traffic available); unit
      evidence only (`TestNarrate` failure tests).*

## Docs

- [x] README updated: status block (Phase 6), pipeline step, "run it",
      repository layout (`narrator.py`), dependency note (`lameenc`).
      → verified in `README.md`: status block ("Status: Phase 6 — Narrator",
      including the voice-note sentence); Pipeline step 7 (Narrator); "Run the
      bot" stage 5 (verbatim TTS → MP3 → `sendVoice`, degrade paths, one note
      per session); layout entry `narrator.py`; the `lameenc` note under Local
      setup.
- [x] `SPECS/ROADMAP.md` Phase 6 has an `Implementation:` note and the status
      row updated.
      → Phase 6 `Implementation:` note covers `narrator.py` constants,
      `encode_mp3`/`lameenc`, `VoiceNote` + PCM parser, `generate_audio`,
      verbatim `speak`, typed encoder failure, `send_voice`, `_narrate` and
      `voice_note_sent` (v1.3); status row reads "Implementation complete —
      live validation pending" (consistent with this file; README does not
      claim a live run).
- [x] `SPECS/TECH.md` updated if the encoder or voice is worth pinning (the
      model is already named there).
      → the Narrator bullet pins: model `gemini-3.1-flash-tts-preview`,
      headerless PCM → local **MP3** via `lameenc` (no system `ffmpeg`), voice
      `Charon`, with the no-British-voice limitation linked to this spec.
- [x] Differences between this spec and what was built are recorded (below).

## Differences between spec and implementation

1. **The script is sent verbatim** — no persona, no style prefix, no
   `systemInstruction` (all rejected or spoken; see requirements finding 5). The
   `SCRIPTER_PERSONA` rules stay in Phase 5; the Narrator adds nothing to the
   words. This is a spec decision, not drift, but it is recorded because a reader
   may expect a narrator persona prompt.
2. **MP3, not OGG/Opus** — dictated by the model returning PCM and the user's
   decision to use `lameenc`. TECH.md's "(OGG/MP3)" already allows this.
3. **The Phase 5 narration text is kept** (user decision) — the voice note
   follows it rather than replacing it.
4. **Failure paths end in `Phase.DONE`, per requirements** — not a deviation.
   An intermediate implementation draft left failures parked in `Phase.NARRATE`;
   that drift was caught in review and fixed (`_narrate` always resolves to
   `DONE`, with `voice_note_sent` only on success). Recorded here only for
   provenance: the shipped behaviour matches the requirements table.
5. **`PcmAudio` became `InlineAudio` + `parse_pcm_format`** — the spec allowed
   "or equivalent internal value". The API's inline audio part is validated into
   `InlineAudio` at the `gemini.py` boundary; the `mimeType` →
   `(rate, channels)` parser lives in `narrator.py` as `parse_pcm_format`.
   Naming/structure difference only; the fail-loud contract is unchanged.
6. **Raw encoder exceptions are typed at the client boundary** — `encode_mp3`'s
   `ValueError` and `lameenc`'s `RuntimeError` are caught in
   `NarratorClient.speak` and re-raised as `GeminiApiError` (chained as
   `__cause__`), so the gateway's documented failure contract holds;
   defence-in-depth tests also prove the gateway degrades even if a raw
   exception escaped (`test_a_raw_encoder_failure_never_crashes_the_loop`).
7. **`duration` lives on the `VoiceNote` contract** — the spec sketched
   `send_voice(chat_id, voice, duration=None)`; the implementation stores the
   ceil-rounded duration on `VoiceNote` (validated ≥ 1 s) and `send_voice`
   derives the form field from it. Same wire behaviour, one fewer parameter.

**Outstanding:** the live Telegram validation (plan task 18, all Live boxes
above). The feature is not "verified" end-to-end until that run happens; the
ROADMAP status row deliberately says so.
