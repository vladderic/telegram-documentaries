# Requirements — The Narrator (Phase 6)

Roadmap phase: **6 — Narrator**. Constitution: `SPECS/MISSION.md`,
`SPECS/TECH.md`, `SPECS/ROADMAP.md`.

## Context

Phase 5 ends with the narration text delivered and stored on the session
(`SessionState.script_text`). Phase 6 is the last stage of the happy path: turn
that paragraph into a **Telegram voice note** so the user receives their hybrid
portrait followed by a spoken, British-documentary voiceover — the promise in
MISSION.md ("photo in → hybrid portrait + voice note out").

Per TECH.md the Narrator is **not an agent**: it is Gemini TTS
(`gemini-3.1-flash-tts-preview`) plus a local encode, delivered with `sendVoice`.

## Facts established before writing this spec (live API, real key)

These were probed directly, because the API shape decides the whole design:

1. `POST /v1beta/models/gemini-3.1-flash-tts-preview:generateContent` with
   `x-goog-api-key` — the same auth and base URL as every other stage.
2. Request: `contents[0].parts[0].text` (the text to recite) and
   `generationConfig.responseModalities = ["AUDIO"]` with
   `speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName`.
3. Response: `candidates[0].content.parts[0].inlineData` —
   `data` (base64) and `mimeType = "audio/l16; rate=24000; channels=1"`, i.e.
   **headerless 16-bit little-endian PCM, 24 kHz, mono**.
4. **The model cannot emit OGG/Opus, MP3 or WAV.** Every `responseFormat` shape
   (`{"mimeType": …}`, `{"audioFormat": …}`, string values, on both 3.1 and 3.8)
   is rejected as an unknown field. Encoding must happen locally.
5. **The model cannot be styled out-of-band.** `speech_metadata` /
   `speechMetadata` → *"Speech metadata is not supported for this model"*;
   a `systemInstruction` → *"Developer instruction is not enabled for this
   model"*. Style can only come from text in the prompt — and a live measurement
   proved a style prefix is **spoken** (6.72 s plain vs 9.28 s with
   *"Read this in a wry… voice: "* prepended). Therefore the script is sent
   **verbatim** and the voice's own character carries the tone.
6. Telegram `sendVoice` accepts **`.OGG` (Opus), `.MP3` or `.M4A`**; anything
   else is sent as a file, not a playable voice note.
7. `ffmpeg` is **not** present in this environment. `lameenc` (pure pip wheel,
   `cp311` manylinux) encodes PCM → MP3 with no system libraries.

## Confirmed user decisions

1. **Branch from `main`** after PR #5 (Phase 5) merged — no stacked diff.
2. **Encoder: `lameenc` → MP3** voice note (small compiled wheel, no numpy, no
   system `ffmpeg`). Telegram plays MP3 voice notes.
3. **Voice: `Charon`** — the deep, measured prebuilt voice closest to a
   documentary narrator. No injected style text (see finding 5).
4. **Delivery: text first, then the voice note.** The Phase 5 narration text
   stays exactly where it is; the voice note follows it. A TTS failure therefore
   never costs the user the narration they can read.

## Behaviour

