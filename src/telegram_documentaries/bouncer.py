"""Bouncer - the vision gate for photo verification (Phase 2).

Pipeline stage 1 of 5 (see ``SPECS/ROADMAP.md``): decides whether an
uploaded image may enter the documentary pipeline. Rejections are
*model-written and content-specific* — the quip references what is actually
in the image and always names the real reason (never a generic error line).

All external boundaries (Telegram downloads, Gemini responses) are parsed
into typed Pydantic models here; raw payloads never cross module edges
(TECH.md). Every call is logged via decorators, never with image bytes or
secret values.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from telegram_documentaries.gemini import GeminiApiError, GeminiClient
from telegram_documentaries.logging_utils import log_async_call
from telegram_documentaries.models import (
    BouncerVerdict,
    Message,
    PhotoSize,
    PortraitSource,
)

logger = logging.getLogger(__name__)

#: Vision model for the gate (constitution: Gemini 3.1 Flash Lite).
BOUNCER_MODEL = "gemini-3.1-flash-lite"

#: Hard edge limit for an uploaded portrait (validated before *and* after
#: download — untrusted input, TECH.md). One cap for photos and documents.
MAX_PHOTO_BYTES = 10 * 1024 * 1024

#: Telegram's ``photo`` array is always JPEG; a document carries its own type.
PHOTO_MIME_TYPE = "image/jpeg"

#: Documents are portraits only when Telegram declares an image media type.
IMAGE_MEDIA_TYPE_PREFIX = "image/"

#: Guardrails prompt: acceptance bar, medium bar, personality rules.
BOUNCER_PROMPT = """\
You are the Bouncer at an exclusive wildlife-documentary studio. You decide \
which photographs are allowed inside for a documentary shoot.

ACCEPT when the image is a photograph whose main subject is exactly one real \
human. The person may be shown only in part - a close-up, a side profile, a \
shoulder, a back, an arm - and may be holding or wearing something: that is \
still one subject. Only people count as subjects; objects, pets, toys, \
clothing, furniture, mirrors, shadows and reflections never count as extra \
subjects.

REJECT when the photograph contains two or more people, no person at all, or \
one person who is clearly not the main subject; also reject animals, cars, \
objects or landscapes with no person in them; drawings, cartoons, anime, \
illustrations, paintings; and obviously synthetic or artistic renderings - \
only real photographs of real people pass.

When you REJECT:
- Base the message on what is actually in the image: a car should be \
acknowledged ("Nice ride...") before asking to see the person, an animal \
("Cute, but this isn't Pet Tinder..."). Never say something generic like \
"invalid input" or "no person detected".
- The message must make the real reason clear to the sender.
- Stay in character: witty, theatrical, documentarian charm.
- Maximum two sentences.

When you ACCEPT: a short in-character confirmation, maximum two sentences.

