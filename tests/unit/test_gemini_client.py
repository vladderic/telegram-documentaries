"""Unit tests for the shared Gemini generateContent client (TECH.md boundary).

The real Gemini API is never contacted: responses are mocked with
httpx.MockTransport. All keys below are placeholders.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from telegram_documentaries.gemini import GeminiApiError, GeminiClient, generate_url

PLACEHOLDER_API_KEY = "PLACEHOLDER-GEMINI-API-KEY"
TEST_MODEL = "gemini-test-model"

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}

CONTENTS: list[dict[str, Any]] = [
    {"role": "user", "parts": [{"text": "say something"}]},
]

Handler = Callable[[httpx.Request], httpx.Response]


def gemini_response(payload: Any) -> dict[str, Any]:
    """Wrap a JSON payload in a realistic Gemini generateContent envelope."""
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": json.dumps(payload)}],
                },
                "finishReason": "STOP",
            }
        ],
        "modelVersion": TEST_MODEL,
    }


def make_client(handler: Handler) -> GeminiClient:
    return GeminiClient(
        api_key=PLACEHOLDER_API_KEY,
        model=TEST_MODEL,
        transport=httpx.MockTransport(handler),
    )


class TestGenerate:
    @pytest.mark.asyncio
    async def test_sends_structured_request_with_header_auth(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=gemini_response({"answer": "ok"}))

        client = make_client(handler)
        try:
            await client.generate(CONTENTS, SCHEMA)
        finally:
            await client.close()

        request = seen[0]
        assert request.url.path.endswith(f"/models/{TEST_MODEL}:generateContent")
        # Auth via header, never the URL (a URL key could leak into errors).
        assert request.headers["x-goog-api-key"] == PLACEHOLDER_API_KEY
        assert PLACEHOLDER_API_KEY not in str(request.url)

        body = json.loads(request.content)
        assert body["contents"] == CONTENTS
        gen = body["generationConfig"]
        assert gen["responseMimeType"] == "application/json"
        assert gen["responseSchema"] == SCHEMA

    @pytest.mark.asyncio
    async def test_returns_the_decoded_json_object(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=gemini_response({"answer": "42"}))

        client = make_client(handler)
        try:
            result = await client.generate(CONTENTS, SCHEMA)
        finally:
            await client.close()

        assert result == {"answer": "42"}

    @pytest.mark.asyncio
    async def test_http_error_is_typed_and_sanitised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                500, json={"error": {"message": "boom", "key": PLACEHOLDER_API_KEY}}
            )

        client = make_client(handler)
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(GeminiApiError) as exc_info:
                await client.generate(CONTENTS, SCHEMA)
            await client.close()

        assert "500" in str(exc_info.value)
        assert PLACEHOLDER_API_KEY not in str(exc_info.value)
        assert PLACEHOLDER_API_KEY not in caplog.text

    @pytest.mark.asyncio
    async def test_transport_error_is_typed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("gemini took too long")

        client = make_client(handler)
        with pytest.raises(GeminiApiError) as exc_info:
            await client.generate(CONTENTS, SCHEMA)
        await client.close()

        assert "ReadTimeout" in str(exc_info.value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response_body",
        [
            pytest.param({"candidates": []}, id="no-candidates"),
            pytest.param({"candidates": [{"content": {"parts": []}}]}, id="no-parts"),
            pytest.param(
                {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
                id="not-json",
            ),
            pytest.param(
                {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]},
                id="json-is-not-an-object",
            ),
            pytest.param({"unexpected": True}, id="missing-candidates"),
        ],
    )
    async def test_untrusted_response_shapes_raise_typed_error(
        self, response_body: dict[str, Any]
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response_body)

        client = make_client(handler)
        with pytest.raises(GeminiApiError):
            await client.generate(CONTENTS, SCHEMA)
        await client.close()


class TestGenerateUrl:
    def test_builds_the_model_endpoint(self) -> None:
        assert generate_url(TEST_MODEL) == (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{TEST_MODEL}:generateContent"
        )