Phases used: `SCRIPT` (narration text sent — Phase 6's entry point), `NARRATE`
(synthesis in progress), `DONE` (voice note delivered, or synthesis failed and
the pipeline gave up gracefully). All three already exist in the `Phase` enum.

| Situation | Behaviour |
|---|---|
| Narration text delivered | Synthesise the audio from **those exact words** (`session.script_text`), then send the voice note |
| TTS returns PCM | Encode to MP3 (24 kHz mono) and `sendVoice` |
| TTS call fails (HTTP/transport/malformed) | Log loudly, send an in-character line ("the narrator's voice…"), leave the text narration standing, phase → `DONE`; **no retry** |
| Encoding fails | Same as a TTS failure — the text still stands, phase → `DONE` |
| `sendVoice` fails | Same degrade path; the text still stands |
| Any message after `DONE` (or `NARRATE`) | `LATER_PHASE_REPLY` — no crash, no second run |
| Script already synthesized for this session | Never synthesize twice (one voice note per session, mirroring Phase 5) |

### Contracts (typed, at the boundary — TECH.md)

- New module `narrator.py`:
  - `NARRATOR_MODEL = "gemini-3.1-flash-tts-preview"`,
    `DEFAULT_VOICE = "Charon"`, `PCM_SAMPLE_RATE = 24000`, `PCM_CHANNELS = 1`.
  - `VoiceNote` — typed contract for the encoded result: `data: bytes` (non-empty
    MP3), `mime_type: Literal["audio/mpeg"]`. Validated at construction
    (`ValidationError` on empty/blank bytes), mirroring `Script`/`GeneratedImage`.
  - `PcmAudio` (or equivalent internal value) — the validated raw PCM from the
    API: bytes + sample rate + channels, with the rate/channels parsed from the
    `mimeType` string (`audio/l16; rate=24000; channels=1`). A malformed or
    unsupported `mimeType` (not 16-bit, not mono, unexpected rate) **fails
    loudly** with `GeminiApiError` rather than being guessed at.
  - `NarratorClient.speak(script: str) -> VoiceNote` — one TTS call + local
    encode; `close()` releases the HTTP pool.
  - `encode_mp3(pcm, sample_rate, channels) -> bytes` — the pure encode step,
    separately testable without any network.
- `GeminiClient.generate_audio(...)` — one new shared method that posts the TTS
  request and returns the inline audio part (base64 + `mimeType`), returning a
  typed value or raising `GeminiApiError`. It reuses the existing `_call`
  transport/error plumbing, so the API key is redacted from every message.
- `SessionState` gains a `voice_note_sent: bool = False` flag (v1.3) so
  "one voice note per session" is derivable, and `clear()` resets it. No audio
  bytes are ever stored on the session.
- `TelegramClient.send_voice(chat_id, voice: VoiceNote, duration: int | None)`
  — multipart upload using the existing `_request(..., files=...)` path (the same
  one `sendPhoto` uses). Never retried.

### Length, duration and size

- Telegram voice notes are capped at 50 MB; a ~90-word MP3 at 24 kHz is a few
  tens of KB, far inside the limit. The `sendVoice` upload therefore needs no
  size guard beyond the typed non-empty contract (YAGNI).
- `duration` is computed from the PCM length
  (`frames / sample_rate`) and passed to `sendVoice` so Telegram renders the
  waveform length correctly.

### Failure & logging

- Decorator-based logging (`log_async_call`) around the TTS call, and an event
  line on delivery: `narrator_speak` (voice, PCM bytes) and
  `voice_delivered chat_id=… bytes=… duration=…`. Never secrets, never raw audio
  bytes in logs.
- Failures follow TECH.md: degrade in-character, never raise into the
  conversation, never un-deliver the text narration that already succeeded.

### Scope cuts (YAGNI)

- No audio streaming (`streamGenerateContent`); one unary call.
- No multi-speaker / dialogue; single speaker only.
- No voice picker, no user preference, no per-user voice — one fixed voice.
- No caching of audio, no persistence (MISSION: in-memory only).
- No transcription, waveform/`waveform` bytes, or captions on the voice note.
- No re-synthesis, no "regenerate the voice" command.

## Success criteria (maps to ROADMAP Phase 6 acceptance)

- The user receives a **playable voice note** whose speech matches
  `session.script_text`.
- The narration text still arrives (Phase 5 unchanged); the voice note follows.
- A TTS/encode/send failure degrades in-character and never loses the text or
  the portrait.
- Exactly one voice note per session; later input gets the holding reply.
- `./scripts/test` and `./scripts/hooks` fully green; secrets never logged.
