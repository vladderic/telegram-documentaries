# Requirements — The Scripter (Phase 5)

## Goal

Give every finished subject a **narration**: one paragraph of reverent, dramatic
British wildlife-documentary voice, built from the dossier, describing the
subject's human quirks as if they were animal behaviour — and send it straight
to the Telegram chat, plain and ready for voice synthesis.

The portrait is the *reveal*; the narration is the payoff that makes the joke
land ("a nature documentary about a person").

## Context

- The Interviewer already produces the input: the typed `BehaviouralProfile`
  (`profile`, `suggested_animal`, `animal_description`), stored on the session
  when the interview closes (`_finish_interview`).
- The state machine already has the stage: `models.Phase` carries `SCRIPT` and
  `NARRATE`, and TECH.md fixes the order
  `… → CONVERT → SCRIPT → NARRATE → DONE`. Phase 5 fills `SCRIPT`; Phase 6
  (TTS) will take `SCRIPT → NARRATE` and consume exactly this text.
- Today the pipeline simply stops after the portrait: every later message gets
  the fixed holding line (`LATER_PHASE_REPLY`, "the crew is still setting up
  this scene"). Phase 5 replaces that dead end with the documentary voice.
- The portrait's caption already names the cast species
  (`PORTRAIT_CAPTION_TEMPLATE`), so the narration naming the same species ties
  the words to the image the user just received.
- The shared `GeminiClient.generate` has no `systemInstruction` support: every
  stage so far folds its persona into the prompt text. This stage's
  instruction is explicitly a **system instruction**, and the API has a field
  for exactly that — so it gets one (a small, additive keyword-only parameter).

## Decisions (user, 2026-10-19)

1. **Automatic, right after the portrait**: portrait first, then the narration
   as its own text message. No user request, no command. One run per session.
2. **Name the animal**: the narration names the species the subject was cast as
   (the same species the portrait caption named), tying words to image.
3. **Normalise + warn, still send**: the prompt asks for one paragraph of
   ≤90 words; the code normalises whitespace/line breaks into a single
   paragraph and logs a warning when the text still exceeds 90 words — but the
   narration is always sent. A word count is never a reason to lose the script.
4. **Store the text on the session** (`SessionState.script_text`): Phase 6 must
   synthesise audio from the very words the user read. No second model call.

## Scope

| Piece | What it does |
|---|---|
| `scripter.py` | Persona, rules, prompt builder, response schema, `ScripterClient.write_script` → typed `Script` |
| `Script` contract | Pydantic model; **guarantees one paragraph** (whitespace normalised at the boundary) |
| `GeminiClient.generate` | Additive `system_instruction` parameter (persona/rules travel as the system instruction) |
| `SessionState.script_text` | Holds the delivered narration for Phase 6; reset by `clear()` |
| Gateway | After the portrait attempt: write the script, send it as plain text, move the session to `Phase.SCRIPT` |
| Docs | README pipeline, ROADMAP Phase 5 row + status table, `validation.md` evidence |

## Contracts

- `Script(BaseModel)`: `text: str`, validated non-empty and **normalised to a
  single paragraph** (every whitespace run, including newlines, collapses to
  one space; the ends are stripped). The model is the guarantee: no consumer
  ever sees a multi-line or double-spaced narration.
- `SCRIPT_SCHEMA`: `{"type": "object", "properties": {"text": {"type": "string",
  "description": …}}, "required": ["text"]}` — the field name matches the model
  field, so the payload validates directly (the Interviewer/Converter
  precedent: `Script.model_validate(payload)`).
- `build_prompt(profile: BehaviouralProfile) -> str` — pure and testable; the
  **user turn carries only the dossier facts** (the dossier text, the species
  and its one-line description), because the persona and the rules are the
  system instruction.
- `SCRIPTER_PERSONA` — the narrator the user asked for: a revered, dramatic
  British wildlife-documentary narrator, treating the human subject as the
  creature under study.
- `SCRIPT_RULES` — one paragraph; 60–90 words, **never more than 90**; plain
  prose for the microphone: no markdown, no lists or headings, no line breaks,
  no quotation marks, no speaker labels, no stage directions, no sound effects;
  name the species; ground every claim in the dossier (invent nothing);
  address the subject as the creature it was cast as, never as a human
  presenter or a list of facts.
- `ScripterClient(api_key, model=SCRIPTER_MODEL, timeout=30.0, transport=None)`
  with `async def write_script(self, profile: BehaviouralProfile) -> Script`.
- `SCRIPTER_MODEL = "gemini-3.1-flash-lite"` (constitution, TECH.md).
- `MAX_WORDS = 90` — the cap the warning is measured against; the prompt also
  states the 60–90 band from the ROADMAP.
- `SessionState.script_text: str | None = None` — the delivered narration
  (Phase 6's input); `clear()` resets it with the rest of the session.

## Gateway behaviour

1. `_finish_interview` stores the dossier and runs the Converter exactly as
   today.
2. Then, **regardless of whether the portrait succeeded** — the two stages are
   independent: a jammed darkroom must not silence the narrator, and a missing
   script must not hide a delivered portrait — the gateway writes the script.
3. Phase transition `CONVERT → SCRIPT` is logged (`log_phase_transition`);
   `session.script_text` is set once the text is in hand and sent.
4. The narration is sent as a **plain text message** (`sendMessage`, no
   `parse_mode`): with no formatting requested, Telegram renders the words
   exactly as written — which is what voice synthesis needs.
5. After that the session rests in `Phase.SCRIPT`. Every stage entry guard
   already refuses later phases (portrait intake needs `AWAITING_PHOTO`,
   interview needs `INTERVIEW_PHASES`), so **the pipeline cannot re-run**: any
   later message gets `LATER_PHASE_REPLY` and no second narration is written
   (asserted by call count, not by hope).
6. Phase 6 will move `SCRIPT → NARRATE` and synthesise audio from
   `session.script_text`; this spec stops at the text.

## Failure behaviour (fail loud, degrade in character)

| Failure | Behaviour |
|---|---|
| Gemini error / unreadable payload / empty text | `SCRIPT_FAILURE_MSG` in character, logged with `logger.exception`; the phase **stays** `SCRIPT` so no second run starts; the portrait already delivered stands |
| Telegram send failure | logged, no crash, no retry (a retried send could post the narration twice) |
| Narration over 90 words | `logger.warning` with the count, then sent as-is (decision 3) |
| Portrait failure (Converter) | the narration is still written and sent (independence) |
| Malformed model payload | typed `GeminiApiError` at the boundary — never a raw dict, never a half-valid `Script` |

## Out of scope (YAGNI)

- TTS/audio, voice selection, audio formats (Phase 6).
- Regenerating, retrying, editing or re-requesting a narration; a `/script`
  command; letting the user influence the narration.
- Stripping the model's words beyond whitespace normalisation (a general
  markdown stripper is arbitrary; the prompt is the lever, the warning is the
  safety net).
- `/start` and `/restart` purge (Phase 7), state persistence (MISSION: memory
  only).
- Refactoring the other stages onto `system_instruction` (their prompts work
  and are live-validated; changing them is not this phase's job).
