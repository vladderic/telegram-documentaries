# Validation — The Scripter (Phase 5)

Every box is evidence, not intent: the command or log line that proves it goes
in the report. Automated boxes are ticked by `./scripts/test` /
`./scripts/hooks`; live boxes need a real Telegram run.

Evidence base: `./scripts/hooks` green (ruff, ruff-format, mypy, pytest,
gitleaks) with **300 unit tests passing** (253 before Phase 5, +47 new).
Commits: TG1 `b3c2823` (contract), TG2 `c318123` (`systemInstruction`), TG3
`6994a5c` (`scripter.py`), TG4 `41a7849` (gateway wiring).

## Automated

- [x] `Script` normalises any whitespace/line-break run into one paragraph
      (multi-line model output → single-paragraph text) and strips the ends.
      → `tests/unit/test_script_contracts.py`.
- [x] `Script` rejects empty, whitespace-only and non-string text
      (`ValidationError`), and validates a schema-shaped payload directly.
      → `tests/unit/test_script_contracts.py`.
- [x] `GeminiClient.generate` sends `systemInstruction` **only** when asked;
      without it the request body is byte-for-byte what it is today (no empty
      system instruction on the wire). → `TestSystemInstruction` in
      `tests/unit/test_gemini_client.py`.
- [x] `build_prompt` carries the dossier, the species and its description; the
      persona and the rules are asserted to state: British-documentary narrator,
      one paragraph, 60–90 words / never more than 90, no markdown, plain prose
      for the microphone, name the species, invent nothing.
      → `tests/unit/test_scripter_client.py`.
- [x] `write_script` posts to `gemini-3.1-flash-lite:generateContent` with the
      persona+rules as the system instruction, the schema as `responseSchema`,
      and returns a typed `Script`. → `tests/unit/test_scripter_client.py`
      (request body asserted against `httpx.MockTransport`).
- [x] Bad model output (HTTP 500, non-JSON, missing/empty `text`) raises
      `GeminiApiError`; no API key inside the message.
      → `tests/unit/test_scripter_client.py`.
- [x] A 91-word narration is still returned and logged as a warning with the
      count; ≤90 words logs no warning. → `tests/unit/test_scripter_client.py`
      (`scripter_over_word_limit`).
- [x] Happy-path gateway flow: interview ends → `sendPhoto` → one `sendMessage`
      with the narration text, **in that order**; session ends in
      `Phase.SCRIPT` with `script_text` set. → `tests/unit/test_scripter_gateway.py`
      (shared call log `["text", "text", "photo", "script", "text"]`).
- [x] The scripter runs **exactly once** per session; after the narration a
      message (text or photo) gets `LATER_PHASE_REPLY` and no further script
      call. → `TestOneNarrationPerSession` in `tests/unit/test_scripter_gateway.py`.
- [x] Scripter failure → `SCRIPT_FAILURE_MSG`, phase stays `SCRIPT`,
      `script_text` stays `None`, no crash and no second portrait.
      → `TestWriteScript` in `tests/unit/test_scripter_gateway.py`.
- [x] A failing portrait upload does not suppress the narration (stage
      independence), and a missing narration cannot undo the portrait.
      → `TestTheNarrationSurvivesTheDarkroom` in
      `tests/unit/test_scripter_gateway.py`; the converter/interview harnesses
      now assert the narration in their finish-path tails too.
- [x] `SessionState.clear()` resets `script_text`.
      → `TestStoredScript` in `tests/unit/test_session_state.py`.
- [x] `./scripts/test` and `./scripts/hooks` green.
      → 300 passed; all five hooks pass.

## Live (real Telegram traffic)

- [ ] Live happy path: send a photo → finish the 5–7 questions → portrait
      arrives, then the narration arrives as a plain text message.
- [ ] The narration names the same species as the portrait caption.
- [ ] Reading it aloud works: one paragraph, no markdown characters, no line
      breaks, ≤90 words (record the `scripter_script words=… chars=…` line and
      the word count of the received text).
- [ ] `script_delivered` in the log with `words`/`chars`.
- [x] The gateway banner reads `(Phase 5 - Scripter)`.
      → Bot restarted on this branch: `2026-10-19 15:09:15,505 INFO
      telegram_documentaries.bot_gateway: Starting Telegram gateway
      (Phase 5 - Scripter)`, then polling `getUpdates` cleanly.
