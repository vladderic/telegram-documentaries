"""Unit tests for the Gemini vision client (Bouncer, Phase 2).

The real Gemini API is never contacted: responses are mocked with
httpx.MockTransport and verdicts are parsed into typed Pydantic models at the
boundary (TECH.md). All keys below are placeholders.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from telegram_documentaries.bouncer import GeminiVisionClient
from telegram_documentaries.gemini import GeminiApiError

PLACEHOLDER_API_KEY = "PLACEHOLDER-GEMINI-API-KEY"

IMAGE_BYTES = b"\xff\xd8\xff\xe0fake-jpeg-bytes"

ACCEPT_VERDICT: dict[str, Any] = {
    "accepted": True,
    "observed_subject": "a single person",
    "message": "Go right in, the documentary awaits.",
}

REJECT_VERDICT: dict[str, Any] = {
    "accepted": False,
    "observed_subject": "a Honda Civic",
    "reason": "NOT_SOLO_HUMAN",
    "message": "Nice ride, but I need to see YOU before the shoot.",
}

Handler = Callable[[httpx.Request], httpx.Response]


def gemini_response(verdict: dict[str, Any]) -> dict[str, Any]:
    """Wrap a verdict dict in a realistic Gemini generateContent payload."""
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": json.dumps(verdict)}],
                },
                "finishReason": "STOP",
            }
        ],
        "modelVersion": "gemini-3.1-flash-lite",
    }


def make_client(handler: Handler) -> GeminiVisionClient:
    return GeminiVisionClient(
        api_key=PLACEHOLDER_API_KEY,
        transport=httpx.MockTransport(handler),
    )


class TestCheckPhoto:
    @pytest.mark.asyncio
    async def test_sends_guardrailed_structured_request(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=gemini_response(ACCEPT_VERDICT))

        client = make_client(handler)
        try:
            await client.check_photo(IMAGE_BYTES, mime_type="image/jpeg")
        finally:
            await client.close()

        request = seen[0]
        # Model path matches the constitution (Gemini 3.1 Flash Lite vision).
        assert "gemini-3.1-flash-lite" in request.url.path
        # Auth via header, never the URL (a URL key could leak into errors).
        assert request.headers["x-goog-api-key"] == PLACEHOLDER_API_KEY
        assert PLACEHOLDER_API_KEY not in str(request.url)

        body = json.loads(request.content)
        parts = body["contents"][0]["parts"]
        prompt_text = next(p["text"] for p in parts if "text" in p)
        inline = next(p["inline_data"] for p in parts if "inline_data" in p)
        assert inline["mime_type"] == "image/jpeg"
        assert base64.b64decode(inline["data"]) == IMAGE_BYTES

        # Prompt guardrails: acceptance bar, medium bar, personality rules.
        lowered = prompt_text.lower()
        assert "exactly one" in lowered and "human" in lowered
        assert "photograph" in lowered
        assert "cartoon" in lowered or "drawing" in lowered
        assert "two sentences" in lowered
        assert "reason" in lowered

        # One person shown in part, or holding something, is still ONE subject
        # (live validation: a shoulder-only portrait and a man holding a plush
        # toy were both rejected as if they contained several subjects).
        assert "in part" in lowered
        assert "holding" in lowered
        assert "only people count as subjects" in lowered
        assert "two or more people" in lowered

        # Structured output: JSON mime type + verdict schema.
        gen = body["generationConfig"]
        assert gen["responseMimeType"] == "application/json"
        schema_props = gen["responseSchema"]["properties"]
        assert set(schema_props) == {
            "accepted",
            "observed_subject",
            "reason",
            "message",
        }
        assert schema_props["reason"]["enum"] == [
            "NOT_SOLO_HUMAN",
            "NOT_A_REAL_PHOTO",
            "UNCLEAR_SUBJECT",
        ]

    @pytest.mark.asyncio
    async def test_parses_valid_accept_verdict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=gemini_response(ACCEPT_VERDICT))

        client = make_client(handler)
        try:
            verdict = await client.check_photo(IMAGE_BYTES)
        finally:
            await client.close()

        assert verdict.accepted is True
        assert verdict.observed_subject == "a single person"
        assert verdict.reason is None

    @pytest.mark.asyncio
    async def test_parses_valid_reject_verdict_with_enum_reason(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=gemini_response(REJECT_VERDICT))

        client = make_client(handler)
        try:
            verdict = await client.check_photo(IMAGE_BYTES)
        finally:
            await client.close()

        assert verdict.accepted is False
        assert verdict.reason is not None
        assert verdict.reason.value == "NOT_SOLO_HUMAN"
        assert "Honda Civic" in verdict.observed_subject

    @pytest.mark.asyncio
    async def test_http_error_raises_typed_error_without_leaking_the_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500, json={"error": {"message": "boom", "key": PLACEHOLDER_API_KEY}}
            )

        client = make_client(handler)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(GeminiApiError) as exc_info:
                await client.check_photo(IMAGE_BYTES)
            await client.close()

        assert "500" in str(exc_info.value)
        assert PLACEHOLDER_API_KEY not in str(exc_info.value)
        assert PLACEHOLDER_API_KEY not in caplog.text

    @pytest.mark.asyncio
    async def test_timeout_raises_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("gemini took too long")

        client = make_client(handler)
        with pytest.raises(GeminiApiError) as exc_info:
            await client.check_photo(IMAGE_BYTES)
        await client.close()

        assert "timeout" in str(exc_info.value).lower() or "ReadTimeout" in str(
            exc_info.value
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response_body",
        [
            pytest.param({"candidates": []}, id="no-candidates"),
            pytest.param(
                {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
                id="not-json",
            ),
            pytest.param(
                {"candidates": [{"content": {"parts": [{"text": '{"foo": 1}'}]}}]},
                id="missing-verdict-fields",
            ),
            pytest.param(
                {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]},
                id="wrong-json-shape",
            ),
        ],
    )
    async def test_junk_verdict_response_raises_typed_error(
        self, response_body: dict[str, Any]
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response_body)

        client = make_client(handler)
        with pytest.raises(GeminiApiError):
            await client.check_photo(IMAGE_BYTES)
        await client.close()

    @pytest.mark.asyncio
    async def test_reject_verdict_missing_reason_raises_typed_error(self) -> None:
        """The enum reason guarantees the message cannot dodge explaining why."""
        bad = {**REJECT_VERDICT}
        del bad["reason"]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=gemini_response(bad))

        client = make_client(handler)
        with pytest.raises(GeminiApiError):
            await client.check_photo(IMAGE_BYTES)
        await client.close()
