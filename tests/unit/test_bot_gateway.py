"""Unit tests for the Phase 1 Telegram gateway (entry point).

No test ever contacts the real Telegram API: the client is mocked and payloads
are typed Pydantic models (TECH.md). All tokens below are placeholders.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from telegram_documentaries import bot_gateway
from telegram_documentaries.config import ConfigError
from telegram_documentaries.models import Phase, SessionState, Update, UpdateResult

PLACEHOLDER_TOKEN = "123456:PLACEHOLDER-TELEGRAM-BOT-TOKEN"
PLACEHOLDER_GEMINI_KEY = "PLACEHOLDER-GEMINI-API-KEY"

SignalRegistration = list[tuple[signal.Signals, Callable[[], None]]]


def make_settings() -> MagicMock:
    settings = MagicMock()
    settings.telegram_bot_token = PLACEHOLDER_TOKEN
    settings.gemini_api_key = PLACEHOLDER_GEMINI_KEY
    return settings


def text_update(update_id: int, chat_id: int, text: str = "Hello") -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1_760_000_000,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
                "text": text,
            },
        }
    )


def inert_signal_handlers(
    monkeypatch: pytest.MonkeyPatch, registered: SignalRegistration
) -> None:
    """Record signal-handler registrations, then simulate an unsupported platform."""

    def fake_add_signal_handler(
        self: object,
        sig: signal.Signals,
        callback: Callable[[], None],
        *args: object,
    ) -> None:
        registered.append((sig, callback))
        raise NotImplementedError("signal handlers unavailable in this test")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(type(loop), "add_signal_handler", fake_add_signal_handler)


class TestBuildReply:
    def test_text_at_awaiting_photo_gets_the_photo_nudge(self) -> None:
        update = text_update(1, 12345, text="Hi bot")
        assert update.message is not None

        reply = bot_gateway.build_reply(update.message, SessionState(chat_id=12345))

        assert reply == bot_gateway.PHOTO_NUDGE
        assert "photograph" in reply.lower()
        assert "Phase 1" not in reply

    def test_photo_message_is_left_to_the_bouncer(self) -> None:
        update = Update.model_validate(
            {
                "update_id": 2,
                "message": {
                    "message_id": 11,
                    "date": 1_760_000_001,
                    "chat": {"id": 12345, "type": "private"},
                    "from": {"id": 12345, "is_bot": False, "first_name": "Alex"},
                    "photo": [
                        {
                            "file_id": "p1",
                            "file_unique_id": "u1",
                            "width": 100,
                            "height": 100,
                        },
                    ],
                },
            }
        )
        assert update.message is not None

        reply = bot_gateway.build_reply(update.message, SessionState(chat_id=12345))

        # None = "the bouncer flow handles this message", not a plain reply.
        assert reply is None

    def test_input_after_the_bouncer_gets_a_holding_reply(self) -> None:
        update = text_update(3, 777, text="more questions?")
        assert update.message is not None
        session = SessionState(chat_id=777, phase=Phase.AWAITING_INTERVIEW)

        reply = bot_gateway.build_reply(update.message, session)

        assert reply == bot_gateway.LATER_PHASE_REPLY


class TestRunBotMissingToken:
    @pytest.mark.asyncio
    async def test_raises_config_error_if_token_missing(self) -> None:
        """Graceful startup: fail loudly if TELEGRAM_BOT_TOKEN missing."""
        with (
            patch.object(
                bot_gateway, "load_settings", side_effect=ConfigError("missing")
            ),
            pytest.raises(ConfigError),
        ):
            await bot_gateway.run_bot()


class TestRunBotBasicFlow:
    @pytest.mark.asyncio
    async def test_processes_updates_and_sends_replies(self) -> None:
        """Processes one update and sends a reply via Telegram API."""
        update = text_update(9001, 555, text="Hello")

        mock_client = AsyncMock()
        mock_client.get_updates = AsyncMock(side_effect=[[update], []])
        mock_client.send_message = AsyncMock(return_value={"ok": True})
        mock_client.close = AsyncMock()

        with (
            patch.object(bot_gateway, "load_settings", return_value=make_settings()),
            patch.object(bot_gateway, "TelegramClient", return_value=mock_client),
        ):
            await bot_gateway.run_bot(poll_timeout=0, max_iterations=2)

        mock_client.get_updates.assert_awaited()
        mock_client.send_message.assert_awaited_once()
        kwargs = mock_client.send_message.await_args.kwargs
        assert kwargs["chat_id"] == 555
        assert kwargs["text"] == bot_gateway.PHOTO_NUDGE
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_replies_to_every_update_in_a_single_poll(self) -> None:
        """Any incoming message gets the hardcoded reply - none are dropped."""
        first = text_update(1, 111, text="one")
        second = text_update(2, 222, text="two")

        mock_client = AsyncMock()
        mock_client.get_updates = AsyncMock(side_effect=[[first, second], []])
        mock_client.send_message = AsyncMock(return_value={"ok": True})
        mock_client.close = AsyncMock()

        with (
            patch.object(bot_gateway, "load_settings", return_value=make_settings()),
            patch.object(bot_gateway, "TelegramClient", return_value=mock_client),
        ):
            await bot_gateway.run_bot(poll_timeout=0, max_iterations=2)

        sent_chats = [
            call.kwargs["chat_id"] for call in mock_client.send_message.await_args_list
        ]
        assert sent_chats == [111, 222]

    @pytest.mark.asyncio
    async def test_handles_malformed_updates_gracefully(self) -> None:
        """Untrusted/malformed updates must not crash the bot."""
        update = Update.model_validate({"update_id": 5})  # no message to reply to

        mock_client = AsyncMock()
        mock_client.get_updates = AsyncMock(side_effect=[[update], []])
        mock_client.send_message = AsyncMock(return_value={"ok": True})
        mock_client.close = AsyncMock()

        with (
            patch.object(bot_gateway, "load_settings", return_value=make_settings()),
            patch.object(bot_gateway, "TelegramClient", return_value=mock_client),
        ):
            await bot_gateway.run_bot(
                poll_timeout=0, max_iterations=2
            )  # must not raise

        mock_client.send_message.assert_not_awaited()
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_client_keeps_the_short_timeout_while_the_poll_window_is_passed(
        self,
    ) -> None:
        """Only `getUpdates` waits for the long poll; other calls fail fast.

        The client is built with its short default (a 40s read timeout for a
        stalled download was the live-validation 38s freeze).
        """
        mock_client = AsyncMock()
        mock_client.get_updates = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()
        settings = make_settings()

        with (
            patch.object(bot_gateway, "load_settings", return_value=settings),
            patch.object(
                bot_gateway, "TelegramClient", return_value=mock_client
            ) as client_cls,
        ):
            await bot_gateway.run_bot(poll_timeout=7, max_iterations=1)

        # No timeout override: the client keeps API_TIMEOUT_SECONDS.
        assert client_cls.call_args.kwargs == {"token": settings.telegram_bot_token}
        # The long-poll window travels with the poll call instead.
        mock_client.get_updates.assert_awaited_with(poll_timeout=7)


class TestRunBotResilience:
    @staticmethod
    def _validation_error() -> Exception:
        with pytest.raises(ValidationError) as exc_info:
            UpdateResult.model_validate({"update_id": "not-a-number"})
        return exc_info.value

    @pytest.mark.parametrize(
        "error_factory",
        [
            pytest.param(
                lambda: bot_gateway.TelegramApiError(f"boom near {PLACEHOLDER_TOKEN}"),
                id="api-error",
            ),
            pytest.param(
                lambda: TestRunBotResilience._validation_error(), id="bad-payload"
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_fetch_failures_are_logged_loudly_without_leaking_the_token(
        self,
        error_factory: Callable[[], Exception],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Fail loudly and keep polling; a token in any log line is a leak."""
        error = error_factory()

        mock_client = AsyncMock()
        mock_client.get_updates = AsyncMock(side_effect=[error, []])
        mock_client.send_message = AsyncMock(return_value={"ok": True})
        mock_client.close = AsyncMock()

        with (
            caplog.at_level(logging.DEBUG),
            patch.object(bot_gateway, "load_settings", return_value=make_settings()),
            patch.object(bot_gateway, "TelegramClient", return_value=mock_client),
        ):
            await bot_gateway.run_bot(
                poll_timeout=0, max_iterations=2, error_backoff=0.0
            )

        error_logs = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR
        ]
        assert any("fetch" in message.lower() for message in error_logs)
        assert PLACEHOLDER_TOKEN not in caplog.text
        mock_client.close.assert_awaited_once()


