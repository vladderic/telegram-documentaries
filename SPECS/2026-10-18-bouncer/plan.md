# Plan — The Bouncer (Phase 2)

Red/Green TDD throughout: write the failing test first, then the minimum code
that makes it pass. Run checks with the dev scripts (`scripts/test`,
`scripts/hooks` — ground truth, see README / `SPECS/TECH.md`).

## Task group 1 — Session state foundation

1. Add a typed, versioned per-`chat_id` session model (Pydantic): phase enum
   (`AWAITING_PHOTO`, `BOUNCER_CHECK`, `AWAITING_INTERVIEW`, …), stored photo
   reference, clear-state operation.
2. Red tests: state transitions (await → bouncer check → accept → awaiting
   interview; reject → back to awaiting photo with media purged).

## Task group 2 — Verdict contract & Gemini vision client

3. Red tests for `BouncerVerdict` (accepted / observed_subject / reason enum /
   message) parsing: valid verdict, missing field, wrong types, junk payload.
4. Implement the verdict model + rejection-reason enum.
5. Red tests for the Gemini client wrapper (existing httpx, no new runtime
   dep unless justified): structured-output request build (prompt guardrails:
   solo real human only, ≤2 sentences, in-character, must state the reason),
   typed parse of the response, timeout and API-error paths returning a
   distinguishable failure — all against mocked transports, never the real API.

## Task group 3 — Bouncer logic

6. Red tests for `check_photo()`: accept path (solo real human), reject paths
   (group, drawing, car/animal/object) asserting the quip references the
   observed subject and the reason enum matches, API-failure path.
7. Implement the bouncer: download photo (size/type validated at the edge),
   call the vision client, return the typed verdict.

## Task group 4 — Gateway wiring

8. Red tests for routing at the awaiting-photo phase: photo → bouncer;
   text/other media → in-character nudge; verdict accepted → store media file
   under the temp dir (keyed by chat_id), advance phase, send confirmation;
   verdict rejected → send quip, purge partial state, remain awaiting photo;
   verdict failure → graceful in-character retry message.
9. Implement routing + decorator-based logging (phase transitions, verdict
   reason + chat_id; never bytes or secrets).

## Task group 5 — Config, hygiene, docs

10. `GEMINI_API_KEY` becomes required at startup (loud failure, presence-only
    logging — extend the Phase 1 pattern); tests for missing key.
11. Gitignore the temp media dir; verify `.env` still untracked.
12. Update README (phase status, env table, architecture sketch) and
    `SPECS/ROADMAP.md` status table **after validation passes**.

## Task group 6 — Full gate

13. `scripts/test` green (all new + existing tests).
14. `scripts/hooks` green: ruff, ruff-format, mypy, pytest, gitleaks.

## Out of scope

Interviewer questions, full `/start` // `/restart` purge (Phase 7),
forensic AI-image detection, multi-person handling UI.
