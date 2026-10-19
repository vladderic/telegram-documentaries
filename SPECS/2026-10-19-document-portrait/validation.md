# Validation — Full-resolution portraits (Phase 4b)

Every box must be checked before merge. Automated checks run via the dev
scripts; live checks need real Telegram traffic against the running bot.

## Automated (`scripts/hooks` must be green)

- [ ] Document contract: `Document` parses Telegram's document payload and
      ignores unknown fields; `Message.document` is populated.
- [ ] `portrait_source` predicates: image doc → source; non-image mime → `None`;
      missing mime → `None`; photo → source; photo + document → photo; text-only
      and caption-only → `None`.
- [ ] Storage: the extension follows the media type (jpg/png/heic) and the
      stored bytes are byte-identical to the download.
- [ ] The session records `photo_mime_type`, and `_deliver_portrait` sends the
      recorded type — a PNG portrait reaches Gemini as `image/png`.
- [ ] Routing equivalence: the same test body (parametrised) passes for a photo
      and for an image document — hold line, exactly one Bouncer call, interview
      opens.
- [ ] Guards: PDF nudged; oversize document refused with **no** `getFile` call;
      mid-interview document nudges and does not answer the pending question;
      post-portrait document gets the holding reply.
- [ ] The Bouncer declares the true media type on the vision call.
- [ ] `grep` shows no remaining hard-coded `portrait.jpg`, `PORTRAIT_MIME_TYPE`
      or "every portrait is a JPEG" assumption.
- [ ] `./scripts/test` all green, and the new tests were red first (counts
      recorded here).

## Live (real Telegram traffic)

- [ ] Portrait sent **as a file** → hold line → verdict → interview opens; the
      log shows the document path and the stored file is `portrait.<ext>`.
- [ ] Interview finished → portrait generated; the log shows `converter_fuse`
      with the document's media type and a **larger** stored source than the
      compressed-photo run (`portrait_bytes` comparison recorded).
- [ ] A PDF gets the photo nudge; an oversize image file gets the too-large line
      with no `getFile` in the log.
- [ ] A normal photo behaves exactly as before (regression check).
- [ ] Quality note recorded honestly: how much did the full-resolution source
      actually help the fusion? (The model's ceiling remains.)
- [ ] Docs updated: README, ROADMAP (Phase 4b), this file, and `TECH.md` if the
      session contract changed.
