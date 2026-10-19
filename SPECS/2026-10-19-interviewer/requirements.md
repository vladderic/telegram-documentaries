# Requirements — The Interviewer (Phase 3)

Roadmap phase: **3 — Interviewer**. Constitution: `SPECS/MISSION.md`,
`SPECS/TECH.md`, `SPECS/ROADMAP.md`.

## Context

Phase 2 leaves an accepted user in phase `AWAITING_INTERVIEW` with a stored
portrait. The Interviewer is the stage that turns that user into a behavioural
dossier: 5–7 sharp, sequential questions asked **one at a time**, answered one
at a time, accumulated per `chat_id`, then distilled into a rich personality
profile plus a suggested animal. That profile is the fuel for the Converter
(hybrid portrait) and the Scripter (narration).

## Confirmed user decisions

1. **Adaptive, model-generated questions.** Gemini invents each next question
   from the answers so far — no fixed question bank. One model call per turn.
2. **The model decides when to stop, within 5–7 answers.** Never fewer than 5
   answers, never more than 7. The thresholds are enforced deterministically in
   code; the model chooses within the window.
3. **The profile is shown to the user.** When the interview ends, the user gets
   an in-character "case file" reveal of the profile plus the suggested animal;
   the same typed profile is stored in session state for later stages.
4. **Strictly one question per message.** A question message contains exactly
   one question, never a batch. The next question is asked only after the user
   has answered the pending one.
5. **Non-text input mid-interview does not advance.** A sticker/photo/voice/
   document gets an in-character "words, please" nudge that repeats the pending
   question; the answer index does not move and nothing crashes.
6. **Extract a shared Gemini client.** The request/structured-output/error
   plumbing currently inside the Bouncer moves to one shared module used by
   both stages; each stage keeps only its prompt, schema, and typed parse.

## Behaviour

Phases used: `AWAITING_INTERVIEW` (accepted, interview not started),
`INTERVIEW` (a question is pending), `CONVERT` (interview finished — the next
stage's entry point; extra input gets the holding reply).

| Situation | Behaviour |
|---|---|
| Bouncer accepts a photo | Send the acceptance confirmation, then immediately ask **Q1**; phase → `INTERVIEW` |
| Text while a question is pending | Treated as the answer. Append the pair, then ask the next question — or finish if the model says so / the cap is reached |
| Text while `INTERVIEW` but no pending question (only after a failed model call) | Re-ask the next question; do **not** record the text as an answer |
| Sticker / photo / voice / document while a question is pending | In-character nudge + repeat the pending question; no state change |
| Empty / whitespace-only text | Same as non-text: nudge + repeat; no state change |
| Model says "finish" | Distil profile, send the case-file reveal, phase → `CONVERT` |
| Gemini failure / timeout | Nothing is recorded; the pending question stays pending; send an in-character retry line; the user can resend the same answer (no duplication) |

### Contracts (typed, at the boundary — TECH.md)

- `InterviewAnswer` — `question: str`, `answer: str` (both non-empty).
- `InterviewStep` — `action: ASK | FINISH` (typed `StrEnum`) with a consistency
  validator: `ASK` requires `question` and forbids profile fields; `FINISH`
  requires `profile` + `suggested_animal` and forbids `question`. A malformed
  model response can never leak through as a raw dict.
- `BehaviouralProfile` — `profile: str` (rich text, the stage's output),
  `suggested_animal: str` (a real animal species — the Converter's input).
- `InterviewState` (nested in `SessionState`) — `answers: list[InterviewAnswer]`,
  `pending_question: str | None`, `profile: BehaviouralProfile | None`.
  - **Deterministic derivation, single source of truth:** the question number a
    user is answering is `len(answers) + 1`; "finished" means `profile is not
    None`. No redundant counters that can drift.
- The pending answer is recorded **only** once the next step resolves — so a
  retry after a failure re-sends the same answer without duplicating it.

### The three model calls (all against the shared client)

1. `next_question(transcript) -> str` — used while `len(answers) < MIN` (5):
   the model must produce the next question.
2. `next_step(transcript) -> InterviewStep` — used while `MIN <= len(answers)
   < MAX` (5 or 6 answers): the model may ask again or finish.
3. `distil(transcript) -> BehaviouralProfile` — used at `len(answers) >= MAX`
   (7 answers): the cap forces the profile, no further questions.

Threshold enforcement lives in the gateway's state machine, not in the model.

### Voice, prompt guardrails, and the profile

- Tone throughout: investigative, playful, slightly eccentric — a documentary
  crew that treats a job interview like a nature expedition.
- Question rules: exactly one question per message; at most two sentences; no
  multi-part or "and also…" questions; never repeat a question already asked;
  probe behaviour and habits (what the animal choice will be built from).
- Profile rules: third person ("the subject…"), 4–7 sentences of rich
  personality traits grounded **only** in the answers given (no invented
  biography), playful case-file register; `suggested_animal` is a real animal
  species whose real-world traits genuinely echo the profile.
- The user-facing reveal is the profile text plus the suggested animal.

### Failure & logging

- Decorator-based logging (`log_async_call`) around every model call; an
  interview event log per turn: question number when a question goes out, the
  answer receipt, and the finish (profile length + animal). Never secrets,
  never raw bytes.
- Failures follow TECH.md: log loudly, degrade in-character, never raise into
  the conversation, never lose collected answers.

### Scope cuts (YAGNI)

- Full `/start` // `/restart` purge stays Phase 7. Known Phase 3 limitation
  (documented, not fixed): a leading-slash message mid-interview is treated as
  an ordinary answer.
- No dossier persistence across restarts (in-memory only, per MISSION).
- No re-interview / editing a previous answer.
- No Converter behaviour — finishing only advances the phase to `CONVERT`.

## Success criteria (maps to ROADMAP Phase 3 acceptance)

- A full interview runs from acceptance to profile: 5–7 questions, strictly one
  per message, each only after the previous answer.
- Session state correctly reports the pending question number and the finished
  state, per `chat_id`, with no cross-user leakage.
- Non-text and empty input mid-interview never advances or crashes.
- The final output is a typed profile (rich text + suggested animal), sent to
  the user in character and stored for the next stage.
- `scripts/hooks` fully green; secrets never logged.
