"""The Narrator: renders the narration text as a Telegram voice note (Phase 6).

Input: the plain-text narration Phase 5 already delivered (``script_text``).
Output: a :class:`VoiceNote` - MP3 bytes Telegram renders as a playable voice
message.

Per TECH.md the Narrator is **not an agent**: it is one Gemini TTS call
(``gemini-3.1-flash-tts-preview``) plus a local encode. The model returns
headerless 16-bit PCM (24 kHz, mono) and cannot emit OGG/Opus or MP3 itself, and
it cannot be styled out-of-band (``speech_metadata`` and ``systemInstruction``
are both rejected). A style prefix in the prompt is *spoken*, so the script is
sent **verbatim** and the voice's own character carries the tone.

Gemini responses are untrusted: the audio payload and its ``mimeType`` are
validated here, and anything unsupported is a typed
:class:`~telegram_documentaries.gemini.GeminiApiError` (TECH.md).
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Final, Literal

import lameenc  # type: ignore[import-not-found]
from pydantic import BaseModel, Field

from telegram_documentaries.gemini import (
    GeminiApiError,
    GeminiClient,
    InlineAudio,
    generate_url,
)
from telegram_documentaries.logging_utils import log_async_call

logger = logging.getLogger(__name__)

#: TTS model (constitution, TECH.md).
NARRATOR_MODEL = "gemini-3.1-flash-tts-preview"

#: The single prebuilt voice. The user wanted a male British narrator; the
#: Gemini voice catalogue has no British voices (see the spec), so this is the
#: deep, measured narrator voice chosen as the closest fit for now.
DEFAULT_VOICE = "Charon"

#: What the TTS model actually returns: headerless 16-bit LE PCM, mono.
PCM_SAMPLE_RATE = 24000
PCM_CHANNELS = 1
PCM_BITS = 16

#: MP3 encode settings. 64 kbit/s mono is transparent enough for speech and
#: keeps a ~90-word note to a few tens of KB.
MP3_BIT_RATE = 64
MP3_QUALITY = 2

#: The only media type a voice note may carry (Telegram plays OGG/Opus, MP3
#: and M4A; MP3 is what `lameenc` produces).
VOICE_MIME_TYPE: Final = "audio/mpeg"


def generate_url_for_narrator() -> str:
    """The TTS ``generateContent`` endpoint (TECH.md: Gemini 3.1 Flash TTS)."""
    return generate_url(NARRATOR_MODEL)


class VoiceNote(BaseModel):
    """The encoded narration, ready for ``sendVoice``.

    A typed boundary value: the bytes are the MP3 payload, never empty, and the
    media type is pinned so a caller cannot accidentally label PCM as audio
    Telegram would refuse to play.
    """

    data: bytes = Field(min_length=1)
    mime_type: Literal["audio/mpeg"] = "audio/mpeg"
    #: Spoken length in whole seconds, from the source PCM frame count.
    duration: int = Field(ge=1)


def encode_mp3(pcm: bytes, sample_rate: int, channels: int) -> bytes:
    """Encode raw 16-bit little-endian PCM into MP3 bytes.

    Pure and network-free, so it is tested directly. ``lameenc`` expects the
    sample bytes as given (no WAV header), which is exactly what Gemini TTS
    returns.

    Args:
        pcm: Headerless 16-bit LE PCM samples.
        sample_rate: Sample rate in Hz.
        channels: Channel count (1 = mono).

    Returns:
        The MP3 byte stream.

    Raises:
        ValueError: If the PCM is empty or not a whole number of 16-bit frames -
            a half sample means the payload was truncated or mis-framed.
    """
    if not pcm:
        raise ValueError("cannot encode empty PCM audio")
    frame_bytes = (PCM_BITS // 8) * channels
    if len(pcm) % frame_bytes != 0:
        raise ValueError(
            f"PCM length {len(pcm)} is not a whole number of {PCM_BITS}-bit frames"
        )

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(MP3_BIT_RATE)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(MP3_QUALITY)
    encoded = bytes(encoder.encode(pcm))
    return encoded + bytes(encoder.flush())


def parse_pcm_format(mime_type: str) -> tuple[int, int]:
    """Parse ``(sample_rate, channels)`` from a TTS ``mimeType`` string.

    Args:
        mime_type: The model's declared media type, e.g.
            ``"audio/l16; rate=24000; channels=1"``.

    Returns:
        The sample rate in Hz and the channel count.

    Raises:
        GeminiApiError: If the media type is not the expected 16-bit PCM at the
            expected rate and channel count. Guessing here would silently
            mis-pitch the narrator.
    """
    parts = [section.strip() for section in mime_type.split(";")]
    container = parts[0].lower()
    if container != "audio/l16":
        raise GeminiApiError(
            f"TTS returned unsupported audio encoding {mime_type!r} "
            f"(expected audio/l16 PCM)"
        )

    params: dict[str, str] = {}
    for section in parts[1:]:
        key, _, value = section.partition("=")
        params[key.strip().lower()] = value.strip()

    try:
        rate = int(params["rate"])
        channels = int(params["channels"])
    except (KeyError, ValueError):
        raise GeminiApiError(
            f"TTS audio format {mime_type!r} is missing a usable rate/channels"
        ) from None

    if rate != PCM_SAMPLE_RATE or channels != PCM_CHANNELS:
        raise GeminiApiError(
            f"TTS returned {rate} Hz / {channels} channel(s); "
            f"expected {PCM_SAMPLE_RATE} Hz mono"
        )
    return rate, channels


class NarratorClient:
    """Renders narration text into a Telegram-ready voice note.

    One method, ``speak``, wrapping a single Gemini TTS call and the local MP3
    encode. The script is sent verbatim - the model reads it aloud exactly as
    written, so the audio can never say something the user did not read.
    """

    def __init__(
        self,
        *,
        api_key: str,
        voice: str = DEFAULT_VOICE,
        client: GeminiClient | None = None,
    ) -> None:
        """Create the client.

        Args:
            api_key: Gemini API key (never logged).
            voice: Prebuilt voice name (see :data:`DEFAULT_VOICE`).
            client: Optional shared Gemini client; one is built when omitted.
        """
        self._client = client or GeminiClient(api_key=api_key, model=NARRATOR_MODEL)
        self._voice = voice

    async def close(self) -> None:
        """Release the HTTP connection pool."""
        await self._client.close()

    @log_async_call(logger)
    async def speak(self, script: str) -> VoiceNote:
        """Synthesise one narration into a voice note.

        Args:
            script: The narration text, exactly as delivered to the user.

        Returns:
            The typed :class:`VoiceNote` (MP3 bytes).

        Raises:
            GeminiApiError: On transport/HTTP failures, a malformed inline audio
                part, an unsupported audio format, or a payload the MP3 encoder
                rejects (truncated frames, encoder failure) — every internal
                failure is typed before it leaves this boundary.
        """
        audio = await self._client.generate_audio(script, voice=self._voice)
        pcm = _decode_audio(audio)
        rate, channels = parse_pcm_format(audio.mime_type)
        # Telegram wants whole seconds for the waveform; round up so the
        # displayed length never undershoots the audio.
        frames = len(pcm) // ((PCM_BITS // 8) * channels)
        duration = max(1, round(frames / rate))
        try:
            encoded = encode_mp3(pcm, rate, channels)
        except (ValueError, RuntimeError) as exc:
            # The encode step fails untyped (ValueError on a truncated
            # payload, RuntimeError from the lameenc C extension); the client
            # boundary makes it the typed error the gateway degrades on,
            # exactly like the parse step above.
            raise GeminiApiError(
                f"TTS audio could not be encoded ({type(exc).__name__}): {exc}"
            ) from exc
        return VoiceNote(data=encoded, duration=duration)


def _decode_audio(audio: InlineAudio) -> bytes:
    """Base64-decode the inline audio payload, failing loudly on garbage."""
    try:
        return base64.b64decode(audio.data, validate=True)
    except (binascii.Error, ValueError):
        raise GeminiApiError("TTS inline audio was not valid base64") from None
