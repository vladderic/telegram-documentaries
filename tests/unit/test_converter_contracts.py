"""Unit tests for the Converter's typed contracts (Phase 4).

Boundary contract (TECH.md): the Gemini image response is untrusted, arbitrary
and base64-encoded on the wire. It is decoded and validated here, so nothing
downstream ever handles base64 text or a raw dict.
"""

import base64

import pytest
from pydantic import ValidationError

from telegram_documentaries.models import GeneratedImage

JPEG_BYTES = b"\xff\xd8\xff\xe0-fake-jpeg-bytes"
JPEG_B64 = base64.b64encode(JPEG_BYTES).decode()


class TestGeneratedImage:
    def test_decodes_base64_at_the_boundary(self) -> None:
        image = GeneratedImage.from_base64("image/jpeg", JPEG_B64)

        assert image.data == JPEG_BYTES
        assert isinstance(image.data, bytes)

    def test_accepts_raw_bytes_unchanged(self) -> None:
        image = GeneratedImage(mime_type="image/png", data=JPEG_BYTES)

        assert image.data == JPEG_BYTES
        assert image.mime_type == "image/png"

    def test_accepts_base64_split_across_lines(self) -> None:
        wrapped = "\n".join(JPEG_B64[i : i + 8] for i in range(0, len(JPEG_B64), 8))

        image = GeneratedImage.from_base64("image/jpeg", wrapped)

        assert image.data == JPEG_BYTES

    def test_normalises_the_mime_type(self) -> None:
        image = GeneratedImage.from_base64("  IMAGE/JPEG  ", JPEG_B64)

        assert image.mime_type == "image/jpeg"

    @pytest.mark.parametrize(
        "payload",
        [
            {"mime_type": "image/jpeg", "data": ""},
            {"mime_type": "image/jpeg", "data": b""},
            {"mime_type": "image/jpeg", "data": "not base64 !!!"},
            {"mime_type": "image/jpeg", "data": "c"},
            {"mime_type": "text/plain", "data": JPEG_B64},
            {"mime_type": "application/json", "data": JPEG_B64},
            {"mime_type": "", "data": JPEG_B64},
            {"mime_type": "image/jpeg"},
            {"data": JPEG_B64},
            {"mime_type": "image/jpeg", "data": 12},
        ],
        ids=[
            "empty-string-data",
            "empty-bytes-data",
            "invalid-base64",
            "truncated-base64",
            "text-mime",
            "json-mime",
            "empty-mime",
            "missing-data",
            "missing-mime",
            "non-string-data",
        ],
    )
    def test_rejects_malformed_images(self, payload: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            GeneratedImage.model_validate(payload)

    def test_ignores_unknown_fields(self) -> None:
        image = GeneratedImage.model_validate(
            {"mime_type": "image/jpeg", "data": JPEG_B64, "prompt": "ignored"}
        )

        assert image.data == JPEG_BYTES
