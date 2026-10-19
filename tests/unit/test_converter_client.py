"""Unit tests for the Converter client (Phase 4).

The real Gemini API is never contacted: requests go through an
httpx.MockTransport and responses are the shapes observed live from
``gemini-3.1-flash-image`` (an inline base64 image part). Placeholder key only.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from telegram_documentaries.converter import ConverterClient, build_prompt
from telegram_documentaries.gemini import GeminiApiError
from telegram_documentaries.models import BehaviouralProfile, GeneratedImage

PLACEHOLDER_API_KEY = "PLACEHOLDER-GEMINI-API-KEY"
PHOTO_BYTES = b"\xff\xd8\xff\xe0-portrait-bytes"
IMAGE_BYTES = b"\xff\xd8\xff\xe0-fused-portrait-bytes"

PROFILE = BehaviouralProfile(
    profile="The subject patrols the kitchen at midnight, seeking snacks.",
    suggested_animal="Red fox",
    animal_description="A lone hunter of the small hours.",
)

Handler = Callable[[httpx.Request], httpx.Response]


def image_response(
    parts: list[dict[str, Any]], model: str = "gemini-3.1-flash-image"
) -> dict[str, Any]:
    """Wrap response parts the way the live API did (camelCase inlineData)."""
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": parts},
                "finishReason": "STOP",
            }
        ],
        "modelVersion": model,
    }


def inline_part(data: bytes = IMAGE_BYTES, mime: str = "image/jpeg") -> dict[str, Any]:
    return {
        "inlineData": {
            "mimeType": mime,
            "data": base64.b64encode(data).decode("ascii"),
        }
    }


def make_client(handler: Handler) -> ConverterClient:
    return ConverterClient(
        api_key=PLACEHOLDER_API_KEY,
        transport=httpx.MockTransport(handler),
    )


def request_body(seen: list[httpx.Request]) -> dict[str, Any]:
    return json.loads(seen[0].content)


class TestBuildPrompt:
    def test_names_the_species_and_keeps_the_likeness(self) -> None:
        prompt = build_prompt(PROFILE)

        assert "Red fox" in prompt
        assert "same person" in prompt.lower()
        assert "likeness" in prompt.lower()

    def test_carries_the_dossier_and_the_crews_brief(self) -> None:
        prompt = build_prompt(PROFILE)

        assert PROFILE.profile in prompt
        assert PROFILE.animal_description in prompt

    def test_separates_the_species_from_its_description(self) -> None:
        """The animal must not be described *to* the model as a sentence."""
        prompt = build_prompt(PROFILE)
        species_line = next(
            line for line in prompt.splitlines() if line.startswith("Species:")
        )

        assert "Red fox" in species_line
        assert PROFILE.animal_description not in species_line

    def test_forbids_text_watermarks_and_multiple_subjects(self) -> None:
        prompt = build_prompt(PROFILE).lower()

        assert "no text" in prompt
        assert "watermark" in prompt
        assert "one subject" in prompt


class TestFuse:
    @pytest.mark.asyncio
    async def test_returns_the_typed_image_and_sends_the_photo(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=image_response([inline_part()]))

        client = make_client(handler)
        try:
            image = await client.fuse(PHOTO_BYTES, "image/jpeg", PROFILE)
        finally:
            await client.close()

        assert isinstance(image, GeneratedImage)
        assert image.mime_type == "image/jpeg"
        assert image.data == IMAGE_BYTES

        body = request_body(seen)
        assert body["generationConfig"] == {"responseModalities": ["IMAGE"]}
        parts = body["contents"][0]["parts"]
        assert PROFILE.suggested_animal in parts[0]["text"]
        assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
        assert base64.b64decode(parts[1]["inline_data"]["data"]) == PHOTO_BYTES

    @pytest.mark.asyncio
    async def test_accepts_a_snake_case_inline_part(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=image_response(
                    [
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": base64.b64encode(IMAGE_BYTES).decode(),
                            }
                        }
                    ]
                ),
            )

        client = make_client(handler)
        try:
            image = await client.fuse(PHOTO_BYTES, "image/jpeg", PROFILE)
        finally:
            await client.close()

        assert image.mime_type == "image/png"
        assert image.data == IMAGE_BYTES

    @pytest.mark.asyncio
    async def test_skips_text_parts_and_finds_the_image(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=image_response([{"text": "Here you go"}, inline_part()]),
            )

        client = make_client(handler)
        try:
            image = await client.fuse(PHOTO_BYTES, "image/jpeg", PROFILE)
        finally:
            await client.close()

        assert image.data == IMAGE_BYTES

    @pytest.mark.asyncio
    async def test_a_response_without_an_image_is_a_typed_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=image_response([{"text": "cannot"}]))

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.fuse(PHOTO_BYTES, "image/jpeg", PROFILE)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_junk_base64_is_a_typed_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=image_response(
                    [{"inlineData": {"mimeType": "image/jpeg", "data": "!!"}}]
                ),
            )

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.fuse(PHOTO_BYTES, "image/jpeg", PROFILE)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_http_failure_is_typed_and_never_leaks_the_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="quota")

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError) as caught:
                await client.fuse(PHOTO_BYTES, "image/jpeg", PROFILE)
        finally:
            await client.close()

        assert PLACEHOLDER_API_KEY not in str(caught.value)


def test_the_result_is_a_typed_image_not_a_dict() -> None:
    """The stage output crosses the module boundary as a typed model."""
    assert GeneratedImage.model_fields.keys() == {"mime_type", "data"}
