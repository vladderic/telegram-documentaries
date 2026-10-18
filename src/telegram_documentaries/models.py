"""Typed Pydantic models for Telegram updates (boundaries).

All external Telegram payloads must be validated into these typed models
at the boundary (TECH.md). Never pass raw dicts across module boundaries.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AliasChoices, BaseModel, Field, RootModel, field_validator

# Chat type literals as used by Telegram API.
ChatType = str


class Chat(BaseModel):
    """Telegram chat model."""

    id: int
    type: ChatType


class User(BaseModel):
    """Telegram user model."""

    id: int
    is_bot: bool
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class PhotoSize(BaseModel):
    """Telegram photo size model (thumbnail/sizes)."""

    file_id: str
    file_unique_id: str
    width: int
    height: int
    file_size: int | None = None


class Message(BaseModel):
    """Telegram message model.

    Note: `from` is an alias for the sender in Telegram API; map it to
    `from_user` to keep Pythonic naming in code while accepting the API shape.
    """

    message_id: int
    date: int
    chat: Chat
    text: str | None = None
    caption: str | None = None
    photo: Annotated[list[PhotoSize] | None, Field(default=None)] = None

    # Map API alias "from" to from_user; also accept "from_user" if present.
    from_user: User | None = Field(
        default=None,
        alias="from",
        validation_alias=AliasChoices("from", "from_user"),
    )

    model_config = {
        "populate_by_name": True,
        "extra": "ignore",  # Be lenient on unknown fields from Telegram.
    }

    @field_validator("text")
    @classmethod
    def validate_text_type(cls, v: str | None) -> str | None:
        """Ensure text is a string if present."""
        if v is not None and not isinstance(v, str):
            raise TypeError("text must be a string")
        return v

    @field_validator("caption")
    @classmethod
    def validate_caption_type(cls, v: str | None) -> str | None:
        """Ensure caption is a string if present."""
        if v is not None and not isinstance(v, str):
            raise TypeError("caption must be a string")
        return v


class Update(BaseModel):
    """Telegram update model.

    Phase 1 models focus on `message` updates; other update kinds must parse
    successfully (extra fields ignored) so the gateway can skip them safely.
    """

    update_id: int
    message: Message | None = None

    model_config = {
        "extra": "ignore",  # Ignore unknown update kinds like edited_message.
    }


class UpdateResult(RootModel[list[Update]]):
    """Root model for getUpdates API result array."""

    root: list[Update]