Return JSON only, with fields:
- accepted: boolean
- observed_subject: short description of what you actually see
- reason: one of NOT_SOLO_HUMAN (two or more people, no person at all, or a \
single person who is not the main subject), \
NOT_A_REAL_PHOTO (art/illustration/synthetic, not a photograph), \
UNCLEAR_SUBJECT (cannot tell what it is) - set only when rejecting; omit \
when accepting
- message: your reply as described above\
"""

#: Structured-output schema; the enum forces every rejection to name a reason.
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "accepted": {"type": "boolean"},
        "observed_subject": {"type": "string"},
        "reason": {
            "type": "string",
            "enum": ["NOT_SOLO_HUMAN", "NOT_A_REAL_PHOTO", "UNCLEAR_SUBJECT"],
        },
        "message": {"type": "string"},
    },
    "required": ["accepted", "observed_subject", "message"],
}


class PhotoValidationError(Exception):
    """The uploaded photo cannot be processed (size/shape edge guard)."""


class PhotoSource(Protocol):
    """Minimal interface for fetching Telegram file bytes (no import cycle)."""

    async def download_file(self, file_id: str) -> bytes:
        """Return the raw bytes for a Telegram file."""
        ...


class GeminiVisionClient:
    """Gemini vision client producing typed :class:`BouncerVerdict` verdicts.

    Thin wrapper over the shared :class:`GeminiClient`: it contributes the
    Bouncer's prompt, its structured-output schema and the typed parse.
    """

    def __init__(
        self,
        api_key: str,
        model: str = BOUNCER_MODEL,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
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
    async def check_photo(
        self, image_bytes: bytes, mime_type: str = "image/jpeg"
    ) -> BouncerVerdict:
        """Run the vision gate on one image and return a typed verdict.

        Args:
            image_bytes: Raw image payload (already edge-validated by size).
            mime_type: MIME type of the image.

        Returns:
            The parsed verdict (accepted or a reasoned rejection).

        Raises:
            GeminiApiError: On transport/HTTP failures or an unexpected
                response shape - the API key is redacted from every message.
        """
        contents = [
            {
                "role": "user",
                "parts": [
                    {"text": BOUNCER_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ],
            }
        ]
        data = await self._gemini.generate(contents, VERDICT_SCHEMA)
        try:
            verdict = BouncerVerdict.model_validate(data)
        except ValidationError as exc:
            # A malformed model response is a typed failure, not a crash and
            # never a raw dict passed downstream (TECH.md).
            raise GeminiApiError(
                f"Gemini API returned an unexpected verdict shape "
                f"({type(exc).__name__})"
            ) from None
        logger.info(
            "bouncer_verdict accepted=%s reason=%s subject=%r",
            verdict.accepted,
            verdict.reason.value if verdict.reason else None,
            verdict.observed_subject,
        )
        return verdict


def select_photo_size(photo: list[PhotoSize]) -> PhotoSize:
    """Pick the highest-resolution entry from a Telegram ``photo`` array.

    Raises:
        PhotoValidationError: If the list is empty (untrusted input guard).
    """
    if not photo:
        raise PhotoValidationError("photo message contained no photo sizes")
    return max(photo, key=lambda size: size.width * size.height)


def portrait_source(message: Message) -> PortraitSource | None:
    """Return the portrait upload carried by ``message``, if any.

    The single rule for "is this message a portrait?": a normal photo (always
    JPEG, largest size wins) or a document explicitly declared as an image.
    Documents without a media type are *not* guessed from their file name -
    schemas over heuristics (TECH.md) - and non-image documents are nudged
    exactly like plain text.

    Photo wins if a message somehow carries both (Telegram never sends both).
    """
    if message.photo:
        largest = select_photo_size(message.photo)
        return PortraitSource(
            file_id=largest.file_id,
            mime_type=PHOTO_MIME_TYPE,
            file_size=largest.file_size,
        )
    document = message.document
    if document is None or document.mime_type is None:
        return None
    if not document.mime_type.startswith(IMAGE_MEDIA_TYPE_PREFIX):
        return None
    return PortraitSource(
        file_id=document.file_id,
        mime_type=document.mime_type,
        file_size=document.file_size,
    )


@log_async_call(logger)
async def fetch_photo(source: PhotoSource, photo: list[PhotoSize]) -> bytes:
    """Download the photo from Telegram with edge validation (untrusted input).

    Selects the highest-resolution entry, checks the size limit *before*
    download when metadata is present and again on the actual bytes after.

    Args:
        source: Provider for Telegram file bytes (the gateway's client).
        photo: The Telegram photo-size array from the message.

    Returns:
        The raw image bytes, ready for the vision gate.

    Raises:
        PhotoValidationError: If the photo is missing or exceeds
            :data:`MAX_PHOTO_BYTES`.
        TelegramApiError: On download failures (raised by ``source``).
    """
    largest = select_photo_size(photo)
    if largest.file_size is not None and largest.file_size > MAX_PHOTO_BYTES:
        raise PhotoValidationError(
            f"photo too large: {largest.file_size} bytes > {MAX_PHOTO_BYTES}"
        )
    image_bytes = await source.download_file(largest.file_id)
    if len(image_bytes) > MAX_PHOTO_BYTES:
        raise PhotoValidationError(
            f"photo too large: {len(image_bytes)} bytes > {MAX_PHOTO_BYTES}"
        )
    return image_bytes
