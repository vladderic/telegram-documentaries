"""Unit tests for the Scripter's typed contract (Phase 5).

Boundary contract (TECH.md): the Gemini text response is untrusted and
arbitrary. It is validated into a typed :class:`Script` at the edge, and the
model - not its callers - guarantees the narration is exactly one paragraph.
"""

import pytest
from pydantic import ValidationError

from telegram_documentaries.models import Script


class TestScript:
    def test_collapses_line_breaks_and_whitespace_runs_into_one_paragraph(
        self,
    ) -> None:
        script = Script(text="Our subject wakes.\n\nShe hunts   the kitchen.\n")

        assert script.text == "Our subject wakes. She hunts the kitchen."

    def test_is_idempotent_for_already_clean_text(self) -> None:
        clean = "A single tidy paragraph, ready for the microphone."

        assert Script(text=clean).text == clean

    def test_never_rewrites_the_narrators_words(self) -> None:
        """Decision: whitespace only - no markdown stripping, no paraphrasing."""
        spoken = "Well - truly, *marvellous* behaviour. #rare"

        assert Script(text=spoken).text == spoken

    def test_validates_a_schema_shaped_payload_directly(self) -> None:
        """The response schema's field name is the model's field name."""
        script = Script.model_validate({"text": "One paragraph."})

        assert script.text == "One paragraph."

    def test_ignores_unknown_fields(self) -> None:
        script = Script.model_validate({"text": "Kept.", "word_count": 2})

        assert script.text == "Kept."

    @pytest.mark.parametrize(
        "payload",
        [
            {"text": ""},
            {"text": "   \n\t  "},
            {"text": None},
            {"text": 12},
            {"text": ["one", "paragraph"]},
            {},
        ],
        ids=[
            "empty",
            "whitespace-only",
            "none",
            "non-string",
            "list",
            "missing",
        ],
    )
    def test_rejects_narrations_that_are_not_one_paragraph(
        self, payload: dict[str, object]
    ) -> None:
        with pytest.raises(ValidationError):
            Script.model_validate(payload)
