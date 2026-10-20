"""Shared Gemini ``generateContent`` client (TECH.md boundary).

Both the Bouncer (vision) and the Interviewer (text) talk to Gemini. This
module owns the HTTP plumbing, structured-output configuration and typed error
handling, so each stage keeps only its prompt, schema and typed parse.

Boundary contract (TECH.md): Gemini responses are untrusted and arbitrary. They
are JSON-decoded here and validated into typed Pydantic models by the caller;
a malformed response raises a typed error - it never leaks downstream as a raw
dict. The API key travels as a header (never in the URL) and is redacted from
every error message.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import AliasChoices, BaseModel, Field, ValidationError

from telegram_documentaries.config import REDACTED
from telegram_documentaries.logging_utils import log_async_call

logger = logging.getLogger(__name__)

#: Base for per-model generateContent endpoints (constitution: Gemini 3.1).
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiApiError(Exception):
    """Gemini API failure (typed; the message never contains the API key)."""


class InlineAudio(BaseModel):
    """The inline audio part of a TTS response (untrusted boundary value).

    ``data`` stays base64 exactly as the API sent it; the Narrator decodes it.
    """

    data: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)


class _InlineData(BaseModel):
    """The ``inlineData`` payload: base64 data plus its media type."""

    mime_type: str = Field(validation_alias=AliasChoices("mimeType", "mime_type"))
    data: str = Field(min_length=1)

    model_config = {
        "extra": "ignore",
    }


class _InlineAudioPart(BaseModel):
    """One inline-data part of a Gemini TTS response.

    Requests travel as snake_case while the live API answered with camelCase
    (``inlineData``/``mimeType``); both spellings are accepted so a casing
    difference can never masquerade as "the model returned no audio" (the
    Converter parses the same live shape the same way).
    """

    inline: _InlineData = Field(
        validation_alias=AliasChoices("inlineData", "inline_data")
    )

    model_config = {
        "extra": "ignore",
    }


def generate_url(model: str) -> str:
    """Build the ``generateContent`` endpoint for one model."""
    return f"{GEMINI_API_BASE}/{model}:generateContent"


class GeminiClient:
    """Gemini client with structured output, shared by every pipeline stage."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
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
    async def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST one generateContent request; return the decoded JSON body.

        Owns the auth header (never a URL, so the key can never leak into an
        error message), the sanitised typed error handling and the response
        decoding. Callers differ only in their payload and their parse.

        Raises:
            GeminiApiError: On transport/HTTP failures or a body that is not a
                JSON object. The API key is redacted from every message.
        """
        # Auth travels as a header so it can never leak into a URL-bearing
        # error message (mirrors the Telegram client's token hygiene).
        headers = {"x-goog-api-key": self.api_key}
        try:
            response = await self._client.post(
                generate_url(self.model), json=payload, headers=headers
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
        except ValueError as exc:
            raise GeminiApiError(
                f"Gemini API returned an unexpected response shape "
                f"({type(exc).__name__})"
            ) from None
        if not isinstance(data, dict):
            raise GeminiApiError(
                "Gemini API returned an unexpected response shape (not a JSON object)"
            )
        return data

    async def generate_image(
        self,
        contents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Run one image-generation call and return the response parts.

        Args:
            contents: Gemini ``contents`` array (prompt text, inline media).

        Returns:
            The response parts (unvalidated - the caller finds and validates
            the inline image part).

        Raises:
            GeminiApiError: On transport/HTTP failures or a response with no
                candidate parts. The API key is redacted from every message.
        """
        data = await self._call(
            {
                "contents": contents,
                "generationConfig": {"responseModalities": ["IMAGE"]},
            }
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GeminiApiError(
                f"Gemini API returned an unexpected response shape "
                f"({type(exc).__name__})"
            ) from None
        if not isinstance(parts, list) or not parts:
            raise GeminiApiError(
                "Gemini API returned an unexpected response shape (no parts)"
            )
        return parts

    async def generate(
        self,
        contents: list[dict[str, Any]],
        schema: dict[str, Any],
        *,
        system_instruction: str | None = None,
    ) -> dict[str, Any]:
        """Run one structured-output call and return the decoded JSON object.

        Args:
            contents: Gemini ``contents`` array (prompt text, inline media).
            schema: JSON schema the model must conform to.
            system_instruction: Optional role/behaviour instruction, sent in the
                dedicated ``systemInstruction`` field so the user turn carries
                only the task input. Omitted from the body when ``None`` (the
                API is never sent an empty instruction).

        Returns:
            The decoded JSON object (unvalidated - the caller parses it into
            its own typed model).

        Raises:
            GeminiApiError: On transport/HTTP failures or when the response
                does not contain a JSON object. The API key is redacted from
                every message.
        """
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        if system_instruction is not None:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        data = await self._call(payload)

        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            decoded = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            # A malformed model response is a typed failure, not a crash and
            # never a raw dict passed downstream (TECH.md).
            raise GeminiApiError(
                f"Gemini API returned an unexpected response shape "
                f"({type(exc).__name__})"
            ) from None
        if not isinstance(decoded, dict):
            raise GeminiApiError(
                "Gemini API returned an unexpected response shape (not a JSON object)"
            )
        return decoded

    async def generate_audio(
        self,
        text: str,
        *,
        voice: str,
    ) -> InlineAudio:
        """Run one TTS call and return the inline audio part (Phase 6).

        The text is sent verbatim: the TTS model reads exactly what it is given,
        and cannot be styled out-of-band (no ``speech_metadata``, no system
        instruction - both are rejected for this model). A style prefix in the
        prompt is spoken aloud, so none is added here. The client is
        constructed with the TTS model as its ``model``.

        Args:
            text: The narration, exactly as delivered to the user.
            voice: Prebuilt voice name.

        Returns:
            The validated inline audio (base64 payload + declared media type).

        Raises:
            GeminiApiError: On transport/HTTP failures or a response without a
                usable inline audio part. The API key is redacted from every
                message.
        """
        data = await self._call(
            {
                "contents": [{"parts": [{"text": text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
                    },
                },
            }
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GeminiApiError(
                f"Gemini API returned an unexpected TTS response shape "
                f"({type(exc).__name__})"
            ) from None
        if not isinstance(parts, list):
            raise GeminiApiError(
                "Gemini API returned an unexpected TTS response shape (no parts)"
            )
        # Scan every part: the model may prepend text (or anything else), so
        # the audio need not be parts[0]. Everything is validated into typed
        # models at this boundary (TECH.md) - a raw dict never crosses it.
        for part in parts:
            try:
                inline = _InlineAudioPart.model_validate(part)
            except ValidationError:
                continue  # a text part, or a part we cannot read: keep looking
            try:
                return InlineAudio(
                    data=inline.inline.data, mime_type=inline.inline.mime_type
                )
            except ValidationError as exc:
                # An empty (or otherwise invalid) payload is a typed failure,
                # never a raw pydantic error escaping the documented
                # GeminiApiError contract (mirrors the Converter).
                raise GeminiApiError(
                    f"Gemini API returned an unexpected TTS response shape "
                    f"({type(exc).__name__})"
                ) from None
        raise GeminiApiError("Gemini API returned no inline audio part")
