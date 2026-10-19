# Validation — The Interviewer (Phase 3)

Every box must be checked before merge. Automated checks run via the dev
scripts; live checks are done against the running bot with real Telegram
traffic.

## Automated (scripts/hooks must be green)

- [x] `scripts/test` — all tests pass; new tests were red before the matching
      implementation existed (Red/Green evidence in commit history / report).
      *185 tests green on the final commit.*
- [x] Shared-client extraction: Bouncer behaviour unchanged (existing Bouncer
      tests still green); typed `GeminiApiError` on transport/HTTP/malformed
      response; API key never in any error message.
- [x] Contract tests: `InterviewStep` ASK/FINISH consistency enforced; malformed
      model output fails loudly, never a raw dict downstream; empty profile /
      animal / animal description rejected.
- [x] State tests: derived question number and finished flag correct;
      `clear()` resets the interview; two `chat_id`s never share answers.
- [x] State-machine tests: strictly one question per turn; a next question is
      only asked after an answer; minimum 5 answers before finishing is allowed;
      `distil` forced at 7 answers; no path exceeds 7 questions.
- [x] Edge tests: non-text / empty / whitespace input → nudge + repeat, no
      advance, no model call; model failure preserves answers and pending
      question, retry does not duplicate.
- [x] Latency/robustness tests: a photo is acknowledged before the slow
      download+judge; ordinary API calls use the short timeout while
      `getUpdates` uses the long-poll window; a stalled idempotent GET is
      retried once, a stalled `sendMessage` never is; a message timestamped
      before the pending question is nudged, not recorded (with clock-slack
      head-room for real answers).
- [x] Logging tests: question number, answer receipt and finish logged; no
      secret values, no raw message bytes.
- [x] ruff, ruff-format, mypy (strict), gitleaks all pass (`scripts/hooks`).
- [x] `.env` and `.tmp/` remain untracked (`git check-ignore`).

## Live (running bot, real Telegram client)

- [x] Send an accepted portrait photo → an immediate "one moment" hold-line,
      then the confirmation followed immediately by **Q1**; exactly one
      question in the message. *Live: hold-line 0.2s after the photo, before
      the download; confirmation + Q1 followed.*
- [x] Answer each question → the next question arrives only after the answer;
      never two questions in one message; no repeated questions. *Live:
      observed turn by turn for six answers.*
- [x] Type a message *while* a photo is being processed and send it before the
      question arrives → it is nudged with the question repeated, never taken
      as the answer. *Automated: `TestStaleMessageGuard`, three cases
      (queued-before-question not recorded; within the 3s clock slack it is
      still an answer; send time stamped). Live: the nudge-and-repeat path
      was observed mid-interview.*
- [x] The interview runs for 5–7 questions and then stops on its own. *Live: 6
      questions, self-finish at 11:02; an earlier run finished cleanly at 5.*
- [x] Send a sticker (and a photo) mid-interview → in-character nudge repeating
      the pending question; the interview then continues correctly from the
      same question. *Live: `interview_nudge` mid-interview; the next answer
      was recorded against the same question.*
- [x] Send an empty/whitespace message → same nudge, no advance. *Automated:
      `TestNonTextInput`; the live nudge path was observed.*
- [x] After answering, the case-file reveal arrives: rich personality profile +
      the suggested animal as a bare species name (one to three words) and one
      line of in-character narration about it. *Live: reveal sent on both
      finishes; the two-field animal split verified against the real API
      (`'common magpie'`, `'beaver'`).*
- [x] Any further input after finishing → holding reply (no crash, no new
      interview). *Automated: `TestAnswerRouting`; the session is in
      `CONVERT`.*
- [x] Logs: per-turn question numbers and the finish visible; token redacted;
      no image bytes. *Observed in `.tmp/bot.log` across full live runs.*

### Live observations / accepted limitations

- **Fixed during live validation:** the first build interviewed the user as if
  they were an animal ("packs", "prey", "watering holes"), because the persona
  prompt framed the crew as studying "a fascinating new specimen". The persona
  now pins the subject as a human being and forbids animal-life questions; the
  animal comparison is drawn later from the transcript (regression test:
  `test_prompt_interviews_the_person_not_an_animal`).
- **Fixed during live validation:** the photo reply could hang for ~38s with no
  feedback (one `getFile` call stalled for 38s and every request shared the 40s
  long-poll read timeout). Now a hold-line is sent the instant a photo arrives
  and non-poll calls use a 15s timeout with a single retry for idempotent GETs.
- **Fixed during live validation:** a message the user had queued before **Q1**
  was sent was recorded as the answer 0.4s later. Answers are now accepted only
  if they are timestamped after the pending question (small clock-slack margin).
- **Fixed during live validation:** `suggested_animal` came back as a whole
  descriptive sentence rather than a bare species name — live output at
  11:02 was *"A common pigeon, constantly pecking at bright objects while
  navigating the concrete maze of a city."* (an earlier run produced *"a
  particularly stubborn rock hyrax that has decided to ignore the camera crew
  entirely."*). Rather than throw the flavour away, the output was split in
  two: `suggested_animal` must now be a bare species name (reinforced by a
  `description` on the schema field — prose rules alone were not enough) and
  a new `animal_description` carries exactly one short sentence of
  narration. The reveal shows both; Phase 4's image prompt gets the bare
  name. Regression tests cover the non-empty contract for all three fields,
  the schema descriptions, and the ceiling path that rebuilds no typed
  object (`test_seven_answers_force_distil_instead_of_next_step`).
- **Amended in Phase 5 (user request): the interview now covers the material the
  narration is built from.** The live Phase 5 run produced a bland narration
  ("a paragraph describing normal animal behaviour"), and the root cause was the
  dossier, not the Scripter: with short everyday answers the interview had never
  asked about the nightly routine or food, and `PROFILE_RULES` asked for "rich
  human personality", which invites abstractions (*"enjoys snacking while
  watching films"*) instead of quotable specifics (*"dry cereal, eaten standing
  up, in the dark"*). The question rules now require three areas to be covered
  before finishing — the nightly routine / how they get to sleep, what and when
  they eat or snack, and one repeated odd habit or ritual — finishing is gated on
  that coverage, and the profile field (schema description + prose rule) demands
  concrete, quotable behaviour. Regression tests:
  `test_prompt_demands_the_material_that_makes_the_narration_funny`,
  `test_finish_is_gated_on_the_coverage_not_only_the_count`, and the profile
  assertions in `test_returns_a_typed_profile_and_animal`. Note the trade-off,
  accepted with the user: the questions are now noticeably more systematic and
  in-character florid, and they can hint at the material ("the secret item you
  scavenge from the pantry").
- A leading-slash message mid-interview is treated as an ordinary answer (full
  command handling is Phase 7).
- Restarting the bot clears in-memory sessions (per MISSION, no persistence).
- Dev-box note (not product behaviour): the 1 GB sandbox can OOM-reap the
  background bot while test/hook runs hog memory; `.tmp/run_bot.sh` (untracked)
  is a restart guard used only for live validation.

## Spec sync (on user approval)

- [x] `SPECS/ROADMAP.md` Phase 3 row marked complete.
- [x] README status section updated to Phase 3.
- [x] Any implementation-vs-spec drift surfaced to the user and resolved.
      *The animal output drifted from "bare species name" twice; the user
      chose to keep both a bare name and the flavour line, so the contract
      gained `animal_description`.*
