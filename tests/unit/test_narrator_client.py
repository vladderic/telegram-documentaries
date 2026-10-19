"""Unit tests for the Narrator client (Phase 6).

The real TTS API is never contacted: responses are mocked with
httpx.MockTransport. Keys below are placeholders.
"""

from __future__ import annotations

import base64
import json
import struct
from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest

from telegram_documentaries import narrator as narrator_module
from telegram_documentaries.gemini import GeminiApiError, GeminiClient
from telegram_documentaries.narrator import (
    DEFAULT_VOICE,
    NARRATOR_MODEL,
    NarratorClient,
    VoiceNote,
    generate_url_for_narrator,
)

PLACEHOLDER_API_KEY = "PLACEHOLDER-GEMINI-API-KEY"
Handler = Callable[[httpx.Request], httpx.Response]

_SCRIPT = "The solitary raccoon delays rest until two in the morning."


def _pcm(seconds: float = 0.4, rate: int = 24000) -> bytes:
    frames = int(rate * seconds)
    return struct.pack(f"<{frames}h", *([0] * frames))


def audio_response(
    pcm: bytes = b"", mime_type: str = "audio/l16; rate=24000; channels=1"
) -> dict[str, object]:
    payload = base64.b64encode(pcm or _pcm()).decode()
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"inlineData": {"mimeType": mime_type, "data": payload}}],
                },
                "finishReason": "STOP",
            }
        ]
    }


def make_narrator(handler: Handler) -> NarratorClient:
    shared = GeminiClient(
        api_key=PLACEHOLDER_API_KEY,
        model=NARRATOR_MODEL,
        transport=httpx.MockTransport(handler),
    )
    return NarratorClient(api_key=PLACEHOLDER_API_KEY, client=shared)


class TestSpeak:
    @pytest.mark.asyncio
    async def test_returns_a_typed_mp3_voice_note(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=audio_response())

        narrator = make_narrator(handler)
        try:
            note = await narrator.speak(_SCRIPT)
        finally:
            await narrator.close()

        assert isinstance(note, VoiceNote)
        assert note.mime_type == "audio/mpeg"
        assert len(note.data) > 0
        assert note.duration >= 1
        assert seen[0].url.path.endswith(f"/{NARRATOR_MODEL}:generateContent")

    @pytest.mark.asyncio
    async def test_sends_the_script_verbatim_with_no_style_prefix(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=audio_response())

        narrator = make_narrator(handler)
        try:
            await narrator.speak(_SCRIPT)
        finally:
            await narrator.close()

        body = json.loads(seen[0].content)
        # A prefix here would be read aloud (live finding), so the prompt must
        # be exactly the narration the user already read.
        assert body["contents"] == [{"parts": [{"text": _SCRIPT}]}]
        assert "systemInstruction" not in body
        voice = body["generationConfig"]["speechConfig"]["voiceConfig"][
            "prebuiltVoiceConfig"
        ]["voiceName"]
        assert voice == DEFAULT_VOICE

    @pytest.mark.asyncio
    async def test_rejects_an_unsupported_audio_format(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=audio_response(mime_type="audio/wav"))

        narrator = make_narrator(handler)
        try:
            with pytest.raises(GeminiApiError):
                await narrator.speak(_SCRIPT)
        finally:
            await narrator.close()

    @pytest.mark.asyncio
    async def test_duration_rounds_up_to_whole_seconds(self) -> None:
        """A 2.5s waveform is displayed as 3s, never undercut at 2 (ceil)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=audio_response(pcm=_pcm(seconds=2.5)))

        narrator = make_narrator(handler)
        try:
            note = await narrator.speak(_SCRIPT)
        finally:
            await narrator.close()

        assert note.duration == 3

    @pytest.mark.asyncio
    async def test_a_truncated_payload_is_a_typed_failure(self) -> None:
        """A half-sample frame must degrade like any other TTS failure.

        ``encode_mp3`` raises ``ValueError`` on an odd-byte-length payload;
        the client boundary turns that into the typed ``GeminiApiError`` the
        gateway's degrade path catches — never a raw exception.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=audio_response(pcm=b"\x01\x02\x03"))

        narrator = make_narrator(handler)
        try:
            with pytest.raises(GeminiApiError):
                await narrator.speak(_SCRIPT)
        finally:
            await narrator.close()

    @pytest.mark.asyncio
    async def test_an_encoder_failure_is_typed_and_chained(self) -> None:
        """``lameenc`` raises ``RuntimeError``; it must not escape untyped."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=audio_response())

        narrator = make_narrator(handler)
        try:
            with (
                pytest.raises(GeminiApiError) as caught,
                patch.object(
                    narrator_module, "encode_mp3", side_effect=RuntimeError("boom")
                ),
            ):
                await narrator.speak(_SCRIPT)
        finally:
            await narrator.close()

        assert isinstance(caught.value.__cause__, RuntimeError)

    @pytest.mark.asyncio
    async def test_http_error_is_typed_and_never_leaks_the_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        narrator = make_narrator(handler)
        try:
            with pytest.raises(GeminiApiError) as caught:
                await narrator.speak(_SCRIPT)
        finally:
            await narrator.close()

        assert PLACEHOLDER_API_KEY not in str(caught.value)


def test_narrator_url_helper_points_at_the_tts_model() -> None:
    assert generate_url_for_narrator() == (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{NARRATOR_MODEL}:generateContent"
    )
