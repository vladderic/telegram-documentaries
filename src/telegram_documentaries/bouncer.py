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
import json
import logging
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from telegram_documentaries.config import REDACTED
from telegram_documentaries.logging_utils import log_async_call
from telegram_documentaries.models import BouncerVerdict, PhotoSize

logger = logging.getLogger(__name__)

#: Vision model for the gate (constitution: Gemini 3.1 Flash Lite).
BOUNCER_MODEL = "gemini-3.1-flash-lite"
GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{BOUNCER_MODEL}:generateContent"
)

#: Hard edge limit for an uploaded photo (validated before *and* after
#: download — untrusted input, TECH.md).
MAX_PHOTO_BYTES = 10 * 1024 * 1024

#: Guardrails prompt: acceptance bar, medium bar, personality rules.
BOUNCER_PROMPT = """\
You are the Bouncer at an exclusive wildlife-documentary studio. You decide \
which photographs are allowed inside for a documentary shoot.

ACCEPT only when the image is a photograph of exactly one real human, clearly \
the main subject.

REJECT everything else: group shots or scenes where a single human is not the \
clear subject; animals, cars, objects or landscapes; drawings, cartoons, \
anime, illustrations, paintings; and obviously synthetic or artistic \
renderings - only real photographs of real people pass.

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
- reason: one of NOT_SOLO_HUMAN (no single human main subject), \
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


class GeminiApiError(Exception):
    """Gemini API failure (typed; the message never contains the API key)."""


class PhotoValidationError(Exception):
    """The uploaded photo cannot be processed (size/shape edge guard)."""


class PhotoSource(Protocol):
    """Minimal interface for fetching Telegram file bytes (no import cycle)."""

    async def download_file(self, file_id: str) -> bytes:
        """Return the raw bytes for a Telegram file."""
        ...


class GeminiVisionClient:
    """Gemini vision client producing typed :class:`BouncerVerdict` verdicts."""

    def __init__(
        self,
        api_key: str,
        base_url: str = GEMINI_GENERATE_URL,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()

    def _sanitize(self, text: str) -> str:
        """Redact the API key from any text destined for logs/exceptions."""
        if not self.api_key:
            return text
        return text.replace(self.api_key, REDACTED)

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
        payload = {
            "contents": [
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
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": VERDICT_SCHEMA,
            },
        }
        # Auth travels as a header so it can never leak into a URL-bearing
        # error message (mirrors the Telegram client's token hygiene).
        headers = {"x-goog-api-key": self.api_key}
        try:
            response = await self._client.post(
                self.base_url, json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            detail = self._sanitize(str(exc))
            # `from None`: chaining would re-print the original exception,
            # whose message could embed request details, into log output.
            raise GeminiApiError(
                f"Gemini API call failed ({type(exc).__name__}): {detail}"
            ) from None
        if response.is_error:
            body = self._sanitize(response.text[:300])
            raise GeminiApiError(
                f"Gemini API returned HTTP {response.status_code}: {body}"
            ) from None

        try:
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            verdict = BouncerVerdict.model_validate(json.loads(text))
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
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