class TestGracefulShutdown:
    @pytest.mark.asyncio
    async def test_registers_handlers_for_sigint_and_sigterm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl+C and SIGTERM must both route into the same graceful exit."""
        registered: SignalRegistration = []
        inert_signal_handlers(monkeypatch, registered)
        shutdown_event = asyncio.Event()

        mock_client = AsyncMock()
        mock_client.get_updates = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        with (
            patch.object(bot_gateway, "load_settings", return_value=make_settings()),
            patch.object(bot_gateway, "TelegramClient", return_value=mock_client),
        ):
            await bot_gateway.run_bot(max_iterations=1, shutdown_event=shutdown_event)

        assert [sig for sig, _ in registered] == [signal.SIGINT, signal.SIGTERM]
        assert all(callback == shutdown_event.set for _, callback in registered)

    @pytest.mark.asyncio
    async def test_shutdown_aborts_an_in_flight_poll_and_closes_the_client(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Shutdown must not wait for a long poll that may last another 30s."""
        inert_signal_handlers(monkeypatch, [])
        poll_started = asyncio.Event()

        async def blocking_get_updates(*, poll_timeout: int) -> list[Update]:
            poll_started.set()
            await asyncio.Event().wait()  # never completes on its own
            raise AssertionError("poll must not complete without shutdown")

        mock_client = AsyncMock()
        mock_client.get_updates = AsyncMock(side_effect=blocking_get_updates)
        mock_client.close = AsyncMock()
        shutdown_event = asyncio.Event()

        with (
            caplog.at_level(logging.DEBUG),
            patch.object(bot_gateway, "load_settings", return_value=make_settings()),
            patch.object(bot_gateway, "TelegramClient", return_value=mock_client),
        ):
            task = asyncio.create_task(
                bot_gateway.run_bot(poll_timeout=30, shutdown_event=shutdown_event)
            )
            await asyncio.wait_for(poll_started.wait(), timeout=2)
            shutdown_event.set()
            await asyncio.wait_for(task, timeout=2)

        mock_client.close.assert_awaited_once()
        assert any(
            "stopped cleanly" in record.getMessage() for record in caplog.records
        )


class TestMain:
    def test_missing_token_fails_loudly_with_a_clear_log(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No .env and no env var: startup must abort with a helpful error."""
        monkeypatch.chdir(tmp_path)  # no .env anywhere up the tree
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        with caplog.at_level(logging.DEBUG), pytest.raises(ConfigError):
            bot_gateway.main()

        error_messages = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR
        ]
        assert any("TELEGRAM_BOT_TOKEN" in message for message in error_messages)
        assert any(".env" in message for message in error_messages)
        assert PLACEHOLDER_TOKEN not in caplog.text
