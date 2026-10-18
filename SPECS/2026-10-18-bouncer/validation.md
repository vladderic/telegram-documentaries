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

- [ ] Send a photo of a single real person → in-character confirmation;
      session advances (Phase 3 will continue from there).
- [ ] Send a photo of a car (e.g. a Honda Civic) → quip references the vehicle
      and asks for the user, not "no person present".
- [ ] Send a group photo → rejection names that there must be one subject.
- [ ] Send a drawing/cartoon of a person → rejection explains it must be a
      real photograph.
- [ ] After each rejection, immediately send a valid photo → accepted without
      `/start`.
- [ ] Send text (and a sticker) while awaiting a photo → in-character nudge,
      no crash.
- [ ] Logs: verdicts and phase transitions visible; no secret values, no image
      bytes anywhere in the output.

## Spec sync (on user approval)

- [ ] `SPECS/ROADMAP.md` Phase 2 row marked complete (currently "In progress"
      pending the live checks above).
- [x] README status section updated to Phase 2.
- [ ] Any implementation-vs-spec drift surfaced to the user and resolved.
