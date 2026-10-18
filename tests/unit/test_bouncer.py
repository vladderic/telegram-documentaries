"""Unit tests for the Bouncer's photo fetch/validate logic (Phase 2).

No test contacts the real Telegram API: the photo source is a stub and
download failures reuse the typed Telegram errors from the gateway.
"""

from __future__ import annotations

import logging

import pytest

from telegram_documentaries import bot_gateway, bouncer
from telegram_documentaries.models import PhotoSize


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


class TestFetchPhoto:
    @pytest.mark.asyncio
    async def test_downloads_the_largest_photo_size(self) -> None:
        source = StubSource(data=b"LARGEST-BYTES")

        result = await bouncer.fetch_photo(source, photo_sizes())

        assert result == b"LARGEST-BYTES"
        assert source.requested == ["p2-large"]

    @pytest.mark.asyncio
    async def test_empty_photo_list_fails_loudly(self) -> None:
        source = StubSource()

        with pytest.raises(bouncer.PhotoValidationError):
            await bouncer.fetch_photo(source, [])

        assert source.requested == []

    @pytest.mark.asyncio
    async def test_oversized_by_metadata_never_downloads(self) -> None:
        source = StubSource()
        oversized = bouncer.MAX_PHOTO_BYTES + 1

        with pytest.raises(bouncer.PhotoValidationError):
            await bouncer.fetch_photo(source, photo_sizes(file_size=oversized))

        # Edge guard: untrusted input is checked *before* any transfer.
        assert source.requested == []

    @pytest.mark.asyncio
    async def test_oversized_actual_bytes_fail_after_download(self) -> None:
        # Metadata missing/lying (untrusted): the real size must still be checked.
        source = StubSource(data=b"x" * (bouncer.MAX_PHOTO_BYTES + 1))

        with pytest.raises(bouncer.PhotoValidationError):
            await bouncer.fetch_photo(source, photo_sizes(file_size=None))

    @pytest.mark.asyncio
    async def test_download_failure_propagates_as_typed_error(self) -> None:
        source = StubSource(error=bot_gateway.TelegramApiError("getFile failed"))

        with pytest.raises(bot_gateway.TelegramApiError):
            await bouncer.fetch_photo(source, photo_sizes())

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
            await bouncer.fetch_photo(source, photo_sizes())

        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ]
        assert any("fetch_photo" in message for message in warnings)
        assert any("TelegramApiError" in message for message in warnings)


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
