"""Unit tests for the Phase 1 Telegram client (long polling / sendMessage).

The real Telegram API is never contacted: responses are mocked with
httpx.MockTransport and parsed into typed Pydantic models at the boundary
(TECH.md). All tokens below are placeholders.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from telegram_documentaries import bot_gateway
from telegram_documentaries.models import Update

PLACEHOLDER_TOKEN = "123456:PLACEHOLDER-TELEGRAM-BOT-TOKEN"

TEXT_UPDATE: dict[str, Any] = {
    "update_id": 9001,
    "message": {
        "message_id": 42,
        "date": 1_760_000_000,
        "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "is_bot": False, "first_name": "Ada"},
        "text": "Hello there",
    },
}

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler) -> Any:
    """Build a client whose HTTP traffic is served by ``handler`` (no network)."""
    return bot_gateway.TelegramClient(
        token=PLACEHOLDER_TOKEN,
        transport=httpx.MockTransport(handler),
    )


class TestGetUpdates:
    @pytest.mark.asyncio
    async def test_parses_mocked_api_response_into_typed_models(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"ok": True, "result": [TEXT_UPDATE]})

        client = make_client(handler)
        try:
            updates = await client.get_updates()
        finally:
            await client.close()

        assert len(updates) == 1
        update = updates[0]
        assert isinstance(update, Update)
        assert update.message is not None
        assert update.message.text == "Hello there"
        assert update.message.chat.id == 555
        assert seen[0].url.path == f"/bot{PLACEHOLDER_TOKEN}/getUpdates"
        assert seen[0].url.params["timeout"] == "30"

    @pytest.mark.asyncio
    async def test_empty_result_returns_no_updates_and_sends_no_offset(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"ok": True, "result": []})

        client = make_client(handler)
        try:
            assert await client.get_updates() == []
        finally:
            await client.close()

        assert "offset" not in seen[0].url.params

    @pytest.mark.asyncio
    async def test_advances_offset_so_updates_are_never_reprocessed(self) -> None:
        responses = iter(
            [
                {"ok": True, "result": [TEXT_UPDATE]},
                {"ok": True, "result": []},
            ]
        )
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=next(responses))

        client = make_client(handler)
        try:
            first = await client.get_updates()
            second = await client.get_updates()
        finally:
            await client.close()

        assert len(first) == 1
        assert second == []
        # First poll has no offset; the next one resumes after update_id 9001.
        assert "offset" not in seen[0].url.params
        assert seen[1].url.params["offset"] == "9002"

    @pytest.mark.asyncio
    async def test_http_error_raises_api_error_without_leaking_the_token(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"ok": False, "error_code": 401, "description": "Unauthorized"},
            )

        client = make_client(handler)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(bot_gateway.TelegramApiError) as exc_info:
                await client.get_updates()
            await client.close()

        message = str(exc_info.value)
        assert "401" in message
        assert PLACEHOLDER_TOKEN not in message
        assert PLACEHOLDER_TOKEN not in caplog.text

    @pytest.mark.asyncio
    async def test_transport_failure_raises_api_error_without_leaking_the_token(
        self,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot reach api for token {PLACEHOLDER_TOKEN}")

        client = make_client(handler)
        with pytest.raises(bot_gateway.TelegramApiError) as exc_info:
            await client.get_updates()
        await client.close()

        assert PLACEHOLDER_TOKEN not in str(exc_info.value)
        assert "ConnectError" in str(exc_info.value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"ok": True}, id="missing-result-key"),
            pytest.param([1, 2, 3], id="not-an-object"),
        ],
    )
    async def test_unexpected_response_shape_is_rejected_loudly(
        self, payload: Any
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        client = make_client(handler)
        with pytest.raises(bot_gateway.TelegramApiError):
            await client.get_updates()
        await client.close()

    @pytest.mark.asyncio
    async def test_malformed_update_inside_result_is_rejected_loudly(self) -> None:
        """Untrusted Telegram payloads must never pass as raw dicts (TECH.md)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"ok": True, "result": [{"update_id": "not-a-number"}]},
            )

        client = make_client(handler)
        with pytest.raises(ValidationError):
            await client.get_updates()
        await client.close()


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_posts_chat_id_and_text_and_returns_api_payload(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200, json={"ok": True, "result": {"message_id": 100, "date": 1}}
            )

        client = make_client(handler)
        try:
            result = await client.send_message(chat_id=555, text="Hello back")
        finally:
            await client.close()

        assert result == {"ok": True, "result": {"message_id": 100, "date": 1}}
        assert seen[0].method == "POST"
        assert seen[0].url.path == f"/bot{PLACEHOLDER_TOKEN}/sendMessage"
        assert json.loads(seen[0].content) == {"chat_id": 555, "text": "Hello back"}
