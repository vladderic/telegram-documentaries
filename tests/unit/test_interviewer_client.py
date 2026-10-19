"""Unit tests for the Interviewer's Gemini client (Phase 3).

The real Gemini API is never contacted: responses are mocked with
httpx.MockTransport and parsed into typed models at the boundary (TECH.md).
All keys below are placeholders.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from telegram_documentaries.gemini import GeminiApiError
from telegram_documentaries.interviewer import (
    ANSWER_MAX_CHARS,
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    InterviewerClient,
    build_transcript,
)
from telegram_documentaries.models import (
    BehaviouralProfile,
    InterviewAction,
    InterviewAnswer,
    InterviewState,
)

PLACEHOLDER_API_KEY = "PLACEHOLDER-GEMINI-API-KEY"

Handler = Callable[[httpx.Request], httpx.Response]


def interview_with(*answers: tuple[str, str]) -> InterviewState:
    state = InterviewState()
    for question, answer in answers:
        state.answers.append(InterviewAnswer(question=question, answer=answer))
    return state


def gemini_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a payload in a realistic Gemini generateContent envelope."""
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
        "modelVersion": "gemini-3.1-flash-lite",
    }


def make_client(handler: Handler) -> InterviewerClient:
    return InterviewerClient(
        api_key=PLACEHOLDER_API_KEY,
        transport=httpx.MockTransport(handler),
    )


def request_body(seen: list[httpx.Request]) -> dict[str, Any]:
    return json.loads(seen[0].content)


def prompt_text(body: dict[str, Any]) -> str:
    parts = body["contents"][0]["parts"]
    return next(part["text"] for part in parts if "text" in part)


class TestBuildTranscript:
    def test_includes_every_completed_pair_in_order(self) -> None:
        interview = interview_with(
            ("What is your habitat?", "A corner desk."),
            ("How do you greet strangers?", "With suspicion."),
        )

        transcript = build_transcript(interview)

        assert "What is your habitat?" in transcript
        assert "A corner desk." in transcript
        assert "How do you greet strangers?" in transcript
        assert "With suspicion." in transcript
        assert transcript.index("What is your habitat?") < transcript.index(
            "How do you greet strangers?"
        )

    def test_includes_the_tentative_answer_when_given(self) -> None:
        interview = interview_with(("Q1?", "A1"))
        interview.pending_question = "Q2?"

        transcript = build_transcript(interview, answer="A2")

        assert "Q2?" in transcript
        assert "A2" in transcript

    def test_reports_how_many_answers_are_in_hand(self) -> None:
        interview = interview_with(("Q1?", "A1"), ("Q2?", "A2"))

        transcript = build_transcript(interview)

        assert "2" in transcript

    def test_truncates_absurdly_long_answers(self) -> None:
        interview = interview_with(("Q1?", "A" * (ANSWER_MAX_CHARS * 3)))

        transcript = build_transcript(interview)

        assert len(transcript) < ANSWER_MAX_CHARS * 2
        assert "truncated" in transcript.lower()


class TestNextQuestion:
    @pytest.mark.asyncio
    async def test_asks_one_question_with_guardrailed_prompt(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=gemini_response({"question": "Q1?"}))

        client = make_client(handler)
        try:
            question = await client.next_question(interview_with(), answer=None)
        finally:
            await client.close()

        assert question == "Q1?"

        body = request_body(seen)
        assert body["contents"][0]["parts"][0]["text"]
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert set(body["generationConfig"]["responseSchema"]["properties"]) == {
            "question"
        }

        lowered = prompt_text(body).lower()
        assert "one question" in lowered
        assert "never repeat" in lowered
        assert "two sentences" in lowered
        # Voice: investigative, playful, slightly eccentric.
        assert "investigative" in lowered
        assert "playful" in lowered
        assert "eccentric" in lowered

    @pytest.mark.asyncio
    async def test_empty_question_from_the_model_is_a_typed_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=gemini_response({"question": "   "}))

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.next_question(interview_with(), answer=None)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_prompt_interviews_the_person_not_an_animal(self) -> None:
        """The subject is a human being: the wildlife lens is tone, not topic.

        Live check found the model asking about packs, prey and watering holes
        - it had read the documentary framing as "interview wildlife". The
        prompt must pin the subject down.
        """
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=gemini_response({"question": "Q1?"}))

        client = make_client(handler)
        try:
            await client.next_question(interview_with(), answer=None)
        finally:
            await client.close()

        lowered = prompt_text(request_body(seen)).lower()
        assert "human being" in lowered
        assert "never ask" in lowered
        assert "animal life" in lowered
        assert "packs" in lowered
        assert "prey" in lowered
        assert "investigative" in lowered


