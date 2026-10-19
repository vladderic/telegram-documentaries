"""Typed Pydantic models for Telegram updates and session state (boundaries).

All external Telegram payloads must be validated into these typed models
at the boundary (TECH.md). Never pass raw dicts across module boundaries.
"""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    RootModel,
    ValidationInfo,
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


class Document(BaseModel):
    """Telegram document (file) model.

    A portrait sent "as a file" arrives here, uncompressed: that is the whole
    point of Phase 4b. Only the fields the pipeline needs are modelled.
    """

    file_id: str
    file_unique_id: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None

    model_config = {"extra": "ignore"}


class PortraitSource(BaseModel):
    """A message's eligible portrait upload (photo *or* image document).

    Derived, never parsed from Telegram directly: it is the typed answer to
    "is this message a portrait, and what is it?" so that the router, the
    guards and the Bouncer intake all share one rule.
    """

    file_id: str
    mime_type: str
    file_size: int | None = None
    #: How it was uploaded: a Telegram photo (compressed) or a file
    #: (original bytes). Logged so live runs are diagnosable.
    kind: Literal["photo", "document"]


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
    document: Document | None = None

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
            raise ValueError("text must be a string")
        return v

    @field_validator("caption")
    @classmethod
    def validate_caption_type(cls, v: str | None) -> str | None:
        """Ensure caption is a string if present."""
        if v is not None and not isinstance(v, str):
            raise ValueError("caption must be a string")
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


# Interview contracts and state (Phase 3). Defined before SessionState so the
# nested default factory can reference InterviewState directly.


class InterviewAction(StrEnum):
    """What the model decided after reading the transcript."""

    ASK = "ASK"
    FINISH = "FINISH"


