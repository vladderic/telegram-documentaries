"""Unit tests for the Bouncer's photo fetch/validate logic (Phase 2).

No test contacts the real Telegram API: the photo source is a stub and
download failures reuse the typed Telegram errors from the gateway.
"""

from __future__ import annotations

import logging

import pytest

from telegram_documentaries import bot_gateway, bouncer
from telegram_documentaries.models import Message, PhotoSize, PortraitSource


def photo_sizes(
    *,
    file_size: int | None = None,
    large_id: str = "p2-large",
) -> list[PhotoSize]:
    """A typical Telegram photo array: ascending resolution, largest last."""
    return [
        PhotoSize(
            file_id="p1-small",
            file_unique_id="u1",
            width=90,
            height=90,
            file_size=file_size,
        ),
        PhotoSize(
            file_id=large_id,
            file_unique_id="u2",
            width=640,
            height=480,
            file_size=file_size,
        ),
    ]


def photo_payload() -> list[dict[str, object]]:
    """Raw Telegram photo array (as JSON), largest last."""
    return [
        {
            "file_id": "p1-small",
            "file_unique_id": "u1",
            "width": 90,
            "height": 90,
        },
        {
            "file_id": "p2-large",
            "file_unique_id": "u2",
            "width": 640,
            "height": 480,
        },
    ]


class StubSource:
    """PhotoSource stub recording which file was requested."""

    def __init__(
        self,
        data: bytes = b"\xff\xd8fake-jpeg",
        error: Exception | None = None,
    ) -> None:
        self.data = data
        self.error = error
        self.requested: list[str] = []

    async def download_file(self, file_id: str) -> bytes:
        self.requested.append(file_id)
        if self.error is not None:
            raise self.error
        return self.data


