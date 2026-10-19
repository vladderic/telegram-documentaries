# Validation — The Bouncer (Phase 2)

Every box must be checked before merge. Automated checks are run via the dev
scripts; live checks are done against the running bot with real Telegram
traffic.

## Automated (scripts/hooks must be green)

- [x] `scripts/test` — all tests pass (93); new tests were red before the matching
      implementation existed (Red/Green evidence in commit history / report).
- [x] Verdict contract tests: valid payload parses; missing/incorrect fields
      fail loudly at the boundary; junk Gemini response never leaks through as
      a raw dict.
- [x] Bouncer tests: accept (solo real human); reject group / drawing /
      non-human object with assertion that the message references the observed
      subject and carries the correct `RejectionReason`; Gemini timeout →
      typed failure.
- [x] Gateway tests: photo routed to bouncer; text/other media → nudge;
      accept → media stored + phase advanced + confirmation sent; reject →
      quip sent + partial state purged + still awaiting photo; API failure →
      graceful retry message, loud log, no exception into the conversation.
- [x] Startup fails loudly when `GEMINI_API_KEY` missing/empty; log shows
      presence only.
- [x] ruff, ruff-format, mypy (strict), gitleaks all pass (`scripts/hooks`).
- [x] `.env` and temp media dir are untracked (`git check-ignore`).

## Live (running bot, real Telegram client)

- [x] Send a photo of a single real person → in-character confirmation;
      session advances (Phase 3 will continue from there). *(23:52, 23:58)*
- [x] Send a photo of a car (e.g. a Honda Civic) → quip references the vehicle
      and asks for the user, not "no person present". *(23:50 — "A black Audi car")*
- [x] Send a group photo → rejection names that there must be one subject.
      *(23:50 — "a man in a suit standing next to another person")*
- [x] Send a drawing/cartoon of a person → rejection explains it must be a
      real photograph. *(23:57 — pixel art, anime drawing and digital
      illustration all rejected as `NOT_A_REAL_PHOTO`)*
- [x] After each rejection, immediately send a valid photo → accepted without
      `/start`. *(23:57:59 reject → 23:58:20 accept)*
- [x] Send text (and a sticker) while awaiting a photo → in-character nudge,
      no crash. *(text verified live 23:48; non-photo media shares the same
      code path and is covered by unit tests)*
- [x] Logs: verdicts and phase transitions visible; no secret values, no image
      bytes anywhere in the output.

### Live observations / accepted limitations

- Realistic AI-generated photos may still be accepted — the Bouncer is a
  best-effort visual judgment with no forensic detection (as scoped in
  `requirements.md`). The user explicitly accepted this limitation.
- An accepted session gets a holding reply for any further input
  (`photo_out_of_phase` in the logs); full reset semantics arrive in Phase 7.
- **Fixed after live validation (Phase 3 run):** the Bouncer over-rejected
  single-person photos. A shoulder-only portrait and a man holding a plush toy
  were both rejected as if the frame held several subjects, because the prompt
  never said that *only people* count as subjects and that a person may be
  shown in part. The prompt now states both explicitly (regression assertions
  in `test_gemini_vision_client.py::test_sends_guardrailed_structured_request`).

## Spec sync (on user approval)

- [x] `SPECS/ROADMAP.md` Phase 2 row marked complete.
- [x] README status section updated to Phase 2.
- [x] Any implementation-vs-spec drift surfaced to the user and resolved
      (realistic AI photos may pass — user accepted; see Live observations).
