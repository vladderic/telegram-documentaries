"""Tests for the Narrator's typed audio contracts (Phase 6).

Two boundaries are validated here:

* `VoiceNote` - the encoded result handed to Telegram (MP3, non-empty).
* the PCM sample format parsed from the model's `mimeType` string, which is
  untrusted input: `audio/l16; rate=24000; channels=1`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from telegram_documentaries.gemini import GeminiApiError
from telegram_documentaries.narrator import VoiceNote, parse_pcm_format

_PCM_MIME = "audio/l16; rate=24000; channels=1"


class TestVoiceNote:
    def test_holds_mp3_bytes_with_an_audio_mpeg_type(self) -> None:
        note = VoiceNote(data=b"\xff\xfb\x90\x00", mime_type="audio/mpeg", duration=12)

        assert note.data == b"\xff\xfb\x90\x00"
        assert note.mime_type == "audio/mpeg"
        assert note.duration == 12

    def test_rejects_empty_bytes(self) -> None:
        with pytest.raises(ValidationError):
            VoiceNote(data=b"", mime_type="audio/mpeg", duration=12)

    def test_rejects_a_non_mp3_media_type(self) -> None:
        with pytest.raises(ValidationError):
            VoiceNote(
                data=b"\xff\xfb\x90\x00",
                mime_type="audio/ogg",  # type: ignore[arg-type]
                duration=12,
            )

    def test_rejects_a_duration_below_one_second(self) -> None:
        with pytest.raises(ValidationError):
            VoiceNote(data=b"\xff\xfb\x90\x00", mime_type="audio/mpeg", duration=0)


class TestParsePcmFormat:
    def test_parses_the_rate_and_channel_count(self) -> None:
        assert parse_pcm_format(_PCM_MIME) == (24000, 1)

    def test_tolerates_extra_whitespace_between_parameters(self) -> None:
        assert parse_pcm_format("audio/l16;rate=24000;channels=1") == (24000, 1)

    @pytest.mark.parametrize(
        "mime",
        [
            "audio/wav; rate=24000; channels=1",  # wrong encoding
            "audio/l16; rate=24000; channels=2",  # not mono
            "audio/l16; rate=16000; channels=1",  # unexpected sample rate
            "audio/l16; rate=24000",  # no channel count
            "audio/l16",  # bare, unparseable
            "audio/l16; rate=abc; channels=1",  # non-numeric rate
            "audio/l16; rate=-1; channels=1",  # negative rate
            "audio/l16; rate=0; channels=1",  # zero rate
            "audio/l16; rate=24000; channels=0",  # zero channels
            "audio/l16; rate=24000; channels=-2",  # negative channels
        ],
    )
    def test_fails_loudly_on_an_unsupported_format(self, mime: str) -> None:
        # Never guessed at: a wrong rate or channel count would silently
        # mis-time (and mis-pitch) the audio; a non-positive value is
        # nonsense, not a format to quietly accept.
        with pytest.raises(GeminiApiError):
            parse_pcm_format(mime)
