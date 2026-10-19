# Validation — The Interviewer (Phase 3)

Every box must be checked before merge. Automated checks run via the dev
scripts; live checks are done against the running bot with real Telegram
traffic.

## Automated (scripts/hooks must be green)

- [ ] `scripts/test` — all tests pass; new tests were red before the matching
      implementation existed (Red/Green evidence in commit history / report).
- [ ] Shared-client extraction: Bouncer behaviour unchanged (existing Bouncer
      tests still green); typed `GeminiApiError` on transport/HTTP/malformed
      response; API key never in any error message.
- [ ] Contract tests: `InterviewStep` ASK/FINISH consistency enforced; malformed
      model output fails loudly, never a raw dict downstream; empty profile /
      animal rejected.
- [ ] State tests: derived question number and finished flag correct;
      `clear()` resets the interview; two `chat_id`s never share answers.
- [ ] State-machine tests: strictly one question per turn; a next question is
      only asked after an answer; minimum 5 answers before finishing is allowed;
      `distil` forced at 7 answers; no path exceeds 7 questions.
- [ ] Edge tests: non-text / empty / whitespace input → nudge + repeat, no
      advance, no model call; model failure preserves answers and pending
      question, retry does not duplicate.
- [ ] Logging tests: question number, answer receipt and finish logged; no
      secret values, no raw message bytes.
- [ ] ruff, ruff-format, mypy (strict), gitleaks all pass (`scripts/hooks`).
- [ ] `.env` and `.tmp/` remain untracked (`git check-ignore`).

## Live (running bot, real Telegram client)

- [ ] Send an accepted portrait photo → confirmation followed immediately by
      **Q1**; exactly one question in the message.
- [ ] Answer each question → the next question arrives only after the answer;
      never two questions in one message; no repeated questions.
- [ ] The interview runs for 5–7 questions and then stops on its own.
- [ ] Send a sticker (and a photo) mid-interview → in-character nudge repeating
      the pending question; the interview then continues correctly from the
      same question.
- [ ] Send an empty/whitespace message → same nudge, no advance.
- [ ] After answering, the case-file reveal arrives: rich personality profile +
      suggested animal, in character.
- [ ] Any further input after finishing → holding reply (no crash, no new
      interview).
- [ ] Logs: per-turn question numbers and the finish visible; token redacted;
      no image bytes.

### Live observations / accepted limitations

- **Fixed during live validation:** the first build interviewed the user as if
  they were an animal ("packs", "prey", "watering holes"), because the persona
  prompt framed the crew as studying "a fascinating new specimen". The persona
  now pins the subject as a human being and forbids animal-life questions; the
  animal comparison is drawn later from the transcript (regression test:
  `test_prompt_interviews_the_person_not_an_animal`).
- A leading-slash message mid-interview is treated as an ordinary answer (full
  command handling is Phase 7).
- Restarting the bot clears in-memory sessions (per MISSION, no persistence).

## Spec sync (on user approval)

- [ ] `SPECS/ROADMAP.md` Phase 3 row marked complete.
- [ ] README status section updated to Phase 3.
- [ ] Any implementation-vs-spec drift surfaced to the user and resolved.
