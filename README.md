# The Telegram Documentaries

A Telegram bot that turns your portrait photo into a narrated, comedy wildlife
documentary about you — hybrid human-animal portrait plus a dramatic
British-documentary voiceover, all inside one Telegram conversation.
The full mission lives in [`SPECS/MISSION.md`](SPECS/MISSION.md).

> **Status: Phase 7 (reset + chat-clear slices) — Narrator complete.** The bot
> boots, long-polls Telegram, and
> gates every conversation on a portrait photo: a Gemini vision verdict
> accepts a solo real human (storing the photo and advancing the session) or
> rejects everything else with a content-specific, in-character quip. An
> accepted photo is then interviewed: 5–7 adaptive questions, one per message,
> ending in a case-file reveal of the dossier, the suggested animal (a bare
> species name) and a one-line description of it. The stored photo and the
> dossier are then fused by Gemini image generation into one hybrid portrait
> and uploaded to the chat, captioned with the suggested species; the narrator
> immediately follows with one paragraph of documentary narration as a plain
> text message, then speaks that same paragraph as a Telegram voice note
> (Gemini TTS, encoded locally to MP3) — exactly one voice note per session.
> `/start` and `/restart` reset a chat in-process — deleting the bot's own
> messages and that chat's temp media — with one confirmation+greeting reply.
> This is one slice of Phase 7; the remaining resilience slices and deployment
> are described in [`SPECS/ROADMAP.md`](SPECS/ROADMAP.md).

## Pipeline

1. User sends `/start`.
2. User uploads a portrait photo.
3. **Bouncer** — vision gate: a human is present → proceed, otherwise a
   cheeky in-character rejection and a reset.
4. **Interviewer** — 5–7 questions one at a time; builds a per-`chat_id`
   behavioural dossier and suggests an animal (also the orchestrator).
5. **Converter** — photo + dossier → hybrid animal portrait, sent back to
   Telegram.
6. **Scripter** — one paragraph (~60–90 words) of dramatic documentary
   narration, sent as a plain text message.
7. **Narrator** — the same paragraph synthesised by Gemini TTS and delivered
   as a Telegram voice note (encoded locally to MP3).

`/start` and `/restart` reset the chat in-process: every message **the bot
itself sent** in that chat is deleted, the session goes back to the
awaiting-photo phase, that chat's own temp-media directory is deleted, and one
confirmation+greeting message comes back — from any phase, with no bot restart.
The bot cannot delete the *user's* incoming messages (a Telegram platform
limit), so those remain; only IDs the bot recorded for its own sends are
removed. Out-of-order input at any stage is handled gracefully and never
crashes the bot.

## Local setup