class InterviewAnswer(BaseModel):
    """One completed question/answer turn (both fields non-empty)."""

    question: str
    answer: str

    model_config = {
        "extra": "ignore",
    }

    @field_validator("question")
    @classmethod
    def validate_question(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("question must be a non-empty string")
        return v.strip()

    @field_validator("answer")
    @classmethod
    def validate_answer(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("answer must be a non-empty string")
        return v.strip()


class InterviewStep(BaseModel):
    """The model's decision: ask the next question, or finish.

    The consistency validator is the contract guarantee: an ASK step carries
    exactly a question, a FINISH step carries exactly the profile, the animal
    and its one-line description. A malformed model response can never leak
    downstream as a raw dict.
    """

    action: InterviewAction
    question: str | None = None
    profile: str | None = None
    suggested_animal: str | None = None
    animal_description: str | None = None

    model_config = {
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def validate_action_consistency(self) -> InterviewStep:
        if self.action is InterviewAction.ASK:
            if not isinstance(self.question, str) or not self.question.strip():
                raise ValueError("an ASK step must carry a non-empty question")
            if (
                self.profile is not None
                or self.suggested_animal is not None
                or self.animal_description is not None
            ):
                raise ValueError("an ASK step must not carry profile fields")
        else:  # FINISH
            if not isinstance(self.profile, str) or not self.profile.strip():
                raise ValueError("a FINISH step must carry a non-empty profile")
            if (
                not isinstance(self.suggested_animal, str)
                or not self.suggested_animal.strip()
            ):
                raise ValueError("a FINISH step must carry a non-empty animal")
            if (
                not isinstance(self.animal_description, str)
                or not self.animal_description.strip()
            ):
                raise ValueError("a FINISH step must carry a non-empty description")
            if self.question is not None:
                raise ValueError("a FINISH step must not carry a question")
        return self

    def to_profile(self) -> BehaviouralProfile | None:
        """The profile of a FINISH step, or ``None`` when this step is not one.

        Typed accessor so callers never have to narrow ``profile`` /
        ``suggested_animal`` / ``animal_description`` themselves; the
        consistency validator above guarantees the fields are present when the
        action is FINISH.
        """
        if self.action is not InterviewAction.FINISH:
            return None
        if (
            self.profile is None
            or self.suggested_animal is None
            or self.animal_description is None
        ):
            return None  # unreachable past the validator - never trust blindly
        return BehaviouralProfile(
            profile=self.profile,
            suggested_animal=self.suggested_animal,
            animal_description=self.animal_description,
        )


class BehaviouralProfile(BaseModel):
    """The Interviewer's output: the dossier that fuels later stages.

    ``suggested_animal`` is a bare species name (Phase 4 feeds it to the image
    model), while ``animal_description`` is the in-character flavour line the
    gateway shows the user - two different jobs, two different fields.
    """

    profile: str
    suggested_animal: str
    animal_description: str

    model_config = {
        "extra": "ignore",
    }

    @field_validator("profile", "suggested_animal", "animal_description")
    @classmethod
    def validate_non_empty(cls, v: str, info: ValidationInfo) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return v.strip()


class GeneratedImage(BaseModel):
    """The Converter's output: one image, ready to upload to Telegram.

    The Gemini response carries the bytes base64-encoded; they are decoded here,
    at the boundary, so no base64 text ever travels past it.
    """

    mime_type: str
    data: bytes

    model_config = {
        "extra": "ignore",
    }

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("mime_type must be a non-empty string")
        mime = v.strip().lower()
        if not mime.startswith("image/"):
            raise ValueError("mime_type must be an image type")
        return mime

    @field_validator("data", mode="before")
    @classmethod
    def decode_data(cls, v: Any) -> bytes:
        if isinstance(v, str):
            try:
                # Whitespace never carries meaning in base64; some encoders wrap
                # long payloads, and a hard failure there would look like a
                # missing image rather than a formatting quirk.
                v = base64.b64decode("".join(v.split()), validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("data must be valid base64") from None
        if not isinstance(v, bytes) or not v:
            raise ValueError("data must be non-empty bytes")
        return v

    @classmethod
    def from_base64(cls, mime_type: str, data: str) -> GeneratedImage:
        """Build an image from the wire form (base64 text, as Gemini sends it).

        The decode rules live in the validator above and nowhere else; this is
        only the type-honest door for base64 callers.
        """
        return cls.model_validate({"mime_type": mime_type, "data": data})


class Script(BaseModel):
    """The Scripter's output: the narration, ready for the microphone.

    The validator is the contract guarantee: a ``Script`` is always exactly one
    paragraph - every whitespace run, line breaks included, collapses to a
    single space - so no caller has to re-normalise the narrator's text and
    Phase 6 can synthesise it as-is. The narrator's words are never rewritten:
    the prompt is the style lever, and over-length text is logged, not cut.
    """

    text: str

    model_config = {
        "extra": "ignore",
    }

    @field_validator("text")
    @classmethod
    def validate_single_paragraph(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("text must be a non-empty string")
        return " ".join(v.split())


class InterviewState(BaseModel):
    """Typed interview progress for one chat.

    Single source of truth: the question a user is answering is derived from
    ``len(answers) + 1`` and "finished" is derived from a stored profile - no
    redundant counters that can drift.
    """

    answers: list[InterviewAnswer] = Field(default_factory=list)
    pending_question: str | None = None
    profile: BehaviouralProfile | None = None
    #: Unix wall-clock seconds when ``pending_question`` reached the user, or
    #: ``None`` while nothing is pending. Used to reject messages that predate
    #: the question (live finding: a message already queued when the question
    #: was sent got recorded as the answer 0.4s later).
    question_sent_at: float | None = None

    model_config = {
        "extra": "ignore",
    }

    @property
    def next_question_number(self) -> int:
        """The 1-based number of the question that will be asked next."""
        return len(self.answers) + 1

    @property
    def is_finished(self) -> bool:
        """True once the profile has been produced."""
        return self.profile is not None


class SessionState(BaseModel):
    """Typed per-chat_id session state.

    The schema is versioned; this is v1.3 (adds the Phase 6 voice-note flag).
    Contains the current phase, any stored photo reference (file_id and local
    path as stored), the interview progress, the narration once written, and
    provides a clear operation to reset state.
    """

    chat_id: int
    phase: Phase = Phase.AWAITING_PHOTO
    photo_file_id: str | None = None
    photo_local_path: str | None = None
    #: Media type of the stored portrait (Phase 4b): a photo is always JPEG,
    #: a document keeps whatever Telegram declared. Never guessed at the
    #: Converter call site.
    photo_mime_type: str = "image/jpeg"
    temp_media_path: str | None = None
    interview: InterviewState = Field(default_factory=InterviewState)
    #: The narration actually sent to the user (Phase 5). Phase 6 synthesises
    #: the voice note from these very words, so no second model call can make
    #: the audio say something the user never read.
    script_text: str | None = None
    #: Whether the voice note for this session has been delivered (Phase 6):
    #: one narration means exactly one voice note, no matter how many late
    #: messages arrive.
    voice_note_sent: bool = False

    model_config = {
        "extra": "ignore",
        "frozen": False,
    }

    def clear(self) -> None:
        """Clear partial state, resetting to AWAITING_PHOTO with no media refs."""
        self.phase = Phase.AWAITING_PHOTO
        self.photo_file_id = None
        self.photo_local_path = None
        self.photo_mime_type = "image/jpeg"
        self.temp_media_path = None
        self.script_text = None
        self.voice_note_sent = False
        self.interview = InterviewState()

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
            raise ValueError("observed_subject must be a non-empty string")
        return v.strip()

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("message must be a non-empty string")
        return v.strip()

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: RejectionReason | None) -> RejectionReason | None:
        if v is None:
            return v
        if not isinstance(v, RejectionReason):
            # Also accept from string values? Pydantic v2 will coerce; enforce type
            raise ValueError("reason must be a RejectionReason")
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
