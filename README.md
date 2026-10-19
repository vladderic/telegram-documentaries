# The Telegram Documentaries

A Telegram bot that turns your portrait photo into a narrated, comedy wildlife
documentary about you — hybrid human-animal portrait plus a dramatic
British-documentary voiceover, all inside one Telegram conversation.
The full mission lives in [`SPECS/MISSION.md`](SPECS/MISSION.md).

> **Status: Phase 3 — Interviewer.** The bot boots, long-polls Telegram, and
> gates every conversation on a portrait photo: a Gemini vision verdict
> accepts a solo real human (storing the photo and advancing the session) or
> rejects everything else with a content-specific, in-character quip. An
> accepted photo is then interviewed: 5–7 adaptive questions, one per message,
> ending in a case-file reveal of the dossier, the suggested animal (a bare
> species name) and a one-line description of it. The remaining stages —
> Converter, Scripter, Narrator — arrive in later phases
> ([`SPECS/ROADMAP.md`](SPECS/ROADMAP.md)).

## Pipeline (target architecture)

1. User sends `/start`.
2. User uploads a portrait photo.
3. **Bouncer** — vision gate: a human is present → proceed, otherwise a
   cheeky in-character rejection and a reset.
4. **Interviewer** — 5–7 questions one at a time; builds a per-`chat_id`
   behavioural dossier and suggests an animal (also the orchestrator).
5. **Converter** — photo + dossier → hybrid animal portrait, sent back to
   Telegram.
6. **Scripter** — one paragraph (~60–90 words) of dramatic documentary
   narration.
7. **Narrator** — the script synthesised as a Telegram voice note (Gemini
   TTS).

`/start` / `/restart` purge the session and temp media; out-of-order input at
any stage is handled gracefully and never crashes the bot.

## Local setup

Requirements: Python 3.11+ and git.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # runtime + dev tooling, pinned in pyproject.toml
cp .env.example .env           # then fill in real values (.env is gitignored)
```

Environment variables (loaded from `.env`; never committed, never logged):

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | **Required.** Token from [@BotFather](https://t.me/BotFather). Startup fails loudly with a clear log if it is missing or empty. |
| `GEMINI_API_KEY` | **Required.** Key from [Google AI Studio](https://aistudio.google.com/apikey); the Bouncer calls Gemini vision on every photo and the Interviewer calls Gemini text on every question. Startup fails loudly if it is missing or empty. |

## Run the bot

```bash
python -m telegram_documentaries
```

It long-polls Telegram (`getUpdates`) and runs the two stages built so far:

1. **Bouncer** — any text or non-photo input gets an in-character nudge to send
   a photograph; a photo gets an immediate hold-line and is then judged by
   Gemini vision (accept → the photo is stored under `.tmp/` and the session
   advances; reject → a specific in-character quip and a clean retry).
2. **Interviewer** — an accepted photo gets a short confirmation and the first
   of 5–7 interview questions, one per message, adapted to the answers given
   so far. A message queued *before* the pending question went out is nudged
   rather than recorded. When the interview ends (the model decides, inside the
   5–7 window), the bot reveals the case file: profile, the suggested animal as
   a bare species name, and a one-line description of it.

Shut down gracefully with **Ctrl+C** or **SIGTERM** — even while a long poll is
in flight. Sessions live in memory only, so a restart means a fresh photo.

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
  logging_utils.py # async-call timing + phase-transition log helpers
  bot_gateway.py   # entry point: long-polling loop, photo + interview flows
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
