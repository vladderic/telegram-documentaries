"""Interviewer - the behavioural-dossier stage (Phase 3).

Pipeline stage 2 of 5 (see ``SPECS/ROADMAP.md``): asks 5-7 sharp questions,
strictly one per message, waits for each answer, then distils the answers into
a rich behavioural profile plus a suggested animal - the fuel for the Converter
and the Scripter.

Design (``SPECS/2026-10-19-interviewer/requirements.md``):

- **The state machine owns the thresholds** (``MIN_QUESTIONS`` /
  ``MAX_QUESTIONS``); the model only chooses *within* the 5-7 window. Three
  thin calls cover the flow: :meth:`InterviewerClient.next_question` (answering
  yet below the floor), :meth:`InterviewerClient.next_step` (in the window: ask
  again or finish) and :meth:`InterviewerClient.distil` (at the ceiling: the
  profile is forced).
- **The pending answer is tentative.** ``build_transcript`` shows the model the
  question/answer pair being resolved, but the caller records it only once the
  model response is parsed - so a retry after a failure cannot duplicate it.

Gemini responses are untrusted: every payload is validated into a typed model
here or raised as a typed :class:`GeminiApiError` (TECH.md).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import ValidationError

from telegram_documentaries.gemini import GeminiApiError, GeminiClient
from telegram_documentaries.logging_utils import log_async_call
from telegram_documentaries.models import (
    BehaviouralProfile,
    InterviewState,
    InterviewStep,
)

logger = logging.getLogger(__name__)

#: Text model for the interview (constitution: Gemini 3.1 Flash Lite).
INTERVIEWER_MODEL = "gemini-3.1-flash-lite"

#: The interview window: the model may not conclude before the floor and the
#: cap forces the profile (spec: 5-7 answers, never fewer, never more).
MIN_QUESTIONS = 5
MAX_QUESTIONS = 7

#: Answers longer than this are truncated in the transcript so prompts stay
#: bounded (untrusted user input, TECH.md).
ANSWER_MAX_CHARS = 2000

INTERVIEWER_PERSONA = (
    "You are the Interviewer for a comedy wildlife documentary, and your "
    "subject is a HUMAN BEING talking about their ordinary human life. You "
    "study people the way a field crew studies a new species: investigative, "
    "playful and slightly eccentric. The animal comparison is drawn later, "
    "from the transcript - it is your private shorthand, never your topic."
)

QUESTION_RULES = (
    "Rules for every question:\n"
    "- Ask exactly one question in this message - a single question mark, "
    "nothing bundled after it.\n"
    "- At most two sentences.\n"
    "- Never repeat a question you have already asked.\n"
    "- Ask about their human life: routines, habits, quirks, people, "
    "feelings, how they spend a day.\n"
    "- Never ask about them as if they were an animal: no animal life, packs, "
    "prey, watering holes, migration or mating. The creature comparison is "
    "decided later, and the subject must never be asked to become it.\n"
    "- Stay in character."
)

PROFILE_RULES = (
    "Rules for the profile:\n"
    "- Write in the third person about 'the subject', a human being.\n"
    "- 4-7 sentences of rich human personality: habits, quirks and manner.\n"
    "- Ground every claim only in the answers given - invent nothing.\n"
    "- Playful case-file register.\n"
    "- suggested_animal must be a real animal species whose real traits echo "
    "that human personality."
)

#: Structured-output schemas (the boundary contract, one per call).
QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
}

STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["ASK", "FINISH"]},
        "question": {"type": "string"},
        "profile": {"type": "string"},
        "suggested_animal": {"type": "string"},
    },
    "required": ["action"],
}

PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "profile": {"type": "string"},
        "suggested_animal": {"type": "string"},
    },
    "required": ["profile", "suggested_animal"],
}


def _truncate(text: str) -> str:
    """Bound one transcript field, marking that it was cut."""
    if len(text) <= ANSWER_MAX_CHARS:
        return text
    return text[:ANSWER_MAX_CHARS] + " [...truncated]"


def build_transcript(interview: InterviewState, answer: str | None = None) -> str:
    """Render the interview so far as the model's transcript.

    Args:
        interview: Current interview state (completed answers + pending question).
        answer: The answer just received for the pending question, if any. It is
            *tentative* - shown to the model, never recorded here (the caller
            records it only once the model response is parsed).

    Returns:
        Every question/answer pair oldest-first, with overly long answers
        truncated so prompts stay bounded.
    """
    pairs: list[tuple[str, str]] = [
        (pair.question, pair.answer) for pair in interview.answers
    ]
    if answer is not None and interview.pending_question:
        pairs.append((interview.pending_question, answer))

    lines = [f"TRANSCRIPT ({len(pairs)} answers so far):"]
    for number, (question, given) in enumerate(pairs, start=1):
        lines.append(f"Q{number}: {_truncate(question)}")
        lines.append(f"A{number}: {_truncate(given)}")
    return "\n".join(lines)


def _contents(prompt: str) -> list[dict[str, Any]]:
    """Wrap one prompt into the Gemini ``contents`` array."""
    return [{"role": "user", "parts": [{"text": prompt}]}]


def _count_with_tentative(interview: InterviewState, answer: str | None) -> int:
    """How many answers are in hand once the tentative one is counted."""
    return len(interview.answers) + (1 if answer is not None else 0)


class InterviewerClient:
    """Gemini text client producing the interview's typed contracts.

    Thin wrapper over the shared :class:`GeminiClient`: it contributes the
    prompts and schemas, and validates the model's payloads into typed models.
    """

    def __init__(
        self,
        api_key: str,
        model: str = INTERVIEWER_MODEL,
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
    async def next_question(self, interview: InterviewState, answer: str | None) -> str:
        """Ask the next question (used while below the interview floor).

        Args:
            interview: Interview state; ``pending_question`` names the question
                the tentative ``answer`` replies to.
            answer: The tentative answer just received, or ``None`` when the
                interview has not started (asking the first question).

        Returns:
            The next question, guaranteed non-empty.

        Raises:
            GeminiApiError: On transport/HTTP failures, an unexpected response
                shape, or an empty question.
        """
        asked = len(interview.answers) + 1
        prompt = (
            f"{INTERVIEWER_PERSONA}\n\n"
            f"{QUESTION_RULES}\n\n"
            f"This is question {asked} of at most {MAX_QUESTIONS}. "
            f"Ask your next question now.\n\n"
            f"{build_transcript(interview, answer)}"
        )
        data = await self._gemini.generate(_contents(prompt), QUESTION_SCHEMA)
        question = data.get("question")
        if not isinstance(question, str) or not question.strip():
            raise GeminiApiError(
                "Gemini API returned an unexpected question shape (empty question)"
            )
        logger.info("interview_question number=%s chars=%s", asked, len(question))
        return question.strip()

    @log_async_call(logger)
    async def next_step(
        self, interview: InterviewState, answer: str | None
    ) -> InterviewStep:
        """Decide the next move inside the 5-7 window: ask again or finish.

        Args:
            interview: Interview state (see :meth:`next_question`).
            answer: The tentative answer just received.

        Returns:
            The parsed :class:`InterviewStep` (ASK carries a question; FINISH
            carries the profile and animal).

        Raises:
            GeminiApiError: On transport/HTTP failures, an unexpected response
                shape, or an inconsistent step payload.
        """
        in_hand = _count_with_tentative(interview, answer)
        prompt = (
            f"{INTERVIEWER_PERSONA}\n\n"
            f"{QUESTION_RULES}\n\n"
            f"You have {in_hand} answers in hand. Choose:\n"
            f"- ASK your next question, or\n"
            f"- FINISH with the profile and the suggested animal.\n"
            f"Ask only while you have fewer than {MAX_QUESTIONS} answers, and "
            f"finish no earlier than {MIN_QUESTIONS}.\n\n"
            f"{build_transcript(interview, answer)}"
        )
        data = await self._gemini.generate(_contents(prompt), STEP_SCHEMA)
        try:
            step = InterviewStep.model_validate(data)
        except ValidationError as exc:
            raise GeminiApiError(
                f"Gemini API returned an unexpected interview step shape "
                f"({type(exc).__name__})"
            ) from None
        if step.action.value == "ASK":
            logger.info("interview_step action=ASK in_hand=%s", in_hand)
        else:
            logger.info(
                "interview_step action=FINISH in_hand=%s animal=%r",
                in_hand,
                step.suggested_animal,
            )
        return step

    @log_async_call(logger)
    async def distil(
        self, interview: InterviewState, answer: str | None
    ) -> BehaviouralProfile:
        """Produce the final profile (used at the interview ceiling).

        Args:
            interview: Interview state (see :meth:`next_question`).
            answer: The tentative final answer.

        Returns:
            The typed :class:`BehaviouralProfile` (rich text + real animal).

        Raises:
            GeminiApiError: On transport/HTTP failures, an unexpected response
                shape, or an empty profile/animal.
        """
        in_hand = _count_with_tentative(interview, answer)
        prompt = (
            f"{INTERVIEWER_PERSONA}\n\n"
            f"{PROFILE_RULES}\n\n"
            f"You have {in_hand} answers - that is enough. Write the profile "
            f"now, with the suggested animal.\n\n"
            f"{build_transcript(interview, answer)}"
        )
        data = await self._gemini.generate(_contents(prompt), PROFILE_SCHEMA)
        try:
            profile = BehaviouralProfile.model_validate(data)
        except ValidationError as exc:
            raise GeminiApiError(
                f"Gemini API returned an unexpected profile shape "
                f"({type(exc).__name__})"
            ) from None
        logger.info(
            "interview_profile animal=%r chars=%s",
            profile.suggested_animal,
            len(profile.profile),
        )
        return profile
