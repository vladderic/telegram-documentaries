"""Phase 1 Telegram gateway (entry point).

Responsibilities:
- load secrets from ``.env`` (never hardcode, never log them),
- long-poll Telegram (``getUpdates``) and answer every incoming message with
  a hardcoded friendly reply (later phases replace the echo),
- fail loudly with a clear log when configuration is missing,
- shut down gracefully on Ctrl+C / SIGTERM, even mid-poll.

Errors on the user's conversation path are caught and logged loudly instead of
raised into the loop; nothing is ever swallowed unlogged (TECH.md).
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

import httpx

from telegram_documentaries.config import (
    REDACTED,
    ConfigError,
    configure_logging,
    install_secret_redaction,
    load_settings,
)
from telegram_documentaries.models import Update, UpdateResult

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


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


def build_reply(update: Update) -> str:
    """Build the hardcoded friendly response for a validated Phase 1 update.

    Args:
        update: Validated update from Telegram.

    Returns:
        The hardcoded reply string (replaced by real pipeline stages later).
    """
    if update.message is not None:
        return "Hello! Thanks for your message. Phase 1 gateway is running."
    return "Hello! Thanks for reaching out. Phase 1 gateway is running."


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


async def _dispatch_update(client: TelegramClient, update: Update) -> None:
    """Build the reply for one update and send it; never raises.

    A single bad update must not kill the polling loop, but every failure is
    logged loudly (TECH.md: no swallowed exceptions).
    """
    if update.message is None:
        logger.debug("Ignoring update %s: no message to reply to", update.update_id)
        return

    reply = build_reply(update)
    try:
        await client.send_message(chat_id=update.message.chat.id, text=reply)
    except Exception:
        logger.exception("Failed to send reply to chat_id=%s", update.message.chat.id)


async def run_bot(
    poll_timeout: int = 30,
    max_iterations: int | None = None,
    error_backoff: float = 1.0,
    *,
    shutdown_event: asyncio.Event | None = None,
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

    if shutdown_event is None:
        shutdown_event = asyncio.Event()
    _install_signal_handlers(shutdown_event)

    # The HTTP read timeout must outlast a single long poll, otherwise every
    # poll races its own timeout.
    client = TelegramClient(
        token=settings.telegram_bot_token,
        timeout=max(30.0, poll_timeout + 10.0),
    )
    logger.info("Starting Telegram gateway (Phase 1)")

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
                await _dispatch_update(client, update)
            # Brief pause so a hot update feed cannot spin the loop.
            await asyncio.sleep(0.01)
    finally:
        await client.close()
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
