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
from telegram_documentaries.models import GeneratedImage, Update

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
        # Mirror production: run_bot installs the redaction filter on root
        # handlers before any polling (config.install_secret_redaction).
        bot_gateway.install_secret_redaction(PLACEHOLDER_TOKEN)
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


class TestDownloadFile:
    @pytest.mark.asyncio
    async def test_resolves_file_path_then_downloads_bytes(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path.endswith("/getFile"):
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": {
                            "file_id": "f1",
                            "file_unique_id": "u1",
                            "file_size": 1024,
                            "file_path": "photos/file_42.jpg",
                        },
                    },
                )
            return httpx.Response(200, content=b"\xff\xd8JPEG-BYTES")

        client = make_client(handler)
        try:
            data = await client.download_file("f1")
        finally:
            await client.close()

        assert data == b"\xff\xd8JPEG-BYTES"
        assert seen[0].url.path == f"/bot{PLACEHOLDER_TOKEN}/getFile"
        assert seen[0].url.params["file_id"] == "f1"
        assert seen[1].url.path == f"/file/bot{PLACEHOLDER_TOKEN}/photos/file_42.jpg"

    @pytest.mark.asyncio
    async def test_unexpected_getfile_shape_raises_loudly(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True, "result": {"file_id": "f1"}})

        client = make_client(handler)
        with pytest.raises(bot_gateway.TelegramApiError, match="file_path"):
            await client.download_file("f1")
        await client.close()

    @pytest.mark.asyncio
    async def test_getfile_http_error_raises_without_leaking_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"ok": False, "description": "bad file"})

        client = make_client(handler)
        with pytest.raises(bot_gateway.TelegramApiError) as exc_info:
            await client.download_file("f1")
        await client.close()

        assert PLACEHOLDER_TOKEN not in str(exc_info.value)
        assert "400" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_file_download_transport_error_raises_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/getFile"):
                return httpx.Response(
                    200,
                    json={"ok": True, "result": {"file_path": "photos/x.jpg"}},
                )
            raise httpx.ReadTimeout("download timed out")

        client = make_client(handler)
        with pytest.raises(bot_gateway.TelegramApiError, match="ReadTimeout"):
            await client.download_file("f1")
        await client.close()

    @pytest.mark.asyncio
    async def test_file_download_http_error_raises_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/getFile"):
                return httpx.Response(
                    200,
                    json={"ok": True, "result": {"file_path": "photos/x.jpg"}},
                )
            return httpx.Response(403, json={"ok": False})

        client = make_client(handler)
        with pytest.raises(bot_gateway.TelegramApiError, match="403"):
            await client.download_file("f1")
        await client.close()


