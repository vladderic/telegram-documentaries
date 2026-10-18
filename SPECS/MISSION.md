# MISSION — The Telegram Documentaries

## Vision

A Telegram bot that turns a user's portrait photo into a narrated, comedy wildlife
documentary about them. The user uploads a photo, answers a short on-camera-style
interview, and receives back a hybrid human-animal portrait plus a dramatic
British-documentary voiceover — all inside Telegram, in one conversation.

## User experience (end to end)

1. User opens a chat with the bot and sends `/start`.
2. User uploads a portrait photo.
3. **Bouncer** checks the photo: a human is present → conversation proceeds;
   otherwise the bot cheekily rejects it and resets.
4. **Interviewer** asks 5–7 questions, one at a time, gathering a behavioural
   dossier and a suggested animal.
5. **Converter** merges the original photo + dossier into a hybrid animal portrait
   and sends it back.
6. **Scripter** writes a one-paragraph (~60–90 word) dramatic documentary
   narration from the dossier.
7. **Narrator** synthesises the script as a Telegram voice note (TTS).
8. User receives: their hybrid portrait image + narrated audio note.
9. `/start` or `/restart` at any point purges all session state and temp media,
   and the conversation begins fresh.

## In scope

- The five pipeline stages above (Bouncer → Interviewer → Converter → Scripter → Narrator).
- Telegram long-polling transport (no webhooks, no public URL).
- Per-`chat_id` in-memory session state with `/start` and `/restart` reset.
- Graceful handling of out-of-order or unexpected message types at each stage.

## Out of scope (YAGNI)

- Webhooks or any publicly reachable server.
- Persistent database or user accounts — state lives in memory only.
- Video output — the result is a still image + audio note, never a video file.
- Multi-turn re-interviews or editing a previous dossier; one run = one documentary.
- Group-chat behaviour beyond basic handling; the product is a 1:1 bot experience.

## Success criteria

- **Happy path:** photo in → hybrid portrait + voice note out, with no manual
  intervention and no crashes.
- **Reset:** `/restart` and `/start` fully purge state and temp media; a new
  photo starts a clean run, even after a previous partial or failed run.
- **Out-of-order input:** sending text when a photo is expected (or a photo when
  text is expected) never crashes the bot; it responds gracefully and continues
  or resets appropriately.

## Non-negotiables

- Never leak one user's session data to another user.
- Never hardcode or commit secrets — `TELEGRAM_BOT_TOKEN` and `GEMINI_API_KEY`
  live in `.env` only.
