"""Unit tests for the Interviewer's typed contracts (Phase 3).

Boundary contracts: a malformed model response must fail loudly here and never
leak downstream as a raw dict (TECH.md).
"""

import pytest
from pydantic import ValidationError

from telegram_documentaries.models import (
    BehaviouralProfile,
    InterviewAction,
    InterviewAnswer,
    InterviewStep,
)


class TestInterviewAnswer:
    def test_parses_a_question_answer_pair(self) -> None:
        answer = InterviewAnswer(
            question="What is your natural habitat?",
            answer="A corner desk with too many mugs.",
        )
        assert answer.question == "What is your natural habitat?"
        assert "mugs" in answer.answer

    @pytest.mark.parametrize(
        "payload",
        [
            {"question": "", "answer": "fine"},
            {"question": "   ", "answer": "fine"},
            {"question": "fine?", "answer": ""},
            {"question": "fine?", "answer": "   "},
            {"question": "fine?"},
            {"answer": "fine"},
        ],
        ids=[
            "empty-question",
            "blank-question",
            "empty-answer",
            "blank-answer",
            "missing-answer",
            "missing-question",
        ],
    )
    def test_rejects_empty_or_missing_fields(self, payload: dict[str, str]) -> None:
        with pytest.raises(ValidationError):
            InterviewAnswer.model_validate(payload)


class TestInterviewStep:
    def test_parses_an_ask_step(self) -> None:
        step = InterviewStep.model_validate(
            {"action": "ASK", "question": "How do you greet a stranger?"}
        )
        assert step.action is InterviewAction.ASK
        assert step.question == "How do you greet a stranger?"
        assert step.profile is None
        assert step.suggested_animal is None

    def test_parses_a_finish_step(self) -> None:
        step = InterviewStep.model_validate(
            {
                "action": "FINISH",
                "profile": "The subject is a nocturnal opportunist.",
                "suggested_animal": "Red fox",
            }
        )
        assert step.action is InterviewAction.FINISH
        assert step.question is None
        assert step.suggested_animal == "Red fox"

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"action": "ASK"}, id="ask-without-question"),
            pytest.param(
                {"action": "ASK", "question": "Q?", "profile": "P"},
                id="ask-with-profile",
            ),
            pytest.param(
                {"action": "ASK", "question": "Q?", "suggested_animal": "Red fox"},
                id="ask-with-animal",
            ),
            pytest.param(
                {"action": "FINISH", "profile": "P"},
                id="finish-without-animal",
            ),
            pytest.param(
                {"action": "FINISH", "suggested_animal": "Red fox"},
                id="finish-without-profile",
            ),
            pytest.param(
                {
                    "action": "FINISH",
                    "profile": "P",
                    "suggested_animal": "Red fox",
                    "question": "Q?",
                },
                id="finish-with-question",
            ),
            pytest.param({"question": "Q?"}, id="missing-action"),
            pytest.param({"action": "MAYBE", "question": "Q?"}, id="unknown-action"),
            pytest.param({"action": False, "question": "Q?"}, id="non-string-action"),
        ],
    )
    def test_enforces_ask_finish_consistency(self, payload: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            InterviewStep.model_validate(payload)

    def test_junk_payload_fails_loudly(self) -> None:
        with pytest.raises(ValidationError):
            InterviewStep.model_validate({"foo": "bar"})

    def test_to_profile_returns_the_typed_profile_for_a_finish_step(self) -> None:
        step = InterviewStep.model_validate(
            {
                "action": "FINISH",
                "profile": "The subject hoards stationery.",
                "suggested_animal": "Eurasian magpie",
            }
        )

        profile = step.to_profile()

        assert profile == BehaviouralProfile(
            profile="The subject hoards stationery.",
            suggested_animal="Eurasian magpie",
        )

    def test_to_profile_is_none_for_an_ask_step(self) -> None:
        step = InterviewStep.model_validate({"action": "ASK", "question": "Q?"})

        assert step.to_profile() is None


class TestBehaviouralProfile:
    def test_parses_the_stage_output(self) -> None:
        profile = BehaviouralProfile(
            profile="The subject forages at night and hoards stationery.",
            suggested_animal="Eurasian magpie",
        )
        assert profile.suggested_animal == "Eurasian magpie"
        assert "stationery" in profile.profile

    @pytest.mark.parametrize(
        "payload",
        [
            {"profile": "", "suggested_animal": "Red fox"},
            {"profile": "  ", "suggested_animal": "Red fox"},
            {"profile": "Something", "suggested_animal": ""},
            {"profile": "Something", "suggested_animal": "   "},
            {"profile": "Something"},
            {"suggested_animal": "Red fox"},
        ],
        ids=[
            "empty-profile",
            "blank-profile",
            "empty-animal",
            "blank-animal",
            "missing-animal",
            "missing-profile",
        ],
    )
    def test_rejects_empty_or_missing_fields(self, payload: dict[str, str]) -> None:
        with pytest.raises(ValidationError):
            BehaviouralProfile.model_validate(payload)
