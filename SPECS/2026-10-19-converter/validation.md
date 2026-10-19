# Validation — The Converter (Phase 4)

Every box must be checked before merge. Automated checks run via the dev
scripts; live checks are done against the running bot with real Telegram
traffic.

## Automated (`scripts/hooks` must be green)

- [x] `scripts/test` — all tests pass (231 at the phase's last green run); the
      new tests were red first (each task group was committed only after its
      Red run: e.g. 3 `TestGenerateImage` failures, 14 contract failures, 11
      converter-client failures, 7 `TestSendPhoto` failures, 10 gateway
      failures before the wiring landed).
- [x] Shared-client change: `generate(...)` behaviour unchanged (every existing
      Bouncer/Interviewer test stays green — 40 passed on the refactor commit);
      `generate_image(...)` sends `responseModalities: ["IMAGE"]`; API key
      never in an error message (`test_http_failure_is_typed_and_never_leaks_the_key`).
- [x] Contract tests: `GeneratedImage` decodes base64 at the boundary (including
      line-wrapped base64), passes raw bytes through, and rejects empty data,
      junk base64 and non-image mime types (15 tests).
- [x] Prompt tests: `build_prompt` names the bare species and the dossier, asks
      for the same person's likeness, one subject, and no text/watermarks; the
      species line never contains the description sentence.
- [x] Client tests: a valid response yields the decoded image; a text-only
      response, a malformed inline part and a missing part all raise the typed
      `GeminiApiError` instead of leaking a raw dict (`test_converter_client.py`).
- [x] Telegram tests: `sendPhoto` is multipart with `chat_id`/`caption`/`photo`,
      is never retried, and redacts the token from errors.
- [x] Gateway tests: both finish routes (model FINISH, ceiling `distil`) send
      reveal → hold line → one captioned photo; failure paths (Gemini, Telegram,
      missing file) send the in-character line, keep `CONVERT`, and never raise;
      the delivered bytes are the stored portrait.
- [x] Shutdown test: the Converter client is built once and closed in
      `run_bot`'s `finally`, which both SIGINT and SIGTERM route into
      (`test_the_converter_pool_is_closed_on_shutdown[SIGINT|SIGTERM]` drives
      the shutdown event; the handlers themselves are registered for both
      signals).
- [x] ruff, ruff-format, mypy (strict), gitleaks all pass (`scripts/hooks`).
- [x] `.env` and `.tmp/` remain untracked (`git check-ignore` → `.gitignore:5`
      and `:24`).

## Live (running bot, real Telegram client)

- [ ] Complete an interview end to end → the case-file reveal arrives, then the
      "developing" hold line, then the hybrid portrait arrives in the chat with
      a caption naming the species.
- [ ] The portrait is a genuine fusion: recognisably the person from the photo,
      visibly the suggested species, one subject, no text or watermark.
- [ ] The portrait arrives without any further action from the user (no extra
      prompt or command).
- [ ] Any message after the portrait → holding reply, no crash, no new run.
- [ ] Force a failure (temporarily point the converter at a dead endpoint) →
      the in-character failure line arrives, the bot stays alive, the log shows
      the typed error with the key redacted, and nothing is logged from the
      image bytes.
- [ ] Logs: image size and mime type logged; no base64, no secrets.

## Live observations / accepted limitations

- The generated JPEG was ~730 KB in the live recon; Telegram's `sendPhoto`
  limit is 10 MB, so no chunking is needed. Recorded rather than pre-optimised
  (no compression step) — revisit only if a real portrait approaches the limit.
- Subjective quality (how flattering/hybrid the result looks) depends on the
  model; the prompt rules are asserted, the output is not.
- Dev-box note (not product behaviour): the 1 GB sandbox runs at ~97% of its
  cgroup memory limit, so the bot can be OOM-reaped during heavy test runs; the
  supervisor restarts it and the phase is committed in small steps.
