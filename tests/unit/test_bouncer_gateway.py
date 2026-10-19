"""Unit tests for the Bouncer wired into the gateway (Phase 2 routing).

No test contacts the real APIs: Telegram and Gemini are mocked and every
payload is a typed Pydantic model (TECH.md). All secrets are placeholders.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from telegram_documentaries import bot_gateway
from telegram_documentaries.gemini import GeminiApiError
from telegram_documentaries.models import (
    BouncerVerdict,
    RejectionReason,
    Update,
)

PLACEHOLDER_TOKEN = "123456:PLACEHOLDER-TELEGRAM-BOT-TOKEN"
PLACEHOLDER_GEMINI_KEY = "PLACEHOLDER-GEMINI-API-KEY"

ACCEPT_VERDICT = BouncerVerdict(
    accepted=True,
    observed_subject="a single person",
    message="Welcome to the shoot - let's begin.",
)
REJECT_VERDICT = BouncerVerdict(
    accepted=False,
    observed_subject="a Honda Civic",
    reason=RejectionReason.NOT_SOLO_HUMAN,
    message="Nice ride, but I need to see YOU before the shoot.",
)
#: The first interview question the acceptance flow asks (Phase 3).
FIRST_QUESTION = "What is your natural habitat?"


def photo_update(
    update_id: int,
    chat_id: int,
    *,
    file_size: int | None = None,
) -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1_760_000_100,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
                "photo": [
                    {
                        "file_id": f"p{update_id}-small",
                        "file_unique_id": f"u{update_id}-s",
                        "width": 90,
                        "height": 90,
                        "file_size": file_size,
                    },
                    {
                        "file_id": f"p{update_id}-large",
                        "file_unique_id": f"u{update_id}-l",
                        "width": 640,
                        "height": 480,
                        "file_size": file_size,
                    },
                ],
            },
        }
    )


def text_update(update_id: int, chat_id: int, text: str = "Hello") -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1_760_000_200,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
                "text": text,
            },
        }
    )


def sticker_only_update(update_id: int, chat_id: int) -> Update:
    """A message whose only content is a sticker (no text, no photo fields)."""
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1_760_000_300,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
            },
        }
    )


def settings_mock() -> Any:
    from unittest.mock import MagicMock

    settings = MagicMock()
    settings.telegram_bot_token = PLACEHOLDER_TOKEN
    settings.gemini_api_key = PLACEHOLDER_GEMINI_KEY
    return settings


async def run_with_mocks(
    updates: list[list[Update]],
    *,
    vision: AsyncMock,
    download: AsyncMock,
    send: AsyncMock,
    media_root: str,
    error_backoff: float = 0.0,
    interviewer: AsyncMock | None = None,
) -> None:
    """Run ``run_bot`` with Telegram + Gemini mocked out.

    Must be awaited *inside* the patch context: returning the coroutine
    instead would let the ``with`` block undo the patches before the bot
    actually runs (and the real API would be hit).
    """
    mock_client = AsyncMock()
    mock_client.get_updates = AsyncMock(side_effect=updates)
    mock_client.send_message = send
    mock_client.download_file = download
    mock_client.close = AsyncMock()

    with (
        patch.object(bot_gateway, "load_settings", return_value=settings_mock()),
        patch.object(bot_gateway, "TelegramClient", return_value=mock_client),
        patch.object(bot_gateway, "GeminiVisionClient", return_value=vision),
        patch.object(
            bot_gateway,
            "InterviewerClient",
            return_value=interviewer or make_interviewer(),
        ),
    ):
        await bot_gateway.run_bot(
            poll_timeout=0,
            max_iterations=len(updates),
            error_backoff=error_backoff,
            media_root=media_root,
        )


def make_interviewer(question: str = FIRST_QUESTION) -> AsyncMock:
    """A scripted interview client: acceptance always opens with one question."""
    interviewer = AsyncMock()
    interviewer.next_question = AsyncMock(return_value=question)
    interviewer.close = AsyncMock()
    return interviewer


def make_vision(*, result: Any = None, side_effect: Any = None) -> AsyncMock:
    vision = AsyncMock()
    if side_effect is not None:
        vision.check_photo = AsyncMock(side_effect=side_effect)
    else:
        vision.check_photo = AsyncMock(return_value=result)
    vision.close = AsyncMock()
    return vision


def sent_texts(send: AsyncMock) -> list[str]:
    # ``send`` IS client.send_message (see run_with_mocks) - read its calls
    # directly; send.send_message would be an unused auto-child mock.
    return [call.kwargs["text"] for call in send.await_args_list]


class TestPhotoFlow:
    @pytest.mark.asyncio
    async def test_accepted_photo_is_stored_and_advances_the_phase(
        self, tmp_path: Any
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        vision = make_vision(result=ACCEPT_VERDICT)
        download = AsyncMock(return_value=b"RECORD-ME-NOT-JPEG-BYTES")

        await run_with_mocks(
            # Second photo arrives after acceptance: the bouncer must NOT run
            # again (phase advanced) - the interview nudges and repeats Q1.
            [[photo_update(1, 555), photo_update(2, 555)], []],
            vision=vision,
            download=download,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [
            ACCEPT_VERDICT.message,
            FIRST_QUESTION,
            f"{bot_gateway.INTERVIEW_NUDGE}\n\n{FIRST_QUESTION}",
        ]
        vision.check_photo.assert_awaited_once()
        stored = tmp_path / "555" / "portrait.jpg"
        assert stored.read_bytes() == b"RECORD-ME-NOT-JPEG-BYTES"

    @pytest.mark.asyncio
    async def test_rejected_photo_quips_clears_and_accepts_immediate_retry(
        self, tmp_path: Any
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        vision = make_vision(side_effect=[REJECT_VERDICT, ACCEPT_VERDICT])
        download = AsyncMock(return_value=b"SECOND-CHANCE-BYTES")

        await run_with_mocks(
            # Reject first photo, then accept the immediate retry - no /start.
            [[photo_update(1, 555), photo_update(2, 555)], []],
            vision=vision,
            download=download,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [
            REJECT_VERDICT.message,
            ACCEPT_VERDICT.message,
            FIRST_QUESTION,
        ]
        # Both photos were judged: the retry went straight back to the bouncer.
        assert vision.check_photo.await_count == 2
        # Only the accepted photo was stored.
        assert (tmp_path / "555" / "portrait.jpg").read_bytes() == (
            b"SECOND-CHANCE-BYTES"
        )

    @pytest.mark.asyncio
    async def test_rejected_photo_alone_stores_no_media(self, tmp_path: Any) -> None:
        send = AsyncMock(return_value={"ok": True})
        vision = make_vision(result=REJECT_VERDICT)
        download = AsyncMock(return_value=b"SHOULD-NOT-BE-KEPT")

        await run_with_mocks(
            [[photo_update(1, 777)], []],
            vision=vision,
            download=download,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [REJECT_VERDICT.message]
        assert not (tmp_path / "777").exists()

    @pytest.mark.asyncio
    async def test_vision_failure_degrades_gracefully_and_keeps_polling(
        self, tmp_path: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        vision = make_vision(side_effect=GeminiApiError("503 from Gemini"))
        download = AsyncMock(return_value=b"BYTES")

        with caplog.at_level(logging.DEBUG):
            await run_with_mocks(
                # The next update must still be processed after the failure.
                [[photo_update(1, 555), text_update(2, 555)], []],
                vision=vision,
                download=download,
                send=send,
                media_root=str(tmp_path),
            )

        assert sent_texts(send) == [
            bot_gateway.BOUNCER_RETRY_MSG,
            bot_gateway.PHOTO_NUDGE,
        ]
        error_logs = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR
        ]
        assert any("chat_id=555" in message for message in error_logs)
        assert PLACEHOLDER_TOKEN not in caplog.text
        assert PLACEHOLDER_GEMINI_KEY not in caplog.text

    @pytest.mark.asyncio
    async def test_oversized_photo_is_refused_before_downloading(
        self, tmp_path: Any
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        vision = make_vision(result=ACCEPT_VERDICT)
        download = AsyncMock(return_value=b"NEVER-CALLED")

        await run_with_mocks(
            [[photo_update(1, 555, file_size=bouncer_max_photo_bytes())], []],
            vision=vision,
            download=download,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [bot_gateway.PHOTO_TOO_LARGE_MSG]
        download.assert_not_awaited()
        vision.check_photo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_bytes_are_never_logged(
        self, tmp_path: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        vision = make_vision(result=ACCEPT_VERDICT)
        download = AsyncMock(return_value=b"SECRET-IMAGE-BYTES")

        with caplog.at_level(logging.DEBUG):
            await run_with_mocks(
                [[photo_update(1, 555)], []],
                vision=vision,
                download=download,
                send=send,
                media_root=str(tmp_path),
            )

        assert "SECRET-IMAGE-BYTES" not in caplog.text
        assert PLACEHOLDER_TOKEN not in caplog.text
        assert PLACEHOLDER_GEMINI_KEY not in caplog.text


def bouncer_max_photo_bytes() -> int:
    from telegram_documentaries import bouncer

    return bouncer.MAX_PHOTO_BYTES + 1


class TestNonPhotoInput:
    @pytest.mark.asyncio
    async def test_text_while_awaiting_photo_gets_the_photo_nudge(
        self, tmp_path: Any
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        vision = make_vision(result=ACCEPT_VERDICT)

        await run_with_mocks(
            [[text_update(1, 555, "hey bot")], []],
            vision=vision,
            download=AsyncMock(),
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [bot_gateway.PHOTO_NUDGE]
        assert "Phase 1" not in sent_texts(send)[0]

    @pytest.mark.asyncio
    async def test_non_photo_media_while_awaiting_photo_gets_the_nudge(
        self, tmp_path: Any
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        vision = make_vision(result=ACCEPT_VERDICT)

        await run_with_mocks(
            [[sticker_only_update(1, 555)], []],
            vision=vision,
            download=AsyncMock(),
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [bot_gateway.PHOTO_NUDGE]


class TestSendFailureDegrades:
    @pytest.mark.asyncio
    async def test_failed_reply_is_logged_and_a_fallback_is_attempted(
        self, tmp_path: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        send = AsyncMock(
            side_effect=[
                bot_gateway.TelegramApiError("sendMessage failed"),
                {"ok": True},
            ]
        )
        vision = make_vision(result=ACCEPT_VERDICT)

        with caplog.at_level(logging.DEBUG):
            await run_with_mocks(
                [[text_update(1, 555)], []],
                vision=vision,
                download=AsyncMock(),
                send=send,
                media_root=str(tmp_path),
            )

        texts = sent_texts(send)
        assert texts[0] == bot_gateway.PHOTO_NUDGE
        assert texts[1] == bot_gateway.DISPATCH_FAILURE_MSG
        error_logs = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR
        ]
        assert any("chat_id=555" in message for message in error_logs)
        assert PLACEHOLDER_TOKEN not in caplog.text
