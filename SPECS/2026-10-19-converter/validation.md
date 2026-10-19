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

- [x] Complete an interview end to end → the case-file reveal arrives, then the
      "developing" hold line, then the hybrid portrait arrives in the chat with
      a caption naming the species. **Verified 2026-10-19 12:19** (chat 555,
      5 answers, `eastern gray squirrel`): `interview_step action=FINISH
      in_hand=5` → `phase_transition INTERVIEW -> CONVERT` → reveal → hold →
      `converter_fuse portrait_bytes=150646` → `gemini-3.1-flash-image` 200 OK
      after 7.8s → `portrait_delivered`.
- [x] The portrait is a genuine fusion: visibly the suggested species, one
      subject, no text or watermark. **User verdict ("it does work", but "a very
      poor photoshop job")**: the hybrid is real and the species reads clearly;
      the likeness of the person is the weak part. Quality recorded as an
      accepted limitation below rather than a blocker - the ROADMAP acceptance
      for this phase is "user receives an image" plus graceful failure.
- [x] The portrait arrives without any further action from the user (no extra
      prompt or command) - the log shows the reveal, hold line, image call and
      upload back-to-back with no inbound update in between.
- [ ] Any message after the portrait → holding reply, no crash, no new run.
- [x] Force a failure (point the shared client's base URL at a dead host from
      a scratch script that calls the real `_deliver_portrait` with the real
      `TelegramClient`) → the hold line and the in-character failure line both
      arrived in the chat, the call returned without raising, the session stayed
      in `CONVERT`, and the log showed `GeminiApiError: ... (ConnectError):
      [Errno -2] Name or service not known` with no bytes and no key.
- [x] Logs: `converter_fuse portrait_bytes=150646` and
      `converter_image mime_type=image/jpeg bytes=<size>`; no base64 and no
      secrets anywhere in the log (`grep` over the whole file: 0 errors, token
      always `[REDACTED]`).

- [ ] Any message after the portrait → holding reply, no crash, no new run.
      (Automated coverage exists - `test_a_photo_after_the_portrait_gets_the_holding_reply`
      covers a photo too - but the live confirmation still needs one message
      sent after the portrait.)

## Live observations / accepted limitations

- The generated JPEG was ~730 KB in the live recon; Telegram's `sendPhoto`
  limit is 10 MB, so no chunking is needed. Recorded rather than pre-optimised
  (no compression step) — revisit only if a real portrait approaches the limit.
- Subjective quality (how flattering/hybrid the result looks) depends on the
  model; the prompt rules are asserted, the output is not. Live verdict
  2026-10-19: functional but crude ("a very poor photoshop job"). Two suspects,
  both outside this phase's scope: (a) the prompt rules let the animal features
  dominate the face - a candidate for a follow-up prompt experiment; (b) the
  source is Telegram's *compressed* photo (the largest `PhotoSize` was
  ~150 KB), and a photo sent as a **document** would preserve detail but is
  currently nudged away by the Bouncer (no `document` support). Recorded as
  findings, not fixed here.
- Dev-box note (not product behaviour): the 1 GB sandbox runs at ~97% of its
  cgroup memory limit, so the bot can be OOM-reaped during heavy test runs; the
  supervisor restarts it and the phase is committed in small steps.
