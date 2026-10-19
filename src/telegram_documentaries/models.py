"""Typed Pydantic models for Telegram updates and session state (boundaries).

All external Telegram payloads must be validated into these typed models
at the boundary (TECH.md). Never pass raw dicts across module boundaries.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

# Chat type literals as used by Telegram API.
ChatType = str


class Phase(StrEnum):
    """Session phase states (see TECH.md)."""

    AWAITING_PHOTO = "AWAITING_PHOTO"
    BOUNCER_CHECK = "BOUNCER_CHECK"
    AWAITING_INTERVIEW = "AWAITING_INTERVIEW"
    INTERVIEW = "INTERVIEW"
    CONVERT = "CONVERT"
    SCRIPT = "SCRIPT"
    NARRATE = "NARRATE"
    DONE = "DONE"


class RejectionReason(StrEnum):
    """Reasons why a photo is rejected by the bouncer.

    The enum reason guarantees the message can never dodge explaining why.
    """

    NOT_SOLO_HUMAN = "NOT_SOLO_HUMAN"
    NOT_A_REAL_PHOTO = "NOT_A_REAL_PHOTO"
    UNCLEAR_SUBJECT = "UNCLEAR_SUBJECT"


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


# Session state models (per-chat_id)


class SessionState(BaseModel):
    """Typed per-chat_id session state.

    The schema is versioned; this is v1.0. Contains the current phase,
    any stored photo reference (file_id and local path as stored), and
    provides a clear operation to reset state.
    """

    chat_id: int
    phase: Phase = Phase.AWAITING_PHOTO
    photo_file_id: str | None = None
    photo_local_path: str | None = None
    temp_media_path: str | None = None

    model_config = {
        "extra": "ignore",
        "frozen": False,
    }

    def clear(self) -> None:
        """Clear partial state, resetting to AWAITING_PHOTO with no media refs."""
        self.phase = Phase.AWAITING_PHOTO
        self.photo_file_id = None
        self.photo_local_path = None
        self.temp_media_path = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for debugging/logging without leaking bytes."""
        return self.model_dump(exclude_none=True)


class BouncerVerdict(BaseModel):
    """Verdict from the bouncer vision check.

    Contracts (typed, at the boundary):
    - accepted: bool
    - observed_subject: str (what the model actually sees)
    - reason: RejectionReason enum (when not accepted)
    - message: str (the quip/confirmation; ≤2 sentences)
    """

    accepted: bool
    observed_subject: str
    reason: RejectionReason | None = None
    message: str

    model_config = {
        "extra": "ignore",
    }

    @field_validator("observed_subject")
    @classmethod
    def validate_observed_subject(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise TypeError("observed_subject must be a non-empty string")
        return v.strip()

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise TypeError("message must be a non-empty string")
        return v.strip()

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: RejectionReason | None) -> RejectionReason | None:
        if v is None:
            return v
        if not isinstance(v, RejectionReason):
            # Also accept from string values? Pydantic v2 will coerce; enforce type
            raise TypeError("reason must be a RejectionReason")
        return v

    @model_validator(mode="after")
    def validate_reason_consistency(self) -> BouncerVerdict:
        """A rejected verdict must name why; an accepted one carries no reason.

        This is the contract guarantee that a rejection message can never
        dodge explaining the reason (spec: the enum reason forces it).
        """
        if not self.accepted and self.reason is None:
            raise ValueError("a rejected verdict must carry a reason")
        if self.accepted and self.reason is not None:
            raise ValueError("an accepted verdict must not carry a reason")
        return self
