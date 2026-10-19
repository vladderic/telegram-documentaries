"""Unit tests for the Interviewer wired into the gateway (Phase 3 routing).

The Telegram API, the Bouncer's vision client and the Interviewer client are
all mocked: no test contacts a real API (TECH.md). The Interviewer fake is
scripted and records every call together with a deep copy of the interview
state it was handed, so the state machine can be asserted without poking at
``run_bot``'s internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram_documentaries import bot_gateway
from telegram_documentaries.gemini import GeminiApiError
from telegram_documentaries.models import (
    BehaviouralProfile,
    BouncerVerdict,
    InterviewAction,
    InterviewAnswer,
    InterviewState,
    InterviewStep,
    Update,
)

PLACEHOLDER_TOKEN = "123456:PLACEHOLDER-TELEGRAM-BOT-TOKEN"
PLACEHOLDER_GEMINI_KEY = "PLACEHOLDER-GEMINI-API-KEY"

ACCEPT_VERDICT = BouncerVerdict(
    accepted=True,
    observed_subject="a single person",
    message="Welcome to the shoot - let's begin.",
)
DISTILLED_PROFILE = BehaviouralProfile(
    profile="The subject patrols the kitchen at midnight, seeking snacks.",
    suggested_animal="Red fox",
)
#: Immediate acknowledgement the photo flow sends while it downloads + judges.
PHOTO_HOLD = bot_gateway.PHOTO_HOLD_MSG
#: The clock the gateway sees in these tests: just before every fixture date,
#: so normal answers are unambiguously "after" the question they answer.
DEFAULT_NOW = 1_760_000_000.0


def photo_update(update_id: int, chat_id: int) -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1_760_000_100,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
                "photo": [
                    {
                        "file_id": f"p{update_id}",
                        "file_unique_id": f"u{update_id}",
                        "width": 640,
                        "height": 480,
                        "file_size": 4096,
                    }
                ],
            },
        }
    )


def text_update(
    update_id: int,
    chat_id: int,
    text: str,
    *,
    date: int = 1_760_000_200,
) -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": date,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
                "text": text,
            },
        }
    )


def sticker_update(update_id: int, chat_id: int) -> Update:
    """A message whose only content is a sticker (no text, no photo)."""
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1_760_000_300,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
            },
        }
    )


@dataclass
class InterviewCall:
    """One recorded call into the interview fake."""

    method: str
    interview: InterviewState
    answer: str | None


class FakeInterviewer:
    """Scripted, recording stand-in for :class:`InterviewerClient`.

    Each queue item is either the value to return or an exception to raise, so
    happy paths and model failures are scripted the same way.
    """

    def __init__(
        self,
        *,
        questions: list[Any] | None = None,
        steps: list[Any] | None = None,
        profiles: list[Any] | None = None,
    ) -> None:
        self.question_queue: list[Any] = list(questions or [])
        self.step_queue: list[Any] = list(steps or [])
        self.profile_queue: list[Any] = list(profiles or [])
        self.calls: list[InterviewCall] = []
        self.closed = False

    def _record(
        self, method: str, interview: InterviewState, answer: str | None
    ) -> InterviewCall:
        call = InterviewCall(method, interview.model_copy(deep=True), answer)
        self.calls.append(call)
        return call

    def _take(self, queue: list[Any], method: str) -> Any:
        if not queue:
            raise AssertionError(f"unexpected {method} call (script exhausted)")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def methods(self) -> list[str]:
        return [call.method for call in self.calls]

    async def next_question(
        self, interview: InterviewState, answer: str | None = None
    ) -> str:
        self._record("next_question", interview, answer)
        return self._take(self.question_queue, "next_question")

    async def next_step(
        self, interview: InterviewState, answer: str | None = None
    ) -> InterviewStep:
        self._record("next_step", interview, answer)
        return self._take(self.step_queue, "next_step")

    async def distil(
        self, interview: InterviewState, answer: str | None = None
    ) -> BehaviouralProfile:
        self._record("distil", interview, answer)
        return self._take(self.profile_queue, "distil")

    async def close(self) -> None:
        self.closed = True


def settings_mock() -> Any:
    settings = MagicMock()
    settings.telegram_bot_token = PLACEHOLDER_TOKEN
    settings.gemini_api_key = PLACEHOLDER_GEMINI_KEY
    return settings


async def run_updates(
    updates: list[list[Update]],
    *,
    interviewer: FakeInterviewer,
    send: AsyncMock,
    media_root: str,
    vision: AsyncMock | None = None,
    download: AsyncMock | None = None,
    now: float = DEFAULT_NOW,
) -> None:
    """Run ``run_bot`` with Telegram, the Bouncer and the Interviewer mocked.

    ``bot_gateway._now`` is pinned to ``now`` so the stale-message guard is
    deterministic (it otherwise compares against the real wall clock).

    Must be awaited *inside* the patch context (see test_bouncer_gateway.py).
    """
    mock_client = AsyncMock()
    mock_client.get_updates = AsyncMock(side_effect=updates)
    mock_client.send_message = send
    mock_client.download_file = download or AsyncMock(return_value=b"JPEG-BYTES")
    mock_client.close = AsyncMock()

    if vision is None:
        vision = AsyncMock()
        vision.check_photo = AsyncMock(return_value=ACCEPT_VERDICT)
        vision.close = AsyncMock()

    with (
        patch.object(bot_gateway, "load_settings", return_value=settings_mock()),
        patch.object(bot_gateway, "TelegramClient", return_value=mock_client),
        patch.object(bot_gateway, "GeminiVisionClient", return_value=vision),
        patch.object(bot_gateway, "InterviewerClient", return_value=interviewer),
        patch.object(bot_gateway, "_now", return_value=now),
    ):
        await bot_gateway.run_bot(
            poll_timeout=0,
            max_iterations=len(updates),
            error_backoff=0.0,
            media_root=media_root,
        )


def sent_texts(send: AsyncMock) -> list[str]:
    # ``send`` IS client.send_message (see run_updates): read its calls.
    return [call.kwargs["text"] for call in send.await_args_list]


def new_send() -> AsyncMock:
    return AsyncMock(return_value={"ok": True})


class TestAcceptStartsTheInterview:
    @pytest.mark.asyncio
    async def test_confirmation_then_q1_and_phase_interview(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        interviewer = FakeInterviewer(questions=["What is your habitat?"])

        await run_updates(
            [[photo_update(1, 555)], []],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            "What is your habitat?",
        ]
        assert interviewer.methods == ["next_question"]
        first_call = interviewer.calls[0]
        assert first_call.answer is None
        assert first_call.interview.answers == []
        assert first_call.interview.pending_question is None

    @pytest.mark.asyncio
    async def test_photo_mid_interview_nudges_and_repeats_the_question(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        interviewer = FakeInterviewer(questions=["What is your habitat?"])

        await run_updates(
            [[photo_update(1, 555)], [photo_update(2, 555)], []],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            "What is your habitat?",
            f"{bot_gateway.INTERVIEW_NUDGE}\n\nWhat is your habitat?",
        ]
        # No extra model call and no recorded answer: nothing advanced.
        assert interviewer.methods == ["next_question"]


class TestAnswerRouting:
    @pytest.mark.asyncio
    async def test_below_the_floor_asks_on_and_never_consults_next_step(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        interviewer = FakeInterviewer(questions=["Q1?", "Q2?", "Q3?"])

        await run_updates(
            [
                [photo_update(1, 555), text_update(2, 555, "A1")],
                [text_update(3, 555, "A2")],
            ],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            "Q1?",
            "Q2?",
            "Q3?",
        ]
        assert interviewer.methods == [
            "next_question",
            "next_question",
            "next_question",
        ]
        assert interviewer.calls[1].answer == "A1"
        assert interviewer.calls[2].answer == "A2"
        # The tentative answer is visible to the model, but only recorded once
        # the step resolves: the second call still sees zero completed answers.
        assert [pair.answer for pair in interviewer.calls[1].interview.answers] == []
        assert [pair.answer for pair in interviewer.calls[2].interview.answers] == [
            "A1"
        ]
        assert interviewer.calls[2].interview.answers[0].question == "Q1?"

    @pytest.mark.asyncio
    async def test_fifth_answer_may_finish_and_later_input_holds(
        self, tmp_path: Any
    ) -> None:
        finish = InterviewStep(
            action=InterviewAction.FINISH,
            profile="The subject hoards stationery.",
            suggested_animal="Eurasian magpie",
        )
        send = new_send()
        interviewer = FakeInterviewer(
            questions=["Q1?", "Q2?", "Q3?", "Q4?", "Q5?"],
            steps=[finish],
        )

        await run_updates(
            [
                [
                    photo_update(1, 555),
                    text_update(2, 555, "A1"),
                    text_update(3, 555, "A2"),
                    text_update(4, 555, "A3"),
                    text_update(5, 555, "A4"),
                    text_update(6, 555, "A5"),
                ],
                [text_update(7, 555, "are we done?")],
            ],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        texts = sent_texts(send)
        assert texts[0] == PHOTO_HOLD
        assert texts[1:3] == [ACCEPT_VERDICT.message, "Q1?"]
        assert texts[6] == "Q5?"
        assert texts[7] == bot_gateway.PROFILE_REVEAL_TEMPLATE.format(
            profile=finish.profile,
            animal=finish.suggested_animal,
        )
        # Phase advanced to CONVERT: the next message gets the holding reply.
        assert texts[8] == bot_gateway.LATER_PHASE_REPLY
        assert interviewer.methods[-1] == "next_step"
        assert interviewer.methods.count("next_step") == 1
        assert interviewer.methods.count("distil") == 0
        assert interviewer.calls[-1].answer == "A5"
        assert len(interviewer.calls[-1].interview.answers) == 4

    @pytest.mark.asyncio
    async def test_seven_answers_force_distil_instead_of_next_step(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        interviewer = FakeInterviewer(
            questions=["Q1?", "Q2?", "Q3?", "Q4?", "Q5?"],
            steps=[
                InterviewStep(action=InterviewAction.ASK, question="Q6?"),
                InterviewStep(action=InterviewAction.ASK, question="Q7?"),
            ],
            profiles=[DISTILLED_PROFILE],
        )

        await run_updates(
            [
                [
                    photo_update(1, 555),
                    text_update(2, 555, "A1"),
                    text_update(3, 555, "A2"),
                    text_update(4, 555, "A3"),
                    text_update(5, 555, "A4"),
                    text_update(6, 555, "A5"),
                    text_update(7, 555, "A6"),
                    text_update(8, 555, "A7"),
                ],
            ],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        assert interviewer.methods.count("next_step") == 2
        assert interviewer.methods.count("distil") == 1
        last = interviewer.calls[-1]
        assert last.method == "distil"
        assert last.answer == "A7"
        assert len(last.interview.answers) == 6
        # The distilled profile (not a step payload) is revealed to the user.
        assert sent_texts(send)[-1] == bot_gateway.PROFILE_REVEAL_TEMPLATE.format(
            profile=DISTILLED_PROFILE.profile,
            animal=DISTILLED_PROFILE.suggested_animal,
        )


class TestNonTextInput:
    @pytest.mark.asyncio
    async def test_sticker_and_blank_text_nudge_and_repeat(self, tmp_path: Any) -> None:
        send = new_send()
        interviewer = FakeInterviewer(questions=["Q1?", "Q2?", "Q3?"])

        await run_updates(
            [
                [
                    photo_update(1, 555),
                    sticker_update(2, 555),
                    text_update(3, 555, "   "),
                    text_update(4, 555, "A1"),
                    text_update(5, 555, "A2"),
                ],
            ],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        nudge = f"{bot_gateway.INTERVIEW_NUDGE}\n\nQ1?"
        assert sent_texts(send) == [
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            "Q1?",
            nudge,
            nudge,
            "Q2?",
            "Q3?",
        ]
        # Only real answers reached the model - one per turn, in order.
        assert interviewer.methods == [
            "next_question",
            "next_question",
            "next_question",
        ]
        assert [call.answer for call in interviewer.calls] == [None, "A1", "A2"]
        # Snapshots predate recording, so the third call (still one answer in
        # hand: empty input added nothing) sees exactly the first pair.
        assert interviewer.calls[2].interview.answers == [
            InterviewAnswer(question="Q1?", answer="A1")
        ]


class TestStaleMessageGuard:
    """Messages that predate the pending question are refused, not recorded."""

    @pytest.mark.asyncio
    async def test_message_queued_before_the_question_is_not_recorded(
        self, tmp_path: Any
    ) -> None:
        """A message already in flight when Q1 went out cannot be its answer.

        Live finding: text the user had queued before Q1 was retried was
        recorded as the answer 0.4s after the question was sent.
        """
        send = new_send()
        interviewer = FakeInterviewer(questions=["Q1?", "Q2?", "Q3?"])
        queued = int(DEFAULT_NOW) - 10  # written before the question existed

        await run_updates(
            [
                [photo_update(1, 555)],
                [text_update(2, 555, "I already told you", date=queued)],
                [text_update(3, 555, "A1")],
            ],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            "Q1?",
            f"{bot_gateway.INTERVIEW_NUDGE}\n\nQ1?",
            "Q2?",
        ]
        # Only the fresh message was ever an answer; the queued one was not.
        assert [call.answer for call in interviewer.calls] == [None, "A1"]

    @pytest.mark.asyncio
    async def test_message_within_the_clock_slack_is_still_an_answer(
        self, tmp_path: Any
    ) -> None:
        """Telegram's 1s timestamps and clock skew must not eat real answers."""
        send = new_send()
        interviewer = FakeInterviewer(questions=["Q1?", "Q2?"])
        near_question = int(DEFAULT_NOW) - 2  # inside QUESTION_CLOCK_SLACK_SECONDS

        await run_updates(
            [
                [photo_update(1, 555)],
                [text_update(2, 555, "A1", date=near_question)],
            ],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            "Q1?",
            "Q2?",
        ]
        assert [call.answer for call in interviewer.calls] == [None, "A1"]

    @pytest.mark.asyncio
    async def test_question_send_time_is_stamped_when_the_question_goes_out(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        interviewer = FakeInterviewer(questions=["Q1?", "Q2?"])

        await run_updates(
            [[photo_update(1, 555)], [text_update(2, 555, "A1")]],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        # Nothing pending at the first call; stamped once Q1 reached the user.
        assert interviewer.calls[0].interview.question_sent_at is None
        assert interviewer.calls[1].interview.question_sent_at == DEFAULT_NOW


class TestFailureBehaviour:
    @pytest.mark.asyncio
    async def test_model_failure_keeps_state_and_a_resend_does_not_duplicate(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        interviewer = FakeInterviewer(
            questions=[
                "Q1?",
                GeminiApiError("503 from Gemini"),
                "Q2?",
                "Q3?",
            ]
        )

        await run_updates(
            [
                [photo_update(1, 555), text_update(2, 555, "A1")],
                [text_update(3, 555, "A1"), text_update(4, 555, "A2")],
            ],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            "Q1?",
            bot_gateway.INTERVIEW_RETRY_MSG,
            "Q2?",
            "Q3?",
        ]
        # The failed call recorded nothing (still zero answers in hand) and the
        # retried answer is recorded exactly once.
        assert [len(call.interview.answers) for call in interviewer.calls] == [
            0,
            0,
            0,
            1,
        ]
        assert interviewer.calls[1].answer == "A1"
        assert interviewer.calls[2].answer == "A1"

    @pytest.mark.asyncio
    async def test_text_without_a_pending_question_reasks_and_is_not_recorded(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        interviewer = FakeInterviewer(
            questions=[
                GeminiApiError("503 from Gemini"),
                "Q1?",
                "Q2?",
                "Q3?",
            ]
        )

        await run_updates(
            [
                [photo_update(1, 555), text_update(2, 555, "chatter")],
                [text_update(3, 555, "A1"), text_update(4, 555, "A2")],
            ],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            bot_gateway.INTERVIEW_RETRY_MSG,
            "Q1?",
            "Q2?",
            "Q3?",
        ]
        # Both asks carried no answer (None) - "chatter" was never recorded.
        assert [call.answer for call in interviewer.calls[:2]] == [None, None]
        assert [len(call.interview.answers) for call in interviewer.calls[:2]] == [0, 0]
        assert [call.answer for call in interviewer.calls[2:]] == ["A1", "A2"]
        # Exactly one pair is in hand by the last call: "chatter" is not a turn.
        assert interviewer.calls[-1].interview.answers == [
            InterviewAnswer(question="Q1?", answer="A1")
        ]

    @pytest.mark.asyncio
    async def test_failed_send_of_a_question_leaves_it_unpending(
        self, tmp_path: Any
    ) -> None:
        send = AsyncMock(
            side_effect=[
                {"ok": True},  # photo hold-line
                {"ok": True},  # acceptance confirmation
                bot_gateway.TelegramApiError("sendMessage failed"),  # Q1 fails
                {"ok": True},  # Q1 re-asked after the user's next message
                {"ok": True},  # Q2 after the answer
            ]
        )
        interviewer = FakeInterviewer(questions=["Q1?", "Q1?", "Q2?"])

        await run_updates(
            [
                [
                    photo_update(1, 555),
                    text_update(2, 555, "chatter"),
                    text_update(3, 555, "A1"),
                ],
            ],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        # The Q1 send failed mid-flight (its text is recorded by the mock),
        # but the user never received it - so the next message re-asks it.
        assert sent_texts(send) == [
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            "Q1?",
            "Q1?",
            "Q2?",
        ]
        # The question that never reached the user was never made pending, so
        # "chatter" triggered a re-ask (no answer) rather than being recorded.
        assert [call.answer for call in interviewer.calls] == [None, None, "A1"]
        assert [len(call.interview.answers) for call in interviewer.calls] == [0, 0, 0]


class TestChatIsolationAndLifecycle:
    @pytest.mark.asyncio
    async def test_two_chats_never_share_answers(self, tmp_path: Any) -> None:
        send = new_send()
        interviewer = FakeInterviewer(
            questions=[
                "Q for 555 #1?",
                "Q for 777 #1?",
                "Q for 555 #2?",
                "Q for 777 #2?",
                "Q for 555 #3?",
                "Q for 777 #3?",
            ]
        )

        await run_updates(
            [
                [photo_update(1, 555), photo_update(2, 777)],
                [
                    text_update(3, 555, "A-555"),
                    text_update(4, 777, "A-777"),
                ],
                [
                    text_update(5, 555, "A-555b"),
                    text_update(6, 777, "A-777b"),
                ],
            ],
            interviewer=interviewer,
            send=send,
            media_root=str(tmp_path),
        )

        assert sent_texts(send) == [
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            "Q for 555 #1?",
            PHOTO_HOLD,
            ACCEPT_VERDICT.message,
            "Q for 777 #1?",
            "Q for 555 #2?",
            "Q for 777 #2?",
            "Q for 555 #3?",
            "Q for 777 #3?",
        ]
        # Snapshots predate recording, so the third turn of each chat shows
        # exactly its own first pair - and never the other chat's.
        first_chat = interviewer.calls[4].interview
        second_chat = interviewer.calls[5].interview
        assert first_chat.pending_question == "Q for 555 #2?"
        assert second_chat.pending_question == "Q for 777 #2?"
        assert [(p.question, p.answer) for p in first_chat.answers] == [
            ("Q for 555 #1?", "A-555")
        ]
        assert [(p.question, p.answer) for p in second_chat.answers] == [
            ("Q for 777 #1?", "A-777")
        ]

    @pytest.mark.asyncio
    async def test_the_interviewer_is_closed_on_shutdown(self, tmp_path: Any) -> None:
        interviewer = FakeInterviewer()

        await run_updates(
            [[]],
            interviewer=interviewer,
            send=new_send(),
            media_root=str(tmp_path),
        )

        assert interviewer.closed is True