Requirements: Python 3.11+ and git.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # runtime + dev tooling, pinned in pyproject.toml
cp .env.example .env           # then fill in real values (.env is gitignored)
```

The Narrator encodes the TTS PCM to MP3 with
[`lameenc`](https://pypi.org/project/lameenc/) — a pure pip wheel (no system
`ffmpeg`) pulled in by the command above, so Telegram plays a real voice note.

Environment variables (loaded from `.env`; never committed, never logged):

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | **Required.** Token from [@BotFather](https://t.me/BotFather). Startup fails loudly with a clear log if it is missing or empty. |
| `GEMINI_API_KEY` | **Required.** Key from [Google AI Studio](https://aistudio.google.com/apikey); the Bouncer calls Gemini vision on every photo, the Interviewer calls Gemini text on every question, the Converter calls Gemini image generation when the interview ends, the Scripter calls Gemini text for the narration, and the Narrator calls Gemini TTS to speak it. Startup fails loudly if it is missing or empty. |

## Run the bot

```bash
python -m telegram_documentaries
```

It long-polls Telegram (`getUpdates`) and runs every stage built so far:

1. **Bouncer** — any text or non-portrait input gets an in-character nudge to
   send a photograph; a portrait gets an immediate hold-line and is then judged
   by Gemini vision (accept → it is stored under `.tmp/` and the session
   advances; reject → a specific in-character quip and a clean retry).
   A portrait may be sent as a normal photo **or as a file** — Telegram
   compresses photos, so sending the image as a file keeps its original
   resolution and gives the Converter more to work with. Either way the cap is
   10 MB and only files explicitly typed as an image count (a PDF, or a file
   with no media type, gets the same nudge as text).
2. **Interviewer** — an accepted photo gets a short confirmation and the first
   of 5–7 interview questions, one per message, adapted to the answers given
   so far. A message queued *before* the pending question went out is nudged
   rather than recorded. The questions deliberately cover the material the
   narration is built from — the nightly routine, what and when you eat or
   snack, and a repeated odd habit — and the interview only ends once it has.
   When the interview ends (the model decides, inside the 5–7 window), the bot
   reveals the case file: profile, the suggested animal as a bare species name,
   and a one-line description of it.
3. **Converter** — the stored portrait and the dossier go to Gemini image
   generation (after a "darkroom" hold line, since generation takes seconds)
   and the hybrid portrait comes back as a captioned photo. The stored bytes
   are handed over unmodified, together with the media type they were uploaded
   with. If generation, the upload or reading the stored portrait fails, the
   bot says so in character and the session moves on — no crash, no partial
   state.
4. **Scripter** — the dossier goes to Gemini text with the narrator persona and
   rules as the model's system instruction, and one paragraph (~60–90 words) of
   narration comes back as a plain text message, sent right after the portrait.
   The narration is about *your own* habits — the dossier is its only source —
   never a lecture about the animal you were cast as. A 91-word+ script is still
   sent (words are never rewritten), with a warning in the log. The portrait and
   the narration are independent: if the darkroom jams the narrator still
   speaks, and if the narrator loses his page the portrait still stands. The text
   is kept on the session for the next stage.
5. **Narrator** — that exact paragraph is sent verbatim to Gemini TTS
   (`gemini-3.1-flash-tts-preview`, voice `Charon`) and the returned PCM
   (24 kHz mono) is encoded locally to MP3 with `lameenc` (no system `ffmpeg`),
   then uploaded with `sendVoice` as a voice note. The words the user already
   read are the only source, so the audio can never say anything else; the text
   and the voice note are independent — if synthesis, encoding or the upload
   fails, the bot says so in character and the text still stands (no crash, no
   retry). Exactly one voice note is ever sent per session: later messages get
   the holding reply.
6. **Reset** — `/start` or `/restart` at any point resets the chat in-process:
   every message **the bot sent** in that chat is deleted, the session returns
   to the awaiting-photo phase, and that chat's own temp-media directory is
   deleted (scoped to the chat, idempotent if already gone), with one
   confirmation+greeting message in reply. The bot cannot delete the user's own
   messages (a Telegram platform limit), so those stay; only IDs the bot
   recorded for its own sends are removed, and only for the current in-memory
   session. The command is recognised before any stage sees the message — it
   can never become an interview answer or a portrait nudge — and a failed
   deletion or purge still clears the session, loudly logged. The reply is sent
   last and is itself tracked, so a second reset deletes it. No bot restart is
   ever needed to recover a stuck session.

Shut down gracefully with **Ctrl+C** or **SIGTERM** — even while a long poll is
in flight. Sessions live in memory only, so a process restart means a fresh
photo; recover a stuck chat with `/start` instead of restarting the bot.

## Development scripts

Ground truth for tests, lint, and type checks (documented in `SPECS/TECH.md`):

| Script | What it runs |
|---|---|
| `scripts/test` | `pytest` — the whole test suite. |
| `scripts/hooks` | Every repo check: ruff lint, ruff-format, mypy, pytest, gitleaks (implemented as `pre-commit run --all-files`). |

```bash
scripts/test          # just the tests
scripts/hooks         # the full gate — run this before committing
```

## Pre-commit hooks

```bash
pre-commit install            # once per clone — runs the checks on commit
pre-commit run --all-files    # same checks on demand (= scripts/hooks)
```

The five checks defined in `.pre-commit-config.yaml`:

| # | Check | Provided by |
|---|---|---|
| 1 | `ruff` (lint) | [astral-sh/ruff-pre-commit](https://github.com/astral-sh/ruff-pre-commit), pinned to the ruff version in `pyproject.toml` |
| 2 | `ruff-format` | same mirror |
| 3 | `mypy` | local hook — `disallow_untyped_defs` and related strictness flags; runs in your environment so it can resolve project dependencies |
| 4 | `pytest` | local hook → `scripts/test` |
| 5 | `gitleaks` (secret scan) | [official mirror](https://github.com/gitleaks/gitleaks), auto-installed by pre-commit |

All tooling configuration (ruff, ruff-format, mypy, pytest) lives in
`pyproject.toml`.

## Tests

Red/Green TDD per `SPECS/TECH.md`: tests are written first and must fail for
the right reason before the implementation makes them pass.

- `tests/unit/` — isolated and deterministic. Telegram traffic is mocked
  (`unittest.mock`, `httpx.MockTransport`); the real Telegram API is never
  contacted. All secret-looking values are obviously fake placeholders.

```bash
scripts/test
```

## Repository layout

```
src/telegram_documentaries/
  config.py        # .env loading, Settings, secret-redacting log filter
  models.py        # typed Pydantic models (phases, verdicts, interview contracts)
  gemini.py        # shared Gemini REST client (auth header, timeouts, errors)
  bouncer.py       # Gemini vision client: photo → structured accept/reject verdict
  interviewer.py   # Gemini text client: adaptive questions + dossier/profile
  converter.py     # Gemini image client: photo + dossier → hybrid portrait
  scripter.py      # Gemini text client: dossier → one-paragraph narration
  narrator.py      # Gemini TTS client: narration → encoded MP3 voice note
  logging_utils.py # async-call timing + phase-transition log helpers
  bot_gateway.py   # entry point: long-polling loop, photo + interview + reveal flows, /start /restart reset
  __main__.py      # python -m telegram_documentaries
tests/unit/        # Red/Green test suite
scripts/           # dev scripts: test, hooks (ground truth, see TECH.md)
SPECS/             # binding constitution: MISSION, TECH, ROADMAP
```

## Secrets policy (non-negotiable)

- `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY` live in `.env` only — gitignored,
  never committed.
- Secret values are never logged: configuration logs report *presence* only,
  API errors are sanitised, and a redaction filter scrubs secrets from every
  log handler (including tracebacks) once settings load.
- `gitleaks` scans every commit for accidentally committed secrets.
- Tests only ever use placeholder secrets.
