# Validation — The Scripter (Phase 5)

Every box is evidence, not intent: the command or log line that proves it goes
in the report. Automated boxes are ticked by `./scripts/test` /
`./scripts/hooks`; live boxes need a real Telegram run.

## Automated

- [ ] `Script` normalises any whitespace/line-break run into one paragraph
      (multi-line model output → single-paragraph text) and strips the ends.
- [ ] `Script` rejects empty, whitespace-only and non-string text
      (`ValidationError`), and validates a schema-shaped payload directly.
- [ ] `GeminiClient.generate` sends `systemInstruction` **only** when asked;
      without it the request body is byte-for-byte what it is today (no empty
      system instruction on the wire).
- [ ] `build_prompt` carries the dossier, the species and its description; the
      persona and the rules are asserted to state: British-documentary narrator,
      one paragraph, 60–90 words / never more than 90, no markdown, plain prose
      for the microphone, name the species, invent nothing.
- [ ] `write_script` posts to `gemini-3.1-flash-lite:generateContent` with the
      persona+rules as the system instruction, the schema as `responseSchema`,
      and returns a typed `Script`.
- [ ] Bad model output (HTTP 500, non-JSON, missing/empty `text`) raises
      `GeminiApiError`; no API key inside the message.
- [ ] A 91-word narration is still returned and logged as a warning with the
      count; ≤90 words logs no warning.
- [ ] Happy-path gateway flow: interview ends → `sendPhoto` → one `sendMessage`
      with the narration text, **in that order**; session ends in
      `Phase.SCRIPT` with `script_text` set.
- [ ] The scripter runs **exactly once** per session; after the narration a
      message (text or photo) gets `LATER_PHASE_REPLY` and no further script
      call.
- [ ] Scripter failure → `SCRIPT_FAILURE_MSG`, phase stays `SCRIPT`,
      `script_text` stays `None`, no crash and no second portrait.
- [ ] A failing portrait upload does not suppress the narration (stage
      independence), and a missing narration cannot undo the portrait.
- [ ] `SessionState.clear()` resets `script_text`.
- [ ] `./scripts/test` and `./scripts/hooks` green.

## Live (real Telegram traffic)

- [ ] Live happy path: send a photo → finish the 5–7 questions → portrait
      arrives, then the narration arrives as a plain text message.
- [ ] The narration names the same species as the portrait caption.
- [ ] Reading it aloud works: one paragraph, no markdown characters, no line
      breaks, ≤90 words (record the `scripter_script words=… chars=…` line and
      the word count of the received text).
- [ ] `script_delivered` in the log with `words`/`chars`; the startup banner
      reads `(Phase 5 - Scripter)`.
- [ ] A message sent after the narration gets the holding reply and produces
      **no** second `scripter_script` line.
- [ ] The narration arrives even when the portrait fails (only reachable live by
      an unlucky run — otherwise covered by the automated independence test).
- [ ] `systemInstruction` is accepted by the live API for this model (first use
      of the field in this repo). If the live call rejects it, fall back to
      folding the persona+rules into the prompt text like every other stage and
      record the deviation here before merging.

## Docs & spec sync

- [ ] README pipeline description mentions the narration step.
- [ ] `SPECS/ROADMAP.md`: Phase 5 gains an `Implementation:` note and its status
      row becomes Complete (after the live boxes).
- [ ] `SPECS/TECH.md` still matches (Scripter already specified; only update if
      the model or the state machine changed).
- [ ] Differences surfaced between this spec and what was actually built are
      recorded here (not silently sorted out in code).
