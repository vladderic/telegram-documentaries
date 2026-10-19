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

from telegram_documentaries.config import REDACTED
from telegram_documentaries.logging_utils import log_async_call

logger = logging.getLogger(__name__)

#: Base for per-model generateContent endpoints (constitution: Gemini 3.1).
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiApiError(Exception):
    """Gemini API failure (typed; the message never contains the API key)."""


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
    async def generate(
        self,
        contents: list[dict[str, Any]],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Run one structured-output call and return the decoded JSON object.

        Args:
            contents: Gemini ``contents`` array (prompt text, inline media).
            schema: JSON schema the model must conform to.

        Returns:
            The decoded JSON object (unvalidated - the caller parses it into
            its own typed model).

        Raises:
            GeminiApiError: On transport/HTTP failures or when the response
                does not contain a JSON object. The API key is redacted from
                every message.
        """
        payload = {
            "contents": contents,
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
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
