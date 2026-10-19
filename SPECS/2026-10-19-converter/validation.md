# Validation — The Converter (Phase 4)

Every box must be checked before merge. Automated checks run via the dev
scripts; live checks are done against the running bot with real Telegram
traffic.

## Automated (`scripts/hooks` must be green)

- [ ] `scripts/test` — all tests pass; new tests were red before the matching
      implementation existed (Red/Green evidence in the commit history).
- [ ] Shared-client change: `generate(...)` behaviour unchanged (every existing
      Bouncer/Interviewer test stays green); `generate_image(...)` sends
      `responseModalities: ["IMAGE"]`; API key never in an error message.
- [ ] Contract tests: `GeneratedImage` decodes base64 at the boundary, passes
      raw bytes through, and rejects empty data, junk base64 and non-image mime
      types.
- [ ] Prompt tests: `build_prompt` names the bare species and the dossier, asks
      for the same person's likeness, one subject, and no text/watermarks.
- [ ] Client tests: a valid response yields the decoded image; a text-only
      response, a malformed inline part and a missing part all raise the typed
      `GeminiApiError` instead of leaking a raw dict.
- [ ] Telegram tests: `sendPhoto` is multipart with `chat_id`/`caption`/`photo`,
      is never retried, and redacts the token from errors.
- [ ] Gateway tests: both finish routes (model FINISH, ceiling `distil`) send
      reveal → hold line → one captioned photo; failure paths (Gemini, Telegram,
      missing file) send the in-character line, keep `CONVERT`, and never raise;
      the delivered bytes are the stored portrait.
- [ ] Shutdown test: the Converter client is closed on both SIGINT and SIGTERM
      (no leaked connection pool).
- [ ] ruff, ruff-format, mypy (strict), gitleaks all pass (`scripts/hooks`).
- [ ] `.env` and `.tmp/` remain untracked (`git check-ignore`).

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
