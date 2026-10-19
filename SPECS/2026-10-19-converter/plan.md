# Plan — The Converter (Phase 4)

Red/Green TDD throughout: every task below starts with failing tests and runs
the dev scripts described in the README (`scripts/test`, `scripts/hooks`).

Dev-box note: the sandbox sits at ~97% of its 1 GB cgroup limit, so commands are
kept small (targeted `pytest` files during the loop; one full `scripts/hooks`
run per task group, not per edit). This is environment policy, not product code.

## Task group 1 — Shared Gemini client learns images

1. Red tests: `generate_image(contents)` posts
   `generationConfig.responseModalities == ["IMAGE"]`, returns the raw response
   parts, and raises the typed `GeminiApiError` (with the API key redacted) on
   transport/HTTP failure — mirroring the existing structured-output tests.
2. Refactor `GeminiClient` so the HTTP call, auth header, error handling and
   sanitising live in one private `_call(payload)`; `generate(...)` keeps its
   current behaviour (existing Bouncer/Interviewer tests must stay green), and
   `generate_image(contents)` adds the image request.

## Task group 2 — `GeneratedImage` contract

3. Red tests for `GeneratedImage`:
   - accepts a base64 `str` and decodes it to non-empty `bytes`;
   - accepts raw `bytes` unchanged;
   - rejects empty data, invalid base64, and non-image mime types;
   - ignores unknown fields.
4. Implement the model in `models.py` (the wire form is decoded at the
   boundary, so nothing downstream sees base64).

## Task group 3 — `converter.py` (prompt + client)

5. Red tests for `build_prompt(profile)`: the prompt names the bare species, the
   dossier text, likeness preservation ("same person"), single subject, and the
   no-text/no-watermark rule; it never asks for the species description as a
   species. Assert the prompt text, not just that a string came back.
6. Red tests for `ConverterClient.fuse(...)` against a mocked transport:
   - happy path → `GeneratedImage` with the mime type and decoded bytes from the
     inline part;
   - a response whose parts hold text but no image → `GeminiApiError`;
   - a malformed inline part (missing/!str data, junk base64) → `GeminiApiError`;
   - the request body carries both the prompt text and the photo inline data.
7. Implement `converter.py`: `CONVERTER_MODEL = "gemini-3.1-flash-image"`,
   persona/rules constants, `build_prompt`, `ConverterClient` (wrapping the
   shared client, `close()` included, generous timeout for image latency).

## Task group 4 — Telegram photo delivery

8. Red tests for `TelegramClient.send_photo`: the request is multipart with
   `chat_id`, `caption` and a `photo` file part whose filename follows the mime
   type; the method is **never retried**; an HTTP error raises
   `TelegramApiError` with the token redacted.
9. Implement `send_photo`.

## Task group 5 — Gateway wiring

10. Red tests (`FakeConverter` + the existing gateway harness):
    - both finish paths (model FINISH and the ceiling `distil`) send the reveal,
      then the hold line, then exactly one photo with a caption naming the
      species;
    - a Gemini failure, a Telegram failure and a missing/unreadable file each
      produce the in-character failure line, leave the session in `CONVERT`, and
      never raise;
    - the photo bytes sent are the stored portrait (byte-for-byte);
    - post-portrait input still gets the holding reply.
11. Implement: `PORTRAIT_HOLD_MSG`, `PORTRAIT_FAILURE_MSG`,
    `PORTRAIT_CAPTION_TEMPLATE`, `_deliver_portrait(...)`, the hook in
    `_finish_interview(...)`, and thread the `converter` through
    `_handle_answer` / `_handle_interview` / `_handle_photo` / `_dispatch_update`
    / `run_bot` (constructed once, closed on shutdown like the Interviewer).

## Task group 6 — Category sweep: "stage not wired into every path"

12. Sweep for siblings of the wiring risk found in Phase 3 (the ceiling path
    silently dropping a new field):
    - every route that ends the interview reaches the Converter;
    - the new client is created exactly once and closed on shutdown (no leak on
      SIGTERM/SIGINT);
    - no branch returns before conversion without logging why;
    - the dispatch branches agree with `Phase` (no dead or unreachable branch).
    Fix every instance found and lock the sweep in with a test.

## Task group 7 — Docs, gates, PR

13. Update `README.md` (status, run flow, layout), `SPECS/ROADMAP.md` (Phase 4
    row + acceptance notes) and this spec folder's `validation.md` with what was
    actually built.
14. Run `scripts/hooks` green; commit and push; open a PR against `main`
    (base `main`, head the new branch) and report the URL — the user merges.
