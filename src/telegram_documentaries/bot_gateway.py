"""Phase 1-3 Telegram gateway (entry point).

Responsibilities:
- load secrets from ``.env`` (never hardcode, never log them),
- long-poll Telegram (``getUpdates``) and route each update to the current
  pipeline stage (Phase 2: the Bouncer vision gate; Phase 3: the Interviewer),
- fail loudly with a clear log when configuration is missing,
- shut down gracefully on Ctrl+C / SIGTERM, even mid-poll.

Errors on the user's conversation path are caught and logged loudly instead of
raised into the loop; nothing is ever swallowed unlogged (TECH.md).
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from typing import Any

import httpx

from telegram_documentaries import bouncer
from telegram_documentaries.bouncer import (
    GeminiVisionClient,
    PhotoValidationError,
)
from telegram_documentaries.config import (
    REDACTED,
    ConfigError,
    configure_logging,
    install_secret_redaction,
    load_settings,
)
from telegram_documentaries.gemini import GeminiApiError
from telegram_documentaries.interviewer import (
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    InterviewerClient,
)
from telegram_documentaries.logging_utils import log_phase_transition
from telegram_documentaries.models import (
    InterviewAction,
    InterviewAnswer,
    InterviewStep,
    Message,
    Phase,
    SessionState,
    Update,
    UpdateResult,
)

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

#: Where accepted portraits are stored (gitignored; keyed per chat_id).
DEFAULT_MEDIA_ROOT = ".tmp/media"

#: Phases owned by the Interviewer (Phase 3): text is an answer, anything else
#: (or nothing at all) only nudges.
INTERVIEW_PHASES = frozenset({Phase.AWAITING_INTERVIEW, Phase.INTERVIEW})

#: Fixed in-character replies for the awaiting-photo phase (rejections are
#: model-written by the Bouncer; these cover input that was never judged).
PHOTO_NUDGE = (
    "A pleasure to hear from you - but I judge faces, not words. "
    "Send me a photograph and we will get you on camera."
)
#: Input that arrives before the stage behind us exists (Converter onwards).
LATER_PHASE_REPLY = (
    "One moment - the crew is still setting up this scene. Give me a beat to catch up."
)
#: The vision gate failed (API down/timeout): degrade, do not crash.
BOUNCER_RETRY_MSG = (
    "Apologies - the studio's vision apparatus just coughed up a hairball. "
    "Send that photo again in a moment."
)
#: Edge validation refused the upload (too large).
PHOTO_TOO_LARGE_MSG = (
    "That frame is a touch too large for my lens. Send a smaller photograph, please."
)
#: Non-text (or empty) input mid-interview: nudge and repeat the question.
INTERVIEW_NUDGE = (
    "The recorder only takes dictation - words, not stickers or silence. "
    "I will ask again:"
)
#: The Interviewer's model call failed (API down/timeout): nothing was
#: recorded, so the same answer can simply be resent.
INTERVIEW_RETRY_MSG = (
    "Hold that thought - my notebook just snapped shut. Say that again, "
    "and I will catch it all down."
)
#: Case-file reveal: the profile text plus the suggested animal.
PROFILE_REVEAL_TEMPLATE = (
    "The dossier is closed - and the crew has seen enough of you.\n\n"
    "{profile}\n\n"
    "Suggested casting: {animal}"
)
#: Catch-all after an unexpected failure on the conversation path.
DISPATCH_FAILURE_MSG = (
    "Something went sideways backstage and that take was lost - do try once more."
)


class TelegramApiError(Exception):
    """A Telegram API call failed. The message never contains the bot token."""


class TelegramClient:
    """Minimal async Telegram Bot API client for long polling."""

    def __init__(
        self,
        token: str,
        base_url: str = TELEGRAM_API_BASE,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._offset: int | None = None

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()

    def _sanitize(self, text: str) -> str:
        """Redact the bot token from any text destined for logs/exceptions."""
        return text.replace(self.token, REDACTED)

    async def _request(
        self,
        method: str,
        *,
        params: dict[str, str | int] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Call one Bot API method; failures raise a token-free TelegramApiError."""
        url = f"{self.base_url}/bot{self.token}/{method}"
        try:
            if payload is not None:
                response = await self._client.post(url, json=payload)
            else:
                response = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            detail = self._sanitize(str(exc))
            # `from None`: chaining would re-print the original exception - whose
            # message can embed the request URL (bot token!) - into log output.
            raise TelegramApiError(
                f"Telegram API {method} failed ({type(exc).__name__}): {detail}"
            ) from None
        if response.is_error:
            body = self._sanitize(response.text[:300])
            raise TelegramApiError(
                f"Telegram API {method} returned HTTP {response.status_code}: {body}"
            ) from None
        return response.json()

    async def get_updates(self, poll_timeout: int = 30) -> list[Update]:
        """Fetch updates via long polling (``getUpdates``).

        The response is untrusted: it is validated into typed Pydantic models
        at this boundary, and malformed payloads are rejected loudly rather
        than passed on as raw dicts (TECH.md).

        Args:
            poll_timeout: Long-poll duration the Telegram server should hold
                the request for, in seconds.

        Returns:
            The batch of validated updates (empty when nothing is pending).

        Raises:
            TelegramApiError: On transport/HTTP failures or an unexpected
                response shape, with the bot token redacted.
        """
        params: dict[str, str | int] = {"timeout": poll_timeout}
        if self._offset is not None:
            params["offset"] = self._offset

        data = await self._request("getUpdates", params=params)
        if not isinstance(data, dict) or "result" not in data:
            raise TelegramApiError(
                "Telegram API getUpdates returned an unexpected response shape"
            )
        # ValidationError below is deliberate - see the docstring.
        updates = UpdateResult.model_validate(data["result"]).root

        if updates:
            # Resume after the last update_id so nothing is processed twice.
            self._offset = updates[-1].update_id + 1
        return updates

    async def send_message(self, chat_id: int, text: str) -> Any:
        """Send a text message to a chat.

        Args:
            chat_id: Target chat ID.
            text: Message text to send.

        Returns:
            The API's JSON payload.
        """
        return await self._request(
            "sendMessage",
            payload={"chat_id": chat_id, "text": text},
        )

    async def download_file(self, file_id: str) -> bytes:
        """Download a Telegram file's raw bytes (``getFile`` + file fetch).

        Args:
            file_id: Telegram file ID (from a ``PhotoSize``, etc.).

        Returns:
            The raw file bytes (untrusted; size-checked by the caller).

        Raises:
            TelegramApiError: If ``getFile`` returns an unexpected shape or
                either HTTP call fails, with the bot token redacted.
        """
        data = await self._request("getFile", params={"file_id": file_id})
        if not isinstance(data, dict) or not isinstance(data.get("result"), dict):
            raise TelegramApiError(
                "Telegram API getFile returned an unexpected response shape"
            )
        file_path = data["result"].get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise TelegramApiError(
                "Telegram API getFile result is missing a valid file_path"
            )

        url = f"{self.base_url}/file/bot{self.token}/{file_path}"
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            detail = self._sanitize(str(exc))
            # `from None`: the original exception could embed the URL with
            # the bot token; never chain it into logs/tracebacks.
            raise TelegramApiError(
                f"Telegram file download failed ({type(exc).__name__}): {detail}"
            ) from None
        if response.is_error:
            body = self._sanitize(response.text[:300])
            raise TelegramApiError(
                f"Telegram file download returned HTTP {response.status_code}: {body}"
            ) from None
        return response.content


