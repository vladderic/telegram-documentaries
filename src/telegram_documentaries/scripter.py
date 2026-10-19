"""The Scripter: turns the dossier into the documentary's narration (Phase 5).

Input: the Interviewer's typed
:class:`~telegram_documentaries.models.BehaviouralProfile`. Output: exactly one
:class:`~telegram_documentaries.models.Script` - a single paragraph of plain
prose, ready to be read by the microphone (Phase 6 synthesises it into audio).

The narrator's role and rules travel as the Gemini **system instruction** (the
first stage in this repo to use that field), so the user turn carries nothing
but the dossier facts.

Gemini responses are untrusted: every payload is validated into the typed
``Script`` here, and anything else is a typed :class:`GeminiApiError` (TECH.md).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import ValidationError

from telegram_documentaries.gemini import GeminiApiError, GeminiClient
from telegram_documentaries.logging_utils import log_async_call
from telegram_documentaries.models import BehaviouralProfile, Script

logger = logging.getLogger(__name__)

#: Text model for the narration (constitution, TECH.md).
SCRIPTER_MODEL = "gemini-3.1-flash-lite"

#: The one hard number the user asked for: the narration is never longer than
#: this. The prompt targets the ROADMAP's 60-90 band; the code only *reports*
#: an overrun - a word count is never a reason to lose the script.
MAX_WORDS = 90

SCRIPTER_PERSONA = (
    "You are the narrator of a revered, dramatic British wildlife "
    "documentary - the voice that has introduced a thousand creatures. Your "
    "subject is a HUMAN BEING, cast as the animal named in the briefing: you "
    "study their ordinary human life as though it were the behaviour of that "
    "creature, with the fond, grand authority of the genre."
)

SCRIPT_RULES = (
    "Write the narration:\n"
    "- Exactly one paragraph, 60 to 90 words - never more than 90 - as a "
    "single unbroken piece of prose.\n"
    "- Plain prose for the microphone: no markdown, no lists, no headings, no "
    "line breaks, no quotation marks, no speaker labels, no stage directions, "
    "no sound effects. It is read aloud exactly as written.\n"
    "- Name the species the subject was cast as, and keep the subject in that "
    "role: their quirks, habits and appetites are the animal behaviour under "
    "study, played for quiet comedy.\n"
    "- Ground every claim in the dossier; invent nothing.\n"
    "- Never address the reader, never explain the joke, never list facts."
)

#: Structured-output schema (the boundary contract): one field, named like the
#: model's own field so the payload validates directly.
SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": (
                "The narration: exactly one paragraph of 60 to 90 words, plain "
                "prose with no markdown, no line breaks and no quotation "
                "marks - it will be read aloud exactly as written."
            ),
        }
    },
    "required": ["text"],
}


def build_prompt(profile: BehaviouralProfile) -> str:
    """Build the user turn from the typed dossier (pure, testable).

    Only the facts: the narrator's role and rules are the system instruction.
    """
    return (
        f"The subject was cast as: {profile.suggested_animal}\n"
        f"The crew's note on that species: {profile.animal_description}\n\n"
        f"Dossier on the subject:\n{profile.profile}\n\n"
        f"Write the narration now."
    )


def _contents(prompt: str) -> list[dict[str, Any]]:
    """Wrap one prompt into the Gemini ``contents`` array."""
    return [{"role": "user", "parts": [{"text": prompt}]}]


def _system_instruction() -> str:
    """The narrator's role and rules, as the Gemini system instruction."""
    return f"{SCRIPTER_PERSONA}\n\n{SCRIPT_RULES}"


class ScripterClient:
    """Gemini text client producing the Scripter's typed output."""

    def __init__(
        self,
        api_key: str,
        model: str = SCRIPTER_MODEL,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._gemini = GeminiClient(
            api_key=api_key,
            model=model,
            timeout=timeout,
            transport=transport,
        )

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._gemini.close()

    @log_async_call(logger)
    async def write_script(self, profile: BehaviouralProfile) -> Script:
        """Write the narration for one subject.

        Args:
            profile: The Interviewer's typed dossier (the personality summary
                plus the species the subject was cast as).

        Returns:
            The typed :class:`Script`: one paragraph, ready for the microphone.

        Raises:
            GeminiApiError: On transport/HTTP failures, an unexpected response
                shape, or empty narration text. The API key is redacted from
                every message.
        """
        logger.info("scripter_write animal=%r", profile.suggested_animal)
        data = await self._gemini.generate(
            _contents(build_prompt(profile)),
            SCRIPT_SCHEMA,
            system_instruction=_system_instruction(),
        )
        try:
            script = Script.model_validate(data)
        except ValidationError as exc:
            # Missing/empty/non-string text is a typed failure, never an empty
            # narration handed to Telegram or to the Narrator.
            raise GeminiApiError(
                f"Gemini API returned an unexpected script shape ({type(exc).__name__})"
            ) from None

        words = len(script.text.split())
        logger.info("scripter_script words=%s chars=%s", words, len(script.text))
        if words > MAX_WORDS:
            # Reported, not fixed: the narration is always delivered (decision
            # recorded in the spec) and a cut would be heard mid-sentence.
            logger.warning(
                "scripter_over_word_limit words=%s limit=%s", words, MAX_WORDS
            )
        return script
