"""Tests for typed Pydantic parsing of Telegram update payloads.

Telegram updates are untrusted, arbitrary input (TECH.md): they must be
validated into typed models at the boundary, and malformed payloads must be
rejected loudly rather than passed around as raw dicts.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from telegram_documentaries.models import Update

# Realistic getUpdates "result" entries, trimmed to the fields Phase 1 models.
TEXT_UPDATE: dict[str, object] = {
    "update_id": 9001,
    "message": {
        "message_id": 42,
        "date": 1_760_000_000,
        "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "is_bot": False, "first_name": "Ada"},
        "text": "Hello there",
    },
}

PHOTO_UPDATE: dict[str, object] = {
    "update_id": 9002,
    "message": {
        "message_id": 43,
        "date": 1_760_000_001,
        "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "is_bot": False, "first_name": "Ada"},
        "text": "my best portrait",
        "photo": [
            {
                "file_id": "small",
                "file_unique_id": "u-small",
                "width": 90,
                "height": 53,
            },
            {
                "file_id": "big",
                "file_unique_id": "u-big",
                "width": 800,
                "height": 471,
            },
        ],
    },
}


DOCUMENT_UPDATE = {
    "update_id": 9002,
    "message": {
        "message_id": 8,
        "date": 1_700_000_000,
        "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "is_bot": False, "first_name": "Ada"},
        "document": {
            "file_id": "doc-1",
            "file_unique_id": "u-doc-1",
            "file_name": "portrait.png",
            "mime_type": "image/png",
            "file_size": 2_048_000,
            "thumbnail": {"file_id": "ignored"},
        },
    },
}


class TestParseValidUpdates:
    def test_parses_text_message_update_into_typed_models(self) -> None:
        update = Update.model_validate(TEXT_UPDATE)

        assert update.update_id == 9001
        assert update.message is not None
        assert update.message.text == "Hello there"
        assert update.message.chat.id == 555
        assert update.message.chat.type == "private"

    def test_maps_from_alias_to_from_user(self) -> None:
        update = Update.model_validate(TEXT_UPDATE)

        assert update.message is not None
        assert update.message.from_user is not None
        assert update.message.from_user.first_name == "Ada"

    def test_parses_photo_message_update(self) -> None:
        update = Update.model_validate(PHOTO_UPDATE)

        assert update.message is not None
        assert update.message.photo is not None
        assert [photo.file_id for photo in update.message.photo] == ["small", "big"]
        assert update.message.photo[-1].width == 800

    def test_parses_document_message_update(self) -> None:
        update = Update.model_validate(DOCUMENT_UPDATE)

        assert update.message is not None
        assert update.message.document is not None
        assert update.message.document.file_id == "doc-1"
        assert update.message.document.mime_type == "image/png"
        assert update.message.document.file_size == 2_048_000

    def test_document_optional_fields_default_to_none(self) -> None:
        update = Update.model_validate(
            {
                "update_id": 9003,
                "message": {
                    "message_id": 9,
                    "date": 1_700_000_001,
                    "chat": {"id": 555, "type": "private"},
                    "document": {"file_id": "d2", "file_unique_id": "u-d2"},
                },
            }
        )

        assert update.message is not None
        assert update.message.document is not None
        assert update.message.document.mime_type is None
        assert update.message.document.file_name is None
        assert update.message.document.file_size is None

    def test_photo_message_has_no_document(self) -> None:
        update = Update.model_validate(PHOTO_UPDATE)

        assert update.message is not None
        assert update.message.document is None

    def test_unknown_update_kind_yields_none_message(self) -> None:
        """Telegram sends update kinds we do not model yet (e.g. edited_message).

        They must parse without error (extra fields ignored) so the gateway
        can log and skip them instead of crashing.
        """
        update = Update.model_validate(
            {"update_id": 1, "edited_message": {"message_id": 7, "whatever": 1}}
        )

        assert update.message is None


class TestRejectMalformedUpdates:
    def test_message_without_chat_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Update.model_validate(
                {"update_id": 1, "message": {"message_id": 1, "date": 0}}
            )

    def test_message_with_wrong_text_type_is_rejected(self) -> None:
        """`text` must be a string — Telegram input is untrusted."""
        payload = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 0,
                "chat": {"id": 1, "type": "private"},
                "text": 12345,
            },
        }

        with pytest.raises(ValidationError):
            Update.model_validate(payload)

    def test_non_mapping_payload_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Update.model_validate(["not", "an", "update"])
