# TECH — The Telegram Documentaries

Technical contract for the project. Every agent and feature spec defers to this.

## Stack

- **Language:** Python
- **Transport:** Telegram Bot API via **long polling** (no webhooks, no public URL).
- **Agent framework:** Google Agent Development Kit (ADK).
- **Models:**
  - **Bouncer** — Gemini 3.1 Flash Lite (vision): human-present check, cheeky
    rejection. Text/image classification only — no TTS, no video.
  - **Interviewer** — Gemini 3.1 Flash Lite: 5–7 sequential questions, dossier
    accumulation, suggested animal. Doubles as the **orchestrator**.
  - **Converter** — Gemini 3.1 Flash Image: multimodal fusion of original photo +
    dossier → hybrid animal portrait, returned directly to Telegram (no
    intermediate text hop).
  - **Scripter** — Gemini 3.1 Flash Lite: one-paragraph (~60–90 word) dramatic
    British-documentary narration.
  - **Narrator** — *not an agent*: Gemini TTS (`gemini-3.1-flash-tts-preview`),
    rendered to a Telegram-compatible audio format (OGG/MP3).
- **Secrets:** `.env` — `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`. Gitignored; never
  committed.

## Architecture

- **ADK hub-and-spoke:** the Interviewer is the orchestrator; every other stage
  is a discrete agent/module it can route to.
- **Explicit state machine.** Session phases advance one way through:
  `AWAITING_PHOTO → BOUNCER_CHECK → INTERVIEW → CONVERT → SCRIPT → NARRATE → DONE`.
  Out-of-order input at any phase is handled gracefully (see error policy), never
  allowed to corrupt state.
- **Session state:** in-memory, keyed by `chat_id`, versioned schema. One shared
  state driver — no ad-hoc per-agent state stores. `/start` and `/restart` purge
  the session state and any temporary media for that `chat_id` without
  restarting the process.

## Contracts at boundaries

- Parse Telegram updates, Gemini responses, and TTS output into **typed Pydantic
  models** at the edge. Never pass raw dicts or unvalidated payloads across
  module boundaries.
- Treat all external input (Telegram messages, media, model outputs) as
  **untrusted and arbitrary** — validate type, size, and shape before use.

## Logging & error policy

- **Comprehensive structured logging** throughout. Prefer decorators for
  cross-cutting logging over sprinkling log calls through business logic.
- On non-critical, user-invisible work: **fail loudly and log**.
- On a validated user's conversation path: **catch errors and degrade
  gracefully** so the conversation continues — log loudly, never raise into the
  user's flow.
- Forbidden: bare `except: pass`, swallowed exceptions, un-logged fallbacks.

## Testing

- **Red/Green TDD.** Tests are written before code.
- Dev scripts live in `scripts/` (e.g. `scripts/test`, `scripts/hooks`) and are
  the ground truth for tests, lint, and type checks. They are documented here and
  in the README.

## Repo hygiene

- `.env` in `.gitignore`; `.env.example` committed as the template.
- Reproducible environment: dependencies pinned in a lockfile or requirements
  file; setup steps documented in the README.

## README policy

The README must document, and stay in sync with: local setup (env vars, install),
how to run the bot, how to run the dev scripts (`scripts/test`, `scripts/hooks`),
and a high-level pipeline description matching MISSION.md.