- [x] `systemInstruction` is accepted by the live API.
      → Direct `ScripterClient.write_script` call against the real API with a
      canned dossier (no Telegram hop): 200 response, **83 words**, one
      paragraph, no newlines, species named ("The Eurasian magpie remains a
      creature of insatiable desire…"), voice on-model. No fallback needed;
      the persona+rules stay in the system instruction.
- [ ] One narration per session live: after the narration, any message gets
      `LATER_PHASE_REPLY` and no second `scripter_script` line.

## Docs

- [x] README updated: status block (Phase 5), pipeline step 6, the "run it"
      step 4, `GEMINI_API_KEY` description, repository layout (`scripter.py`).
- [x] `SPECS/ROADMAP.md` Phase 5 has an `Implementation:` note.
- [x] `SPECS/TECH.md` needs no change — it already names Gemini 3.1 Flash Lite
      for the Scripter and `… → CONVERT → SCRIPT → …` in the state machine;
      neither the model nor the state machine changed.
- [x] Differences between this spec and what was built are recorded (below).

## Differences recorded (not silently sorted out in code)

1. **No markdown stripper.** The persona and rules forbid markdown and the
   over-length warning is the only safety net; the words are never rewritten.
   User decision at spec time.
2. **No second hold line before the narration.** The portrait's darkroom line
   covers the wait and the text call is fast; a second "one moment" would read
   as stalling. User decision at spec time.
3. **The stage is claimed before the model call.** `_write_script` sets
   `Phase.SCRIPT` *before* calling the model (the spec only required the phase
   to stay `SCRIPT` on failure). This makes a failure strictly unable to re-open
   the pipeline; `test_marks_the_stage_sends_plain_text_and_keeps_it` and
   `test_a_failed_script_degrades_and_never_runs_twice` lock it in.
4. **Supersedes Phase 4's "stays in `CONVERT`".** Phase 4's spec
   (`SPECS/2026-10-19-converter/requirements.md`, "session stays `CONVERT`" /
   "Phase 5 appends the next stage") described the end state as `CONVERT`; the
   user decision for Phase 5 was that the narration runs automatically after the
   portrait, so the session now ends in `SCRIPT`. That is what keeps "one run per
   session" true (a late photo or text gets `LATER_PHASE_REPLY`, not a second
   portrait). This holds on the failure branch too: a jammed darkroom leaves the
   session in `SCRIPT`, not `CONVERT`. Phase 4's validation note is left as
   written — it describes the phase-4 slice, not the pipeline end state.
5. **`test_converter_gateway.py` / `test_interview_gateway.py` were updated**
   (a `FakeScripter`, the new tail assertions, and the renamed
   `test_converter_failure_still_narrates_and_holds_afterwards`): the finish
   path now ends after the narration, so `texts[-1]` is the narration, not the
   hold line.
6. **ROADMAP status row reads "Implementation complete — live validation
   pending"**, not "Complete": every live box above is still unticked and
   Phase 5's own doc box gates `Complete` on a real Telegram run.
7. **Voice fix after the first live run (user finding).** The first live
   narration (15:38, `common raccoon`, 81 words) read as generic animal
   behaviour rather than the subject's quirks. RCA, all three checks run live
   against the real API:
   - *Reproduction:* a **quirk-rich** dossier produced a properly personal
     paragraph (2 a.m. dry cereal, a nest of socks, wrappers under the sofa,
     nail-biting, four repeated notes); a **thin** dossier of the same length
     produced generic raccoon biology ("emerges under the cover of darkness",
     "nightly labor", "solitary rituals").
   - *Isolation:* hardening the Scripter rules alone improved the thin case
     (no invented species facts) but could not make it specific — there was no
     concrete material to use. The root cause was upstream: the interview had
     never asked about bedtime or food, and the profile rule asked for "rich
     human personality", which invites abstractions.
   - *Fix (both stages, user decision):* the Scripter persona/rules now pin the
     narration to the subject's own documented habits and forbid species natural
     history (`never state general facts about the species`), prefer the
     concrete detail over the abstract trait, and the user turn leads with the
     dossier while demoting the species note to flavour. The interviewer now
     covers nightly routine / food and snacking / one odd ritual, gates
     finishing on that coverage, and demands concrete, quotable behaviour in the
     profile (prose rule **and** schema description).
   - *Post-fix live check (scripted interview, mundane answers):* the questions
     harvested the material ("what specific ritual do you perform…",
     "the secret item you scavenge from your pantry", "a singular, repetitive
     physical tic"), the profile became concrete ("obsessively reboiling the
     kettle three times in succession without ever brewing a drink") and the
     narration anchored on it ("the repetitive ritual of reboiling the kettle
     three times in succession, never once brewing a drink") with no species
     biology at all.
   - Regression tests: `tests/unit/test_scripter_client.py` (`TestVoice`,
     `test_leads_with_the_dossier_and_demotes_the_species_note`) and
     `tests/unit/test_interviewer_client.py` (coverage, finish gate, profile
     concreteness). Suite: 306 passing.
   - Trade-off recorded with the user: the interview questions are now more
     systematic and can hint at the material ("the secret item you scavenge from
     the pantry"). The live Telegram boxes above still need a fresh run on this
     build.
