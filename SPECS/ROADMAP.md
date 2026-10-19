# ROADMAP — The Telegram Documentaries

Ordered build plan. One phase per pipeline capability. A phase is marked
complete only when its acceptance criteria are verified. Each phase also notes
which MISSION success criterion it serves.

## Phase 1 — Repository & gateway
Project skeleton, `.env` loading, Telegram long-polling loop that receives
updates and echoes a hardcoded reply.
- **Acceptance:** bot responds to any message with the echo; secrets load from
  `.env` and are never logged.
- **Serves:** happy-path foundation.

## Phase 2 — Bouncer
Vision gate: confirm a human is present in the uploaded photo; cheekily reject
non-human images and reset state.
- **Acceptance:** human photo → proceed to interview; non-human/invalid image →
  in-character rejection + state reset; no crash on any media type.
- **Serves:** happy path + out-of-order input handling.

## Phase 3 — Interviewer
Sequential stateful Q&A: 5–7 questions one at a time, dossier accumulation
keyed by `chat_id`, final dossier summary + suggested animal.
- **Acceptance:** full interview completes with correct phase transitions;
  dossier persists across the Q&A; wrong-type messages mid-interview handled
  gracefully.
- **Serves:** happy path.

## Phase 4 — Converter
Multimodal hybrid portrait: original photo + dossier → Gemini Flash Image →
portrait sent directly to Telegram.
- **Acceptance:** user receives an image in Telegram; failures degrade
  gracefully with a logged error, never a crash.
- **Implementation:** `converter.py` (`CONVERTER_MODEL = "gemini-3.1-flash-image"`,
  `build_prompt`, `ConverterClient.fuse`), the typed `GeneratedImage` contract
  (base64 decoded at the boundary), `TelegramClient.send_photo` (multipart,
  never retried), and the delivery step in `_finish_interview` — the single
  finish path, so both the model-FINISH and ceiling-`distil` routes reach the
  Converter. The client is built once in `run_bot` and closed on shutdown.
- **Serves:** happy path.

## Phase 5 — Scripter
One-paragraph (~60–90 word) dramatic British-documentary narration built from
the dossier.
- **Acceptance:** script generated from dossier, length-bounded, in the
  documentary voice.
- **Serves:** happy path.

## Phase 6 — Narrator
TTS synthesis via Gemini TTS, rendered to Telegram-compatible audio (OGG/MP3),
delivered as a voice note.
- **Acceptance:** user receives a playable voice note matching the script.
- **Serves:** happy path (end-to-end).

## Phase 7 — Resilience
`/restart` and `/start` full reset (purge state + temp files), wrong-payload-
at-wrong-stage guards across all phases, API-timeout fallbacks.
- **Acceptance:** reset mid-every-phase starts clean; timeouts produce a
  graceful user-facing response and a loud log; no phase crashes on unexpected
  input.
- **Serves:** reset + out-of-order success criteria.

## Phase 8 — Deployment (zero-cost only)
Run the bot as a supervised, always-on service so an unexpected death (crash,
OOM kill, box restart) never leaves it silently offline.
- **Acceptance:** on one zero-cost target the bot runs as a supervised service
  and killing its process brings it back within seconds; restarting the host
  brings the bot back with it; secrets come from the environment, never from the
  image or the repo; `README.md` documents how to deploy it and how to check it
  is alive; the runbook is verified end-to-end on that target.
- **Constraint (user decision):** **no paid hosting.** Free targets only — a
  machine the user already owns, or a free-tier VM (Oracle Cloud Always Free /
  GCP `e2-micro`; a card may be required for identity verification but is never
  charged). Paid platforms are out of scope: the former free worker tiers
  (Fly.io / Railway / Render) no longer keep a long-polling worker online for
  free, and Render's free web services sleep after ~15 minutes.
- **Note (user decision):** session state stays **in-memory** (per MISSION, no
  database). A restart mid-interview means the user sends a photo again — this
  is accepted, not a bug to fix in this phase.
- **Serves:** assessment availability — the bot is up when it is needed.

## Status

| Phase | Status |
|-------|--------|
| 1 — Repository & gateway | Complete |
| 2 — Bouncer | Complete |
| 3 — Interviewer | Complete |
| 4 — Converter | Complete |
| 5 — Scripter | Not started |
| 6 — Narrator | Not started |
| 7 — Resilience | Not started |
| 8 — Deployment (zero-cost) | Not started |
