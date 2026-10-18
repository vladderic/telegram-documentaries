# Requirements — The Bouncer (Phase 2)

Roadmap phase: **2 — Bouncer**. Constitution: `SPECS/MISSION.md`,
`SPECS/TECH.md`, `SPECS/ROADMAP.md`.

## Context

Phase 1 gave us a long-polling gateway that echoes a hardcoded reply to any
message. The Bouncer is the first real pipeline stage: a vision gate that
decides whether the uploaded photo may enter the documentary pipeline.

## Confirmed user decisions

1. **Model-written, specific rejections.** The vision model looks at the actual
   image content and crafts a tailored, in-character quip (e.g. Honda Civic →
   *"Nice ride, but I need to see YOU..."*). Guardrails: the message must state
   the real reason for rejection, stay in character, be at most two sentences,
   and never be a generic error-style line.
2. **Acceptance = solo real photo only.** Exactly one human, clearly the
   subject, and a photograph of a real person — drawings, cartoons, anime,
   illustrations and obviously synthetic/artistic renderings are rejected.
   Group shots and busy frames are rejected. (Best-effort model judgment — no
   forensic detection.)
3. **After a rejection: ready for a new photo.** The rejection clears the bad
   photo and any partial session state, but the bot stays in the awaiting-photo
   phase — the user can immediately send another photo without `/start`.

## Behaviour

Input at the awaiting-photo phase:

| User sends | Behaviour |
|---|---|
| Photo containing exactly one real human, clearly the subject | **Accepted:** store photo, advance session phase, in-character confirmation |
| Photo of anything else (car, animal, object, group shot, drawing, …) | **Rejected:** model-written quip naming what it sees and why it can't proceed; state cleared; still awaiting a photo |
| Text | Polite in-character nudge to send a photo (not a rejection quip — nothing was judged) |
| Other media (sticker, video, non-image document) | Same in-character nudge |

### Contracts (typed, at the boundary — TECH.md)

- Telegram photo download and Gemini verdict both parsed into Pydantic models;
  no raw dicts past module edges.
- Gemini returns a **structured verdict**: `accepted: bool`,
  `observed_subject: str` (what the model actually sees),
  `reason: RejectionReason` (enum: `NOT_SOLO_HUMAN`, `NOT_A_REAL_PHOTO`,
  `UNCLEAR_SUBJECT`), `message: str` (the quip). The acceptance verdict uses
  the same shape with a fixed confirmation message.
- Prompt guardrails enforce the personality rules; the enum reason guarantees
  the message can never dodge explaining *why*.

### Failure & logging

- Gemini API failure/timeout on the user's path: degrade gracefully — friendly
  in-character "try again in a moment" reply, **loud structured log**, never
  raise into the conversation (TECH.md error policy).
- Decorator-based logging around the bouncer call: log phase transitions and
  verdicts (reason + chat_id), never image bytes, never secrets.
- Download size/type limits validated at the edge; all external input treated
  as untrusted.

### Scope cuts (YAGNI)

- No multi-person disambiguation UI, no forensic AI-image detection.
- Full `/start` // `/restart` purge semantics remain Phase 7; this phase only
  clears rejected/partial state as described above.
- No Interviewer behaviour — after acceptance the phase advances to
  `AWAITING_INTERVIEW` and the bot confirms readiness (questions come in
  Phase 3).

## Success criteria (maps to ROADMAP Phase 2 acceptance)

- Human solo photo → proceeds with confirmation.
- Non-human / group / drawing photo → in-character, content-specific rejection
  that names the actual subject; state reset; immediate retry works.
- No message type at this phase ever crashes the bot.
- `scripts/hooks` fully green; secrets never logged.
