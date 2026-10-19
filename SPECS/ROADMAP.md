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
- **Amendment (Phase 5, user request):** the interview deliberately covers the
  material the narration is built from — the nightly routine, what and when the
  subject eats or snacks, and one repeated odd habit — and finishing is gated on
  that coverage. The profile is written as concrete, quotable behaviour rather
  than abstractions, because the dossier is the Scripter's only fuel: a live
  Phase 5 run on a thin dossier produced a paragraph of generic species biology
  instead of the subject's own quirks. See
  `SPECS/2026-10-19-interviewer/requirements.md` (live observations).
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

## Phase 4b — Full-resolution portrait input
The portrait may be sent as a **file** (document) as well as a photo, so the
Converter works from Telegram's original, uncompressed bytes instead of the
compressed `PhotoSize` rendition.
- **Acceptance:** a file portrait completes the whole pipeline exactly like a
  photo, the stored bytes are the original ones, the Converter is told the real
  media type, and non-image files are still nudged; a plain photo is unaffected.
- **Implementation:** `Document`/`PortraitSource` contracts, one
  `bouncer.portrait_source` rule shared by the router, the interview guard and
  `build_reply`, `bouncer.fetch_portrait`, `_store_photo(..., mime_type)` and
  `SessionState.photo_mime_type`.
- **Serves:** portrait quality (the compressed photo caps the fusion).

## Phase 5 — Scripter
One-paragraph (~60–90 word) dramatic British-documentary narration built from
the dossier.
- **Acceptance:** script generated from dossier, length-bounded, in the
  documentary voice.
- **Implementation:** `scripter.py` (`SCRIPTER_MODEL = "gemini-3.1-flash-lite"`,
  `MAX_WORDS = 90`, `SCRIPTER_PERSONA`/`SCRIPT_RULES`, `build_prompt`,
  `SCRIPT_SCHEMA`, `ScripterClient.write_script`) and the typed `Script` contract
  (whitespace normalised to one paragraph). The persona and rules go to Gemini as
  a `systemInstruction` — the first stage in this repo to use that field;
  `GeminiClient.generate` only sends it when one is given. The narration is sent
  as plain text (no parse mode) immediately after the portrait by `_write_script`,
  on the same single finish path, and kept on `SessionState.script_text` for
  Phase 6. The two stages are independent: a jammed darkroom still narrates, a
  lost script still leaves the portrait standing. Over-length output is sent as
  written with a `scripter_over_word_limit` warning.
- **Voice fix (live finding):** the narrator is pinned to the subject's own
  documented habits — never the species' natural history — with concrete dossier
  detail preferred over abstract traits, and the dossier leads the user turn
  while the species note is labelled as flavour only. The interview was
  strengthened alongside it (see Phase 3's amendment); a controlled live check
  showed the same dossiers now produce quirk-anchored narration instead of
  raccoon biology.
- **Live verified (`2026-10-19`):** a full Telegram run (giant panda, 5 answers)
  delivered the portrait and then an 82-word narration naming the same species,
  with one-narration-per-session holding (`LATER_PHASE_REPLY` on later
  messages). Boxes and evidence: `SPECS/2026-10-19-scripter/validation.md`.
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
| 5 — Scripter | Complete |
| 6 — Narrator | Not started |
| 7 — Resilience | Not started |
| 8 — Deployment (zero-cost) | Not started |
