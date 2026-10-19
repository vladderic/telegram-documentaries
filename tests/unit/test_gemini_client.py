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


class TestSystemInstruction:
    """Phase 5: a stage may speak through the API's own system instruction."""

    @pytest.mark.asyncio
    async def test_sends_the_system_instruction_when_asked(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=gemini_response({"answer": "ok"}))

        client = make_client(handler)
        try:
            await client.generate(CONTENTS, SCHEMA, system_instruction="Be a narrator.")
        finally:
            await client.close()

        body = json.loads(seen[0].content)
        assert body["systemInstruction"] == {"parts": [{"text": "Be a narrator."}]}
        # The user turn keeps carrying only the task input.
        assert body["contents"] == CONTENTS

    @pytest.mark.asyncio
    async def test_omits_the_field_entirely_when_not_asked(self) -> None:
        """Every existing stage must keep sending today's exact body."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=gemini_response({"answer": "ok"}))

        client = make_client(handler)
        try:
            await client.generate(CONTENTS, SCHEMA)
        finally:
            await client.close()

        body = json.loads(seen[0].content)
        assert "systemInstruction" not in body

    @pytest.mark.asyncio
    async def test_still_returns_the_decoded_object(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=gemini_response({"answer": "42"}))

        client = make_client(handler)
        try:
            result = await client.generate(
                CONTENTS, SCHEMA, system_instruction="Be a narrator."
            )
        finally:
            await client.close()

        assert result == {"answer": "42"}


class TestGenerateUrl:
    def test_builds_the_model_endpoint(self) -> None:
        assert generate_url(TEST_MODEL) == (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{TEST_MODEL}:generateContent"
        )


def image_response(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap parts in a realistic Gemini generateContent image envelope."""
    return {
        "candidates": [
            {"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}
        ],
        "modelVersion": TEST_MODEL,
    }


class TestGenerateImage:
    @pytest.mark.asyncio
    async def test_requests_image_modality_and_returns_the_parts(self) -> None:
        seen: list[httpx.Request] = []
        parts = [
            {"inlineData": {"mimeType": "image/jpeg", "data": "cGxhY2Vob2xkZXI="}},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=image_response(parts))

        client = make_client(handler)
        try:
            parts_out = await client.generate_image(CONTENTS)
        finally:
            await client.close()

        body = json.loads(seen[0].content)
        assert body["generationConfig"] == {"responseModalities": ["IMAGE"]}
        assert body["contents"] == CONTENTS
        assert "responseSchema" not in body["generationConfig"]
        assert parts_out == parts

    @pytest.mark.asyncio
    async def test_missing_parts_raise_a_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"candidates": []})

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.generate_image(CONTENTS)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_http_error_is_typed_and_never_leaks_the_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="nope")

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError) as caught:
                await client.generate_image(CONTENTS)
        finally:
            await client.close()

        assert PLACEHOLDER_API_KEY not in str(caught.value)


def audio_response(
    mime_type: str = "audio/l16; rate=24000; channels=1",
) -> dict[str, Any]:
    """Wrap a base64 audio payload in a realistic Gemini TTS envelope."""
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": "cGxhY2Vob2xkZXI=",
                            }
                        }
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "modelVersion": TEST_MODEL,
    }


class TestGenerateAudio:
    @pytest.mark.asyncio
    async def test_sends_the_script_verbatim_with_audio_modality_and_voice(
        self,
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=audio_response())

        client = make_client(handler)
        try:
            audio = await client.generate_audio("The raccoon stirs.", voice="Charon")
        finally:
            await client.close()

        body = json.loads(seen[0].content)
        # The script is the sole content - no style wrapper, no system turn.
        assert body["contents"] == [{"parts": [{"text": "The raccoon stirs."}]}]
        assert "systemInstruction" not in body
        assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
        assert body["generationConfig"]["speechConfig"] == {
            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Charon"}}
        }
        assert audio.data == "cGxhY2Vob2xkZXI="
        assert audio.mime_type == "audio/l16; rate=24000; channels=1"

    @pytest.mark.asyncio
    async def test_http_error_is_typed_and_never_leaks_the_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError) as caught:
                await client.generate_audio("text", voice="Charon")
        finally:
            await client.close()

        assert PLACEHOLDER_API_KEY not in str(caught.value)

    @pytest.mark.asyncio
    async def test_missing_inline_audio_raises_a_typed_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"candidates": []})

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.generate_audio("text", voice="Charon")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_a_text_only_response_is_rejected(self) -> None:
        # A TTS model that answered with text (or an empty part) must fail
        # loudly rather than reach the encoder with nothing.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=gemini_response({"text": ""}))

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.generate_audio("text", voice="Charon")
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_finds_the_audio_after_a_leading_text_part(self) -> None:
        """The model may prepend a text part; the audio need not be parts[0]."""
        response = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "One moment."},
                            {
                                "inlineData": {
                                    "mimeType": "audio/l16; rate=24000; channels=1",
                                    "data": "cGxhY2Vob2xkZXI=",
                                }
                            },
                        ],
                    },
                    "finishReason": "STOP",
                }
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response)

        client = make_client(handler)
        try:
            audio = await client.generate_audio("text", voice="Charon")
        finally:
            await client.close()

        assert audio.data == "cGxhY2Vob2xkZXI="
        assert audio.mime_type == "audio/l16; rate=24000; channels=1"

    @pytest.mark.asyncio
    async def test_accepts_the_snake_case_part_spelling(self) -> None:
        """Casing tolerance: requests are snake_case, the live API camelCase."""
        response = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "inline_data": {
                                    "mime_type": "audio/l16; rate=24000; channels=1",
                                    "data": "cGxhY2Vob2xkZXI=",
                                }
                            }
                        ],
                    },
                    "finishReason": "STOP",
                }
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response)

        client = make_client(handler)
        try:
            audio = await client.generate_audio("text", voice="Charon")
        finally:
            await client.close()

        assert audio.data == "cGxhY2Vob2xkZXI="
        assert audio.mime_type == "audio/l16; rate=24000; channels=1"

    @pytest.mark.asyncio
    async def test_an_inline_part_with_missing_fields_is_rejected(self) -> None:
        response = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"inlineData": {"mimeType": "audio/l16"}}],
                    },
                    "finishReason": "STOP",
                }
            ]
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response)

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.generate_audio("text", voice="Charon")
        finally:
            await client.close()