class TestFetchPortrait:
    """One intake for photos and image documents (Phase 4b).

    The *choice* of portrait lives in ``portrait_source`` (tested below); this
    tests the transfer and its edge guards: the declared size is checked before
    any download, and the real bytes are checked again afterwards.
    """

    @staticmethod
    def source(
        *,
        file_id: str = "p2-large",
        mime_type: str = "image/png",
        file_size: int | None = None,
    ) -> PortraitSource:
        return PortraitSource(file_id=file_id, mime_type=mime_type, file_size=file_size)

    @pytest.mark.asyncio
    async def test_downloads_the_chosen_portrait(self) -> None:
        source = StubSource(data=b"ORIGINAL-BYTES")

        result = await bouncer.fetch_portrait(source, self.source())

        assert result == b"ORIGINAL-BYTES"
        assert source.requested == ["p2-large"]

    @pytest.mark.asyncio
    async def test_oversized_by_metadata_never_downloads(self) -> None:
        source = StubSource()
        oversized = bouncer.MAX_PHOTO_BYTES + 1

        with pytest.raises(bouncer.PhotoValidationError):
            await bouncer.fetch_portrait(source, self.source(file_size=oversized))

        # Edge guard: untrusted input is checked *before* any transfer.
        assert source.requested == []

    @pytest.mark.asyncio
    async def test_oversized_actual_bytes_fail_after_download(self) -> None:
        # Metadata missing/lying (untrusted): the real size must still be checked.
        source = StubSource(data=b"x" * (bouncer.MAX_PHOTO_BYTES + 1))

        with pytest.raises(bouncer.PhotoValidationError):
            await bouncer.fetch_portrait(source, self.source())

    @pytest.mark.asyncio
    async def test_download_failure_propagates_as_typed_error(self) -> None:
        source = StubSource(error=bot_gateway.TelegramApiError("getFile failed"))

        with pytest.raises(bot_gateway.TelegramApiError):
            await bouncer.fetch_portrait(source, self.source())

    @pytest.mark.asyncio
    async def test_failures_are_logged_loudly_by_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Decorator logging (TECH.md): failures log name + type, not bytes."""
        source = StubSource(error=bot_gateway.TelegramApiError("boom"))

        with (
            caplog.at_level(logging.DEBUG),
            pytest.raises(bot_gateway.TelegramApiError),
        ):
            await bouncer.fetch_portrait(source, self.source())

        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ]
        assert any("fetch_portrait" in message for message in warnings)
        assert any("TelegramApiError" in message for message in warnings)


class TestPortraitSource:
    """One predicate decides "is this message a portrait?" (Phase 4b).

    Photos still win when both are present, but the eligible-upload rule lives
    in a single place so routing can never disagree with the stage.
    """

    @staticmethod
    def _message(**fields: object) -> Message:
        return Message.model_validate(
            {
                "message_id": 1,
                "date": 1_700_000_000,
                "chat": {"id": 555, "type": "private"},
                **fields,
            }
        )

    def test_photo_message_yields_the_largest_size_as_source(self) -> None:
        source = bouncer.portrait_source(self._message(photo=photo_payload()))

        assert source is not None
        assert source.file_id == "p2-large"
        assert source.mime_type == "image/jpeg"

    def test_image_document_yields_a_source_with_its_own_metadata(self) -> None:
        source = bouncer.portrait_source(
            self._message(
                document={
                    "file_id": "doc-1",
                    "file_unique_id": "u-doc-1",
                    "file_name": "portrait.png",
                    "mime_type": "image/png",
                    "file_size": 2_048_000,
                }
            )
        )

        assert source is not None
        assert source.file_id == "doc-1"
        assert source.mime_type == "image/png"
        assert source.file_size == 2_048_000

    def test_photo_wins_when_a_message_carries_both(self) -> None:
        source = bouncer.portrait_source(
            self._message(
                photo=photo_payload(),
                document={
                    "file_id": "doc-1",
                    "file_unique_id": "u-doc-1",
                    "mime_type": "image/png",
                },
            )
        )

        assert source is not None
        assert source.file_id == "p2-large"

    def test_non_image_document_is_not_a_portrait(self) -> None:
        source = bouncer.portrait_source(
            self._message(
                document={
                    "file_id": "doc-2",
                    "file_unique_id": "u-doc-2",
                    "file_name": "rules.pdf",
                    "mime_type": "application/pdf",
                }
            )
        )

        assert source is None

    def test_document_without_a_media_type_is_not_guessed_from_the_name(self) -> None:
        """Schemas over heuristics: an unflagged file is not a portrait."""
        source = bouncer.portrait_source(
            self._message(
                document={
                    "file_id": "doc-3",
                    "file_unique_id": "u-doc-3",
                    "file_name": "portrait.png",
                }
            )
        )

        assert source is None

    def test_text_and_caption_messages_are_not_portraits(self) -> None:
        assert bouncer.portrait_source(self._message(text="hello")) is None
        assert bouncer.portrait_source(self._message(caption="my best")) is None


class TestSelectPhotoSize:
    def test_picks_highest_resolution(self) -> None:
        sizes = photo_sizes()
        assert bouncer.select_photo_size(sizes).file_id == "p2-large"

    def test_empty_list_raises(self) -> None:
        with pytest.raises(bouncer.PhotoValidationError):
            bouncer.select_photo_size([])

    def test_single_entry_is_returned(self) -> None:
        size = PhotoSize(file_id="only", file_unique_id="u", width=10, height=10)
        assert bouncer.select_photo_size([size]) is size


class TestPromptGuardrails:
    def test_prompt_forces_specific_in_character_rejections(self) -> None:
        """The prompt is the personality contract (requirements.md)."""
        prompt = bouncer.BOUNCER_PROMPT.lower()
        assert "exactly one" in prompt and "human" in prompt
        assert "photograph" in prompt
        assert "cartoon" in prompt and "drawing" in prompt
        assert "two sentences" in prompt
        assert "nice ride" in prompt  # Honda-Civic-style content-specific quips
        assert "never say something generic" in prompt

    def test_schema_reason_enum_matches_the_model_enum(self) -> None:
        from telegram_documentaries.models import RejectionReason

        enum = bouncer.VERDICT_SCHEMA["properties"]["reason"]["enum"]
        assert enum == [reason.value for reason in RejectionReason]