def build_reply(message: Message, session: SessionState) -> str | None:
    """Build the reply for non-photo input at the session's current phase.

    The interview stage (``INTERVIEW_PHASES``) never reaches this function -
    the dispatcher hands those messages to the Interviewer first.

    Args:
        message: Validated Telegram message (never a raw dict).
        session: The sender's session state (phase decides the reply).

    Returns:
        The fixed in-character reply, or ``None`` when the message is a
        photo — photos are handled by the Bouncer flow, not a plain reply.
    """
    if message.photo:
        return None
    if session.phase == Phase.AWAITING_PHOTO:
        return PHOTO_NUDGE
    return LATER_PHASE_REPLY


def _session_for(sessions: dict[int, SessionState], chat_id: int) -> SessionState:
    """Fetch or lazily create the shared session state for a chat."""
    session = sessions.get(chat_id)
    if session is None:
        session = SessionState(chat_id=chat_id)
        sessions[chat_id] = session
    return session


def _store_photo(media_root: str | Path, chat_id: int, image_bytes: bytes) -> str:
    """Persist accepted portrait bytes under ``media_root/<chat_id>/``.

    Returns:
        The path the portrait was written to (held by the session until the
        pipeline consumes it; purged on ``/restart``, Phase 7).
    """
    directory = Path(media_root) / str(chat_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "portrait.jpg"
    path.write_bytes(image_bytes)
    return str(path)


async def _send(client: TelegramClient, chat_id: int, text: str) -> bool:
    """Send one in-character message, logging (never raising) on failure.

    A send failure must not lose the interview: callers only record the
    question they sent once this returns ``True`` (TECH.md error policy).
    """
    try:
        await client.send_message(chat_id=chat_id, text=text)
    except Exception:
        logger.exception("failed to send message chat_id=%s", chat_id)
        return False
    return True


async def _ask_question(
    client: TelegramClient,
    interviewer: InterviewerClient,
    session: SessionState,
    answer: str | None,
) -> bool:
    """Ask (or re-ask) the next question; never raises.

    Args:
        client: Telegram client for the outbound message.
        interviewer: Interviewer client (the model call).
        session: Session being interviewed; advanced to ``INTERVIEW``.
        answer: The tentative answer that triggered this call, or ``None``
            (first question / re-ask after a failure).

    Returns:
        True when the question reached the user (and is now pending), False
        when the model or the send failed (an in-character retry was sent
        instead and no question is pending, so the user's next message can
        safely re-ask without recording anything).
    """
    chat_id = session.chat_id
    try:
        question = await interviewer.next_question(session.interview, answer)
    except (GeminiApiError, TelegramApiError):
        logger.exception("interviewer failed to ask chat_id=%s", chat_id)
        await _send(client, chat_id, INTERVIEW_RETRY_MSG)
        return False

    if session.phase is not Phase.INTERVIEW:
        log_phase_transition(logger, chat_id, session.phase, Phase.INTERVIEW)
        session.phase = Phase.INTERVIEW
    logger.info(
        "interview_question chat_id=%s number=%s",
        chat_id,
        session.interview.next_question_number,
    )
    if not await _send(client, chat_id, question):
        return False
    # Only now is it the question the user is answering.
    session.interview.pending_question = question
    return True


async def _handle_answer(
    client: TelegramClient,
    interviewer: InterviewerClient,
    session: SessionState,
    answer: str,
) -> None:
    """Resolve one answer and advance the interview; never raises.

    The thresholds are enforced here, not by the model: below
    :data:`~telegram_documentaries.interviewer.MIN_QUESTIONS` the next question
    is forced, inside the window the model chooses (ask or finish), and at
    :data:`~telegram_documentaries.interviewer.MAX_QUESTIONS` the profile is
    forced. The answer is recorded only once the model response is parsed, so
    a failed call can be retried without duplicating it.
    """
    chat_id = session.chat_id
    interview = session.interview
    pending = interview.pending_question or ""
    in_hand = len(interview.answers) + 1
    logger.info(
        "interview_answer chat_id=%s number=%s chars=%s",
        chat_id,
        in_hand,
        len(answer),
    )

    try:
        if in_hand < MIN_QUESTIONS:
            step = InterviewStep(
                action=InterviewAction.ASK,
                question=await interviewer.next_question(interview, answer),
            )
        elif in_hand < MAX_QUESTIONS:
            step = await interviewer.next_step(interview, answer)
        else:
            profile = await interviewer.distil(interview, answer)
            step = InterviewStep(
                action=InterviewAction.FINISH,
                profile=profile.profile,
                suggested_animal=profile.suggested_animal,
            )
    except (GeminiApiError, TelegramApiError):
        # Nothing recorded: the pending question stays pending, so resending
        # the same answer cannot duplicate it.
        logger.exception("interviewer failed chat_id=%s", chat_id)
        await _send(client, chat_id, INTERVIEW_RETRY_MSG)
        return

    # The step resolved: record the tentative answer exactly once.
    interview.answers.append(InterviewAnswer(question=pending, answer=answer))
    interview.pending_question = None

    if step.action is InterviewAction.FINISH:
        final_profile = step.to_profile()
        if final_profile is None:  # contract guard; the validator forbids this
            logger.error("interview_finish_without_profile chat_id=%s", chat_id)
            await _send(client, chat_id, INTERVIEW_RETRY_MSG)
            return
        interview.profile = final_profile
        log_phase_transition(logger, chat_id, session.phase, Phase.CONVERT)
        session.phase = Phase.CONVERT
        logger.info(
            "interview_finished chat_id=%s answers=%s animal=%r",
            chat_id,
            len(interview.answers),
            final_profile.suggested_animal,
        )
        await _send(
            client,
            chat_id,
            PROFILE_REVEAL_TEMPLATE.format(
                profile=final_profile.profile,
                animal=final_profile.suggested_animal,
            ),
        )
        return

    question = step.question
    if not question:  # contract guard; the validator forbids this
        logger.error("interview_ask_without_question chat_id=%s", chat_id)
        await _send(client, chat_id, INTERVIEW_RETRY_MSG)
        return
    if await _send(client, chat_id, question):
        interview.pending_question = question


async def _handle_interview(
    client: TelegramClient,
    interviewer: InterviewerClient,
    session: SessionState,
    text: str | None,
) -> None:
    """Route one message that arrived during the interview; never raises.

    Text is an answer; non-text (``None``) or whitespace-only text only nudges
    and repeats the pending question, without touching the state. If no
    question is pending - only possible after a failed ask - the next question
    is (re)asked and the user's text is never recorded as an answer.

    Args:
        client: Telegram client for outbound messages.
        interviewer: Interviewer client (the model call).
        session: Session in ``INTERVIEW_PHASES``.
        text: The message's text, or ``None`` for photo/sticker/voice/etc.
    """
    chat_id = session.chat_id
    answer = (text or "").strip()

    if not answer:
        pending = session.interview.pending_question
        if pending is None:
            logger.info("interview_nontext_without_question chat_id=%s", chat_id)
            await _ask_question(client, interviewer, session, answer=None)
            return
        logger.info(
            "interview_nudge chat_id=%s number=%s",
            chat_id,
            session.interview.next_question_number,
        )
        await _send(client, chat_id, f"{INTERVIEW_NUDGE}\n\n{pending}")
        return

    if session.interview.pending_question is None:
        logger.info("interview_reask chat_id=%s", chat_id)
        await _ask_question(client, interviewer, session, answer=None)
        return

    await _handle_answer(client, interviewer, session, answer)


async def _handle_photo(
    client: TelegramClient,
    vision: GeminiVisionClient,
    interviewer: InterviewerClient,
    session: SessionState,
    message: Message,
    media_root: str | Path,
) -> None:
    """Run the Bouncer flow for one photo message; never raises.

    Accept: store media, advance to ``AWAITING_INTERVIEW``, send the
    model-written confirmation and immediately ask the first interview
    question (phase → ``INTERVIEW``). Reject: send the content-specific quip
    and clear partial state so the user can immediately retry (no ``/start``).
    Failures: clear state first, then degrade with an in-character reply —
    every branch logs loudly (TECH.md error policy).
    """
    chat_id = message.chat.id
    if session.phase != Phase.AWAITING_PHOTO:
        logger.info("photo_out_of_phase chat_id=%s phase=%s", chat_id, session.phase)
        await client.send_message(chat_id=chat_id, text=LATER_PHASE_REPLY)
        return

    log_phase_transition(logger, chat_id, session.phase, Phase.BOUNCER_CHECK)
    session.phase = Phase.BOUNCER_CHECK

    try:
        image_bytes = await bouncer.fetch_photo(client, message.photo or [])
        verdict = await vision.check_photo(image_bytes)
    except PhotoValidationError:
        # Expected user input (too large): reset for retry, log with context.
        logger.warning("photo refused at the edge chat_id=%s", chat_id, exc_info=True)
        session.clear()
        await client.send_message(chat_id=chat_id, text=PHOTO_TOO_LARGE_MSG)
        return
    except (GeminiApiError, TelegramApiError):
        logger.exception("bouncer failed chat_id=%s", chat_id)
        session.clear()
        await client.send_message(chat_id=chat_id, text=BOUNCER_RETRY_MSG)
        return

    if not verdict.accepted:
        logger.info(
            "bouncer_rejected chat_id=%s reason=%s subject=%r",
            chat_id,
            verdict.reason,
            verdict.observed_subject,
        )
        session.clear()
        await client.send_message(chat_id=chat_id, text=verdict.message)
        log_phase_transition(logger, chat_id, Phase.BOUNCER_CHECK, session.phase)
        return

    # Accepted: persist the portrait, advance the state machine, confirm.
    try:
        stored_path = _store_photo(media_root, chat_id, image_bytes)
    except OSError:
        logger.exception("failed to store accepted photo chat_id=%s", chat_id)
        session.clear()
        await client.send_message(chat_id=chat_id, text=DISPATCH_FAILURE_MSG)
        return
    if message.photo:
        session.photo_file_id = bouncer.select_photo_size(message.photo).file_id
    session.photo_local_path = stored_path
    session.temp_media_path = str(Path(stored_path).parent)
    log_phase_transition(logger, chat_id, session.phase, Phase.AWAITING_INTERVIEW)
    session.phase = Phase.AWAITING_INTERVIEW
    await client.send_message(chat_id=chat_id, text=verdict.message)
    # The interview opens straight away: no extra prompt needed after "come in".
    await _ask_question(client, interviewer, session, answer=None)


def _install_signal_handlers(shutdown_event: asyncio.Event) -> None:
    """Route SIGINT/SIGTERM into a graceful loop exit (Ctrl+C / kill)."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except (NotImplementedError, RuntimeError) as exc:
            # Fallback (e.g. Windows or a non-main thread): the loop still
            # exits via shutdown_event / KeyboardInterrupt - but log it.
            logger.info("Signal handler for %s unavailable (%s)", sig.name, exc)


async def _poll_updates(
    client: TelegramClient,
    shutdown_event: asyncio.Event,
    poll_timeout: int,
) -> list[Update] | None:
    """Fetch one batch of updates, aborting promptly when shutdown is requested.

    Returns:
        The polled updates, or ``None`` when shutdown was requested while the
        long poll was still in flight.

    Raises:
        Whatever the underlying poll raises (logged loudly by the caller).
    """
    poll_task = asyncio.create_task(client.get_updates(poll_timeout=poll_timeout))
    stop_task = asyncio.create_task(shutdown_event.wait())
    try:
        await asyncio.wait({poll_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if not poll_task.done():
            return None  # shutdown won the race - abandon the in-flight poll
        return poll_task.result()  # may raise; the caller logs it loudly
    finally:
        stop_task.cancel()
        if not poll_task.done():
            poll_task.cancel()
        # Wait for cancellation so the client is never closed mid-request.
        await asyncio.gather(poll_task, stop_task, return_exceptions=True)


async def _dispatch_update(
    client: TelegramClient,
    vision: GeminiVisionClient,
    interviewer: InterviewerClient,
    sessions: dict[int, SessionState],
    media_root: str | Path,
    update: Update,
) -> None:
    """Route one update through the current pipeline stage; never raises.

    A single bad update must not kill the polling loop, but every failure is
    logged loudly (TECH.md: no swallowed exceptions).
    """
    try:
        if update.message is None:
            logger.debug(
                "Ignoring update %s: no message to reply to",
                update.update_id,
            )
            return

        session = _session_for(sessions, update.message.chat.id)

        if session.phase in INTERVIEW_PHASES:
            # Phase 3: the Interviewer owns this stage. A photo (or any other
            # non-text input) never advances the interview - it only nudges.
            text = None if update.message.photo else update.message.text
            await _handle_interview(client, interviewer, session, text)
            return

        if update.message.photo:
            await _handle_photo(
                client, vision, interviewer, session, update.message, media_root
            )
            return

        reply = build_reply(update.message, session)
        if reply is None:
            await _handle_photo(
                client, vision, interviewer, session, update.message, media_root
            )
            return

        try:
            await client.send_message(chat_id=update.message.chat.id, text=reply)
        except Exception:
            logger.exception(
                "Failed to send reply to chat_id=%s", update.message.chat.id
            )
            try:
                await client.send_message(
                    chat_id=update.message.chat.id, text=DISPATCH_FAILURE_MSG
                )
            except Exception:
                logger.exception(
                    "Failed to send fallback reply to chat_id=%s",
                    update.message.chat.id,
                )
    except Exception:
        logger.exception("Unexpected error handling update_id=%s", update.update_id)


async def run_bot(
    poll_timeout: int = 30,
    max_iterations: int | None = None,
    error_backoff: float = 1.0,
    *,
    shutdown_event: asyncio.Event | None = None,
    media_root: str | Path = DEFAULT_MEDIA_ROOT,
) -> None:
    """Run the Telegram long-polling loop until shutdown (or ``max_iterations``).

    Args:
        poll_timeout: Long-poll duration in seconds.
        max_iterations: Optional polling-iteration cap (test seam).
        error_backoff: Seconds to wait after a failed fetch before retrying.
        shutdown_event: Optional externally owned shutdown trigger; one is
            created and wired to SIGINT/SIGTERM when omitted.

    Raises:
        ConfigError: If TELEGRAM_BOT_TOKEN is missing/empty - fails loudly at
            startup instead of polling without credentials.
    """
    settings = load_settings()
    # Redact the token from *all* log output before anything can log it.
    install_secret_redaction(settings.telegram_bot_token)
    install_secret_redaction(settings.gemini_api_key)

    if shutdown_event is None:
        shutdown_event = asyncio.Event()
    _install_signal_handlers(shutdown_event)

    # The HTTP read timeout must outlast a single long poll, otherwise every
    # poll races its own timeout.
    client = TelegramClient(
        token=settings.telegram_bot_token,
        timeout=max(30.0, poll_timeout + 10.0),
    )
    vision = GeminiVisionClient(api_key=settings.gemini_api_key)
    interviewer = InterviewerClient(api_key=settings.gemini_api_key)
    sessions: dict[int, SessionState] = {}
    logger.info("Starting Telegram gateway (Phase 3 - Interviewer)")

    try:
        iterations = 0
        while not shutdown_event.is_set():
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1
            try:
                updates = await _poll_updates(client, shutdown_event, poll_timeout)
            except Exception:
                logger.exception("Failed to fetch updates; retrying after backoff")
                await asyncio.sleep(error_backoff)
                continue
            if updates is None:  # shutdown requested mid-poll
                break
            for update in updates:
                await _dispatch_update(
                    client, vision, interviewer, sessions, media_root, update
                )
            # Brief pause so a hot update feed cannot spin the loop.
            await asyncio.sleep(0.01)
    finally:
        await client.close()
        await vision.close()
        await interviewer.close()
        logger.info("Telegram gateway stopped cleanly")


def main() -> None:
    """Console entry point (``python -m telegram_documentaries``)."""
    configure_logging()
    try:
        asyncio.run(run_bot())
    except ConfigError as exc:
        logger.error(
            "Startup aborted: %s. Copy .env.example to .env and fill it in, "
            "or export the variable, then retry.",
            exc,
        )
        raise
    except KeyboardInterrupt:
        logger.info("Interrupted - shutting down")
    except Exception:
        logger.exception("Fatal gateway error")
        raise
