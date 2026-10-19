"""Tests for the Narrator's PCM -> MP3 encode step (Phase 6).

The TTS model returns headerless PCM and cannot emit MP3 itself, so the encode
step is pure, dependency-backed (`lameenc`) and tested without any network.
"""

from __future__ import annotations

import math
import struct

import pytest

from telegram_documentaries.narrator import (
    PCM_CHANNELS,
    PCM_SAMPLE_RATE,
    encode_mp3,
)

#: MPEG audio frame sync (0xFFE) followed by a known MPEG-1/2 Layer III sync
#: pattern. lameenc output starts with one of these; an ID3 tag is also valid.
_MP3_SYNC_PREFIXES = (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa")


def _tone(seconds: float, *, rate: int = PCM_SAMPLE_RATE) -> bytes:
    """Build little-endian 16-bit mono PCM for a quiet sine tone."""
    frames = int(rate * seconds)
    amplitude = 8000
    samples = [
        int(amplitude * math.sin(2 * math.pi * 440 * i / rate)) for i in range(frames)
    ]
    return struct.pack(f"<{frames}h", *samples)


class TestEncodeMp3:
    def test_encodes_pcm_to_a_non_empty_mp3_frame_stream(self) -> None:
        mp3 = encode_mp3(_tone(0.5), PCM_SAMPLE_RATE, PCM_CHANNELS)

        assert mp3
        assert mp3.startswith(_MP3_SYNC_PREFIXES) or mp3.startswith(b"ID3"), mp3[:8]

    def test_encoded_duration_tracks_the_input_length(self) -> None:
        # A ~1s clip at 24kHz mono, 16-bit is 48000 bytes; the MP3 should be in
        # the same ballpark (a broken/looping encoder would blow this up).
        one_second = encode_mp3(_tone(1.0), PCM_SAMPLE_RATE, PCM_CHANNELS)
        two_seconds = encode_mp3(_tone(2.0), PCM_SAMPLE_RATE, PCM_CHANNELS)

        assert len(one_second) > 1_000
        assert 1.5 < (len(two_seconds) / len(one_second)) < 2.5

    def test_rejects_empty_pcm(self) -> None:
        with pytest.raises(ValueError):
            encode_mp3(b"", PCM_SAMPLE_RATE, PCM_CHANNELS)

    def test_rejects_an_odd_number_of_bytes(self) -> None:
        # 16-bit samples are two bytes each; a stray half-sample is a bug.
        with pytest.raises(ValueError):
            encode_mp3(b"\x01\x02\x03", PCM_SAMPLE_RATE, PCM_CHANNELS)