class TestNextStep:
    @pytest.mark.asyncio
    async def test_parses_an_ask_step(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=gemini_response({"action": "ASK", "question": "Q6?"})
            )

        client = make_client(handler)
        try:
            step = await client.next_step(interview_with(("Q1?", "A1")), answer="A1")
        finally:
            await client.close()

        assert step.action is InterviewAction.ASK
        assert step.question == "Q6?"

    @pytest.mark.asyncio
    async def test_parses_a_finish_step(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=gemini_response(
                    {
                        "action": "FINISH",
                        "profile": "The subject is a nocturnal hoarder.",
                        "suggested_animal": "Red fox",
                    }
                ),
            )

        client = make_client(handler)
        try:
            step = await client.next_step(interview_with(("Q1?", "A1")), answer="A1")
        finally:
            await client.close()

        assert step.action is InterviewAction.FINISH
        assert step.suggested_animal == "Red fox"

    @pytest.mark.asyncio
    async def test_prompt_states_the_five_to_seven_rule(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200, json=gemini_response({"action": "ASK", "question": "Q?"})
            )

        client = make_client(handler)
        try:
            await client.next_step(
                interview_with(("Q1?", "A1"), ("Q2?", "A2")), answer="A2"
            )
        finally:
            await client.close()

        body = request_body(seen)
        assert set(body["generationConfig"]["responseSchema"]["properties"]) == {
            "action",
            "question",
            "profile",
            "suggested_animal",
        }
        assert body["generationConfig"]["responseSchema"]["properties"]["action"][
            "enum"
        ] == ["ASK", "FINISH"]

        lowered = prompt_text(body).lower()
        assert str(MIN_QUESTIONS) in lowered
        assert str(MAX_QUESTIONS) in lowered
        assert "one question" in lowered

    @pytest.mark.asyncio
    async def test_inconsistent_step_payload_is_a_typed_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=gemini_response({"action": "FINISH", "profile": "P"})
            )

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.next_step(interview_with(("Q1?", "A1")), answer="A1")
        finally:
            await client.close()


class TestDistil:
    @pytest.mark.asyncio
    async def test_returns_a_typed_profile_and_animal(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json=gemini_response(
                    {
                        "profile": "The subject patrols the kitchen at midnight.",
                        "suggested_animal": "Red fox",
                    }
                ),
            )

        client = make_client(handler)
        try:
            profile = await client.distil(interview_with(("Q1?", "A1")), answer="A1")
        finally:
            await client.close()

        assert isinstance(profile, BehaviouralProfile)
        assert profile.suggested_animal == "Red fox"

        body = request_body(seen)
        assert set(body["generationConfig"]["responseSchema"]["properties"]) == {
            "profile",
            "suggested_animal",
        }

        lowered = prompt_text(body).lower()
        assert "third person" in lowered
        assert "animal" in lowered
        assert "only" in lowered and "answers" in lowered
        # The dossier describes a human personality; the animal is the metaphor.
        assert "human personality" in lowered

    @pytest.mark.asyncio
    async def test_empty_profile_from_the_model_is_a_typed_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=gemini_response({"profile": "", "suggested_animal": "Red fox"}),
            )

        client = make_client(handler)
        try:
            with pytest.raises(GeminiApiError):
                await client.distil(interview_with(("Q1?", "A1")), answer="A1")
        finally:
            await client.close()


@pytest.mark.parametrize(
    "response_body",
    [
        pytest.param({"candidates": []}, id="no-candidates"),
        pytest.param(
            {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
            id="not-json",
        ),
    ],
)
@pytest.mark.asyncio
async def test_untrusted_response_shapes_raise_typed_errors(
    response_body: dict[str, Any],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body)

    client = make_client(handler)
    try:
        with pytest.raises(GeminiApiError):
            await client.next_question(interview_with(), answer=None)
    finally:
        await client.close()
