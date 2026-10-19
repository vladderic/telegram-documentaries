"""The Converter: fuses the portrait and the dossier into one hybrid image.

Input: the accepted reference photo plus the Interviewer's typed
:class:`~telegram_documentaries.models.BehaviouralProfile`. Output: exactly one
:class:`~telegram_documentaries.models.GeneratedImage`, ready to upload to
Telegram.

The model and payload were fixed by a live call against the real API: the
``generateContent`` request carries the prompt text and the photo as inline
data, ``responseModalities: ["IMAGE"]`` asks for an image back, and the bytes
arrive base64-encoded in the candidate's parts (camelCase ``inlineData``).
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from telegram_documentaries.gemini import GeminiApiError, GeminiClient
from telegram_documentaries.logging_utils import log_async_call
from telegram_documentaries.models import BehaviouralProfile, GeneratedImage

logger = logging.getLogger(__name__)

#: Gemini image model (verified live: returns the fused JPEG in ~8s).
CONVERTER_MODEL = "gemini-3.1-flash-image"

#: Image generation is slower than the text stages; a stalled call must not
#: hold the poll loop forever.
IMAGE_TIMEOUT_SECONDS = 60.0

CONVERTER_PERSONA = (
    "You are the film crew's stills photographer, shooting one portrait for a "
    "wildlife documentary."
)

#: Wording chosen in a three-variant live experiment (2026-10-19): the "current
#: rules" and "likeness first" framings produced crude composites, so the rules
#: now frame the work as one real photograph with the person's face intact and
#: the animal's traits added around it.
CONVERTER_RULES = (
    "Shoot a single professional wildlife portrait photograph on an 85mm "
    "portrait lens.\n"
    "- One believable hybrid creature: the person in the reference photo, with "
    "their head, face and expression intact - the same facial features, "
    "hairstyle, glasses and skin.\n"
    "- Add the species' ears, fur texture, eye colour and colouring around the "
    "face and shoulders, and borrow its habits for the pose and setting.\n"
    "- Natural-history still: soft window light, shallow depth of field, fine "
    "skin and fur detail, seamless anatomy.\n"
    "- Exactly one subject, centred, looking towards the camera.\n"
    "- No text, no caption, no watermark, no border or frame, no collage, no "
    "composite artifacts.\n"
    "- Return the image itself and nothing else."
)


def build_prompt(profile: BehaviouralProfile) -> str:
    """Build the Converter prompt from the typed dossier (pure, testable)."""
    return (
        f"{CONVERTER_PERSONA}\n\n"
        f"{CONVERTER_RULES}\n\n"
        f"Species: {profile.suggested_animal}\n"
        f"Crew's note on that animal: {profile.animal_description}\n\n"
        f"Dossier on the subject:\n{profile.profile}"
    )


def _contents(prompt: str, portrait: bytes, mime_type: str) -> list[dict[str, Any]]:
    """Wrap the prompt and the reference photo into a Gemini ``contents`` array."""
    return [
        {
            "role": "user",
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(portrait).decode("ascii"),
                    }
                },
            ],
        }
    ]


class _InlineData(BaseModel):
    """The ``inlineData`` payload: base64 bytes plus their media type."""

    mime_type: str = Field(validation_alias=AliasChoices("mimeType", "mime_type"))
    data: str

    model_config = {
        "extra": "ignore",
    }


class _InlineImagePart(BaseModel):
    """One inline-data part of a Gemini image response.

    Requests travel as snake_case while the live API answered with camelCase
    (``inlineData``/``mimeType``); both spellings are accepted so a casing
    difference can never masquerade as "the model returned no image".
    """

    inline: _InlineData = Field(
        validation_alias=AliasChoices("inlineData", "inline_data")
    )

    model_config = {
        "extra": "ignore",
    }


class ConverterClient:
    """Gemini image client producing the Converter's typed output."""

    def __init__(
        self,
        api_key: str,
        model: str = CONVERTER_MODEL,
        timeout: float = IMAGE_TIMEOUT_SECONDS,
        transport: Any | None = None,
    ) -> None:
        self._gemini = GeminiClient(
            api_key=api_key,
            model=model,
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._gemini.close()

    @log_async_call(logger)
    async def fuse(
        self,
        portrait: bytes,
        mime_type: str,
        profile: BehaviouralProfile,
    ) -> GeneratedImage:
        """Generate the fused portrait.

        Args:
            portrait: The accepted reference photo's raw bytes.
            mime_type: That photo's media type (from the Telegram upload).
            profile: The Interviewer's typed dossier.

        Returns:
            The typed :class:`GeneratedImage` (decoded bytes + mime type).

        Raises:
            GeminiApiError: On transport/HTTP failures, an unexpected response
                shape, or a response that carries no image part. The API key is
                redacted from every message; image bytes are never logged.
        """
        logger.info(
            "converter_fuse animal=%r portrait_bytes=%s",
            profile.suggested_animal,
            len(portrait),
        )
        parts = await self._gemini.generate_image(
            _contents(build_prompt(profile), portrait, mime_type)
        )
        for part in parts:
            try:
                inline = _InlineImagePart.model_validate(part)
            except ValidationError:
                continue  # a text part, or a part we cannot read: keep looking
            try:
                image = GeneratedImage.from_base64(
                    inline.inline.mime_type, inline.inline.data
                )
            except ValidationError as exc:
                # Bad base64 or a non-image mime is a typed failure, never a
                # half-valid image handed to Telegram.
                raise GeminiApiError(
                    f"Gemini API returned an unexpected image shape "
                    f"({type(exc).__name__})"
                ) from None
            logger.info(
                "converter_image mime_type=%s bytes=%s",
                image.mime_type,
                len(image.data),
            )
            return image
        raise GeminiApiError("Gemini API returned no image part")