class TestTimeoutsAndRetries:
    """A stalled request must not freeze the reply for the whole poll timeout.

    Live validation: one `getFile` call hung for 38s because every request
    shared the 40s long-poll read timeout, with no feedback to the user.
    """

    @pytest.mark.asyncio
    async def test_poll_uses_the_long_timeout_while_api_calls_stay_short(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path.endswith("/getUpdates"):
                return httpx.Response(200, json={"ok": True, "result": []})
            return httpx.Response(200, json={"ok": True, "result": True})

        client = make_client(handler)
        try:
            await client.get_updates(poll_timeout=30)
            await client.send_message(chat_id=555, text="Hello back")
        finally:
            await client.close()

        poll_timeout = seen[0].extensions["timeout"]["read"]
        send_timeout = seen[1].extensions["timeout"]["read"]
        assert poll_timeout == 40.0
        assert send_timeout == bot_gateway.API_TIMEOUT_SECONDS
        assert send_timeout < poll_timeout

    @pytest.mark.asyncio
    async def test_stalled_getfile_is_retried_once_and_then_succeeds(self) -> None:
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/getFile"):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise httpx.ReadTimeout("stalled")
                return httpx.Response(
                    200,
                    json={"ok": True, "result": {"file_path": "photos/x.jpg"}},
                )
            return httpx.Response(200, content=b"JPEG-BYTES")

        client = make_client(handler)
        try:
            data = await client.download_file("f1")
        finally:
            await client.close()

        assert data == b"JPEG-BYTES"
        assert attempts["count"] == 2

    @pytest.mark.asyncio
    async def test_stalled_file_download_is_retried_once(self) -> None:
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/getFile"):
                return httpx.Response(
                    200,
                    json={"ok": True, "result": {"file_path": "photos/x.jpg"}},
                )
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise httpx.ReadTimeout("stalled")
            return httpx.Response(200, content=b"JPEG-BYTES")

        client = make_client(handler)
        try:
            data = await client.download_file("f1")
        finally:
            await client.close()

        assert data == b"JPEG-BYTES"
        assert attempts["count"] == 2

    @pytest.mark.asyncio
    async def test_stalled_send_is_never_retried(self) -> None:
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            raise httpx.ReadTimeout("stalled")

        client = make_client(handler)
        with pytest.raises(bot_gateway.TelegramApiError, match="ReadTimeout"):
            await client.send_message(chat_id=555, text="Hello back")
        await client.close()

        # A retried send could post the same message twice: never do it.
        assert attempts["count"] == 1


class TestSendPhoto:
    IMAGE = GeneratedImage(mime_type="image/jpeg", data=b"\xff\xd8\xff\xe0-hybrid")

    @pytest.mark.asyncio
    async def test_posts_a_multipart_upload_and_returns_api_payload(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200, json={"ok": True, "result": {"message_id": 101, "date": 2}}
            )

        client = make_client(handler)
        try:
            result = await client.send_photo(
                chat_id=555, image=self.IMAGE, caption="Suggested casting: Red fox"
            )
        finally:
            await client.close()

        assert result == {"ok": True, "result": {"message_id": 101, "date": 2}}
        request = seen[0]
        assert seen[0].method == "POST"
        assert seen[0].url.path == f"/bot{PLACEHOLDER_TOKEN}/sendPhoto"
        assert seen[0].headers["content-type"].startswith("multipart/form-data")
        body = seen[0].content
        assert b'name="chat_id"' in body and b"555" in body
        assert b'name="caption"' in body
        assert b"Suggested casting: Red fox" in body
        assert b'name="photo"; filename="portrait.jpg"' in body
        assert b"image/jpeg" in body
        assert b"\xff\xd8\xff\xe0-hybrid" in request.read()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("mime_type", "filename"),
        [
            ("image/jpeg", b'filename="portrait.jpg"'),
            ("image/png", b'filename="portrait.png"'),
            ("image/webp", b'filename="portrait.webp"'),
            ("image/avif", b'filename="portrait.jpg"'),
        ],
    )
    async def test_picks_a_filename_from_the_mime_type(
        self, mime_type: str, filename: bytes
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"ok": True, "result": {}})

        client = make_client(handler)
        try:
            await client.send_photo(
                chat_id=555,
                image=GeneratedImage(mime_type=mime_type, data=b"IMAGE"),
                caption="Suggested casting: Red fox",
            )
        finally:
            await client.close()

        assert filename in seen[0].content

    @pytest.mark.asyncio
    async def test_stalled_photo_send_is_never_retried(self) -> None:
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            raise httpx.ReadTimeout("stalled")

        client = make_client(handler)
        with pytest.raises(bot_gateway.TelegramApiError, match="ReadTimeout"):
            await client.send_photo(chat_id=555, image=self.IMAGE, caption="caption")
        await client.close()

        # A retried upload could post the same photo twice: never do it.
        assert attempts["count"] == 1

    @pytest.mark.asyncio
    async def test_http_error_is_typed_and_redacts_the_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text=f"bad request for {PLACEHOLDER_TOKEN}")

        client = make_client(handler)
        try:
            with pytest.raises(bot_gateway.TelegramApiError) as caught:
                await client.send_photo(
                    chat_id=555, image=self.IMAGE, caption="caption"
                )
        finally:
            await client.close()

        assert "400" in str(caught.value)
        assert PLACEHOLDER_TOKEN not in str(caught.value)
