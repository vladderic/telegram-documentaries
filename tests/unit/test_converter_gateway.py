"""Unit tests for the Converter wired into the gateway (Phase 4 routing).

Telegram, the Bouncer's vision client, the Interviewer and the Converter are
all fakes: no test contacts a real API (TECH.md). The Converter fake records
the bytes and dossier it was handed, so the wiring can be asserted without
poking at ``run_bot``'s internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram_documentaries import bot_gateway, bouncer
from telegram_documentaries.gemini import GeminiApiError
from telegram_documentaries.models import (
    BehaviouralProfile,
    BouncerVerdict,
    GeneratedImage,
    InterviewAction,
    InterviewStep,
    Script,
    Update,
)

PLACEHOLDER_TOKEN = "123456-PLACEHOLDER-TELEGRAM-BOT-TOKEN"
PLACEHOLDER_GEMINI_KEY = "PLACEHOLDER-GEMINI-API-KEY"

ACCEPT_VERDICT = BouncerVerdict(
    accepted=True,
    observed_subject="a single person",
    message="Welcome to the shoot - let's begin.",
)
FINISH_PROFILE = BehaviouralProfile(
    profile="The subject hoards stationery.",
    suggested_animal="Eurasian magpie",
    animal_description="A shameless thief of anything shiny.",
)
DISTILLED_PROFILE = BehaviouralProfile(
    profile="The subject patrols the kitchen at midnight, seeking snacks.",
    suggested_animal="Red fox",
    animal_description="A lone hunter of the small hours.",
)
IMAGE = GeneratedImage(mime_type="image/jpeg", data=b"HYBRID-PORTRAIT-BYTES")
#: The narration the Scripter fake returns, so the flow past the Converter can
#: be asserted here too (Phase 5 always runs after the portrait, success or
#: failure).
NARRATION = "Our subject rises at dusk and patrols the kitchen with intent."
#: The bytes the mocked download hands back, so the Bouncer stores - and the
#: Converter must receive - exactly these.
PORTRAIT_BYTES = b"STORED-PORTRAIT-BYTES"
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


def document_update(
    update_id: int,
    chat_id: int,
    *,
    mime_type: str | None = "image/png",
    file_size: int | None = 2_048_000,
    file_name: str | None = "portrait.png",
    caption: str | None = None,
) -> Update:
    """A portrait sent as a *file*: Telegram preserves the original bytes."""
    message: dict[str, object] = {
        "message_id": update_id,
        "date": 1_760_000_100,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
        "document": {
            "file_id": f"d{update_id}",
            "file_unique_id": f"du{update_id}",
            "file_name": file_name,
            "mime_type": mime_type,
            "file_size": file_size,
        },
    }
    if caption is not None:
        message["caption"] = caption
    return Update.model_validate({"update_id": update_id, "message": message})


def text_update(
    update_id: int, chat_id: int, text: str, *, date: int = 1_760_000_200
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


@dataclass
class FuseCall:
    """One recorded call into the converter fake."""

    portrait: bytes
    mime_type: str
    profile: BehaviouralProfile


class FakeConverter:
    """Scripted, recording stand-in for :class:`ConverterClient`.

    Each queue item is either a value to return or an exception to raise, so
    happy paths and model failures are scripted the same way.
    """

    def __init__(self, results: list[Any] | None = None) -> None:
        self.queue: list[Any] = list(results if results is not None else [IMAGE])
        self.calls: list[FuseCall] = []
        self.closed = False

    async def fuse(
        self, portrait: bytes, mime_type: str, profile: BehaviouralProfile
    ) -> GeneratedImage:
        self.calls.append(FuseCall(portrait, mime_type, profile))
        if not self.queue:
            raise AssertionError("unexpected fuse call (script exhausted)")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


class FakeScripter:
    """Minimal stand-in for :class:`ScripterClient` (one canned narration)."""

    def __init__(self) -> None:
        self.closed = False

    async def write_script(self, profile: BehaviouralProfile) -> Script:
        return Script(text=NARRATION)

    async def close(self) -> None:
        self.closed = True


class FakeInterviewer:
    """Scripted stand-in for :class:`InterviewerClient` (no recording needed)."""

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
        self.closed = False

    def _take(self, queue: list[Any], method: str) -> Any:
        if not queue:
            raise AssertionError(f"unexpected {method} call (script exhausted)")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def next_question(self, interview: Any, answer: str | None = None) -> str:
        return self._take(self.question_queue, "next_question")

    async def next_step(self, interview: Any, answer: str | None = None) -> Any:
        return self._take(self.step_queue, "next_step")

    async def distil(self, interview: Any, answer: str | None = None) -> Any:
        return self._take(self.profile_queue, "distil")

    async def close(self) -> None:
        self.closed = True


def settings_mock() -> Any:
    settings = MagicMock()
    settings.telegram_bot_token = PLACEHOLDER_TOKEN
    settings.gemini_api_key = PLACEHOLDER_GEMINI_KEY
    return settings


def new_send() -> AsyncMock:
    return AsyncMock(return_value={"ok": True})


def new_send_photo() -> AsyncMock:
    return AsyncMock(return_value={"ok": True})


async def run_updates(
    updates: list[list[Update]],
    *,
    interviewer: FakeInterviewer,
    converter: FakeConverter,
    send: AsyncMock,
    send_photo: AsyncMock,
    media_root: str,
    vision: AsyncMock | None = None,
) -> None:
    """Run ``run_bot`` with Telegram, the Bouncer, the Interviewer, the
    Converter and the Scripter mocked. Must be awaited inside the patch
    context."""
    mock_client = AsyncMock()
    mock_client.get_updates = AsyncMock(side_effect=updates)
    mock_client.send_message = send
    mock_client.send_photo = send_photo
    mock_client.download_file = AsyncMock(return_value=PORTRAIT_BYTES)
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
        patch.object(bot_gateway, "ConverterClient", return_value=converter),
        patch.object(bot_gateway, "ScripterClient", return_value=FakeScripter()),
        patch.object(bot_gateway, "_now", return_value=DEFAULT_NOW),
    ):
        await bot_gateway.run_bot(
            poll_timeout=0,
            max_iterations=len(updates),
            error_backoff=0.0,
            media_root=media_root,
        )


def sent_texts(send: AsyncMock) -> list[str]:
    return [call.kwargs["text"] for call in send.await_args_list]


def model_finish_script() -> FakeInterviewer:
    """A five-answer interview the model chooses to finish."""
    return FakeInterviewer(
        questions=["Q1?", "Q2?", "Q3?", "Q4?", "Q5?"],
        steps=[
            InterviewStep(
                action=InterviewAction.FINISH,
                profile=FINISH_PROFILE.profile,
                suggested_animal=FINISH_PROFILE.suggested_animal,
                animal_description=FINISH_PROFILE.animal_description,
            )
        ],
    )


FINISH_UPDATES = [
    [photo_update(1, 555)],
    [
        text_update(2, 555, "A1"),
        text_update(3, 555, "A2"),
        text_update(4, 555, "A3"),
        text_update(5, 555, "A4"),
        text_update(6, 555, "A5"),
    ],
]

#: The five answers that push an interview past its ceiling.
ANSWER_UPDATES = [
    text_update(2, 555, "A1"),
    text_update(3, 555, "A2"),
    text_update(4, 555, "A3"),
    text_update(5, 555, "A4"),
    text_update(6, 555, "A5"),
]


class TestFinishRoutesReachTheConverter:
    @pytest.mark.asyncio
    async def test_model_finish_sends_reveal_then_hold_then_captioned_photo(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        send_photo = new_send_photo()
        converter = FakeConverter()
        interviewer = model_finish_script()

        await run_updates(
            FINISH_UPDATES,
            interviewer=interviewer,
            converter=converter,
            send=send,
            send_photo=send_photo,
            media_root=str(tmp_path),
        )

        texts = sent_texts(send)
        assert texts[-3] == bot_gateway.PROFILE_REVEAL_TEMPLATE.format(
            profile=FINISH_PROFILE.profile,
            animal=FINISH_PROFILE.suggested_animal,
            animal_description=FINISH_PROFILE.animal_description,
        )
        assert texts[-2] == bot_gateway.PORTRAIT_HOLD_MSG
        # And the narrator still has the last word (Phase 5).
        assert texts[-1] == NARRATION

        assert send_photo.await_count == 1
        photo_call = send_photo.await_args_list[0].kwargs
        assert photo_call["chat_id"] == 555
        assert photo_call["image"] == IMAGE
        assert photo_call["caption"] == bot_gateway.PORTRAIT_CAPTION_TEMPLATE.format(
            animal=FINISH_PROFILE.suggested_animal
        )
        assert "Eurasian magpie" in photo_call["caption"]
        assert bot_gateway.PORTRAIT_FAILURE_MSG not in texts
        # The Converter ran exactly once, on the stored portrait and dossier.
        assert len(converter.calls) == 1
        assert converter.calls[0].portrait == PORTRAIT_BYTES
        assert converter.calls[0].mime_type == "image/jpeg"
        assert converter.calls[0].profile == FINISH_PROFILE

    @pytest.mark.asyncio
    async def test_ceiling_distil_path_also_reaches_the_converter(
        self, tmp_path: Any
    ) -> None:
        """The Phase 3 "finish route missed the new stage" bug class."""
        send = new_send()
        send_photo = new_send_photo()
        converter = FakeConverter()
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
                [photo_update(1, 555)],
                [text_update(i, 555, f"A{i}") for i in range(2, 9)],
            ],
            interviewer=interviewer,
            converter=converter,
            send=send,
            send_photo=send_photo,
            media_root=str(tmp_path),
        )

        assert sent_texts(send)[-2] == bot_gateway.PORTRAIT_HOLD_MSG
        assert sent_texts(send)[-1] == NARRATION
        assert send_photo.await_count == 1
        assert converter.calls[0].profile == DISTILLED_PROFILE
        assert "Red fox" in send_photo.await_args_list[0].kwargs["caption"]

    @pytest.mark.asyncio
    async def test_the_converter_receives_the_stored_portrait_byte_for_byte(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        send_photo = new_send_photo()
        converter = FakeConverter()
        interviewer = model_finish_script()

        await run_updates(
            FINISH_UPDATES,
            interviewer=interviewer,
            converter=converter,
            send=send,
            send_photo=send_photo,
            media_root=str(tmp_path),
        )

        stored = Path(tmp_path, "555", "portrait.jpg")
        assert stored.read_bytes() == PORTRAIT_BYTES
        assert converter.calls[0].portrait == stored.read_bytes()


class TestDocumentPortrait:
    """Phase 4b: a file portrait goes through the exact same door as a photo."""

    @pytest.mark.asyncio
    async def test_the_log_says_which_kind_of_upload_arrived(
        self, tmp_path: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Live validation reads the log: the kind must be there to read."""
        vision = AsyncMock()
        vision.check_photo = AsyncMock(return_value=ACCEPT_VERDICT)
        vision.close = AsyncMock()

        with caplog.at_level("INFO"):
            await run_updates(
                [[document_update(1, 555, mime_type="image/png")]],
                interviewer=model_finish_script(),
                converter=FakeConverter(),
                send=new_send(),
                send_photo=new_send_photo(),
                media_root=str(tmp_path),
                vision=vision,
            )

        received = [
            r.getMessage()
            for r in caplog.records
            if "portrait_received" in r.getMessage()
        ]
        assert len(received) == 1
        assert "kind=document" in received[0]
        assert "mime=image/png" in received[0]
        # Both raw flags are logged, so "photo wins" can never hide a file.
        assert "has_document=True" in received[0]
        assert "has_photo=False" in received[0]

    @pytest.mark.asyncio
    async def test_a_file_portrait_is_accepted_stored_and_passed_on_truthfully(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        send_photo = new_send_photo()
        converter = FakeConverter()
        interviewer = model_finish_script()
        vision = AsyncMock()
        vision.check_photo = AsyncMock(return_value=ACCEPT_VERDICT)
        vision.close = AsyncMock()

        await run_updates(
            [
                [
                    document_update(
                        1, 555, mime_type="image/png", file_name="portrait.png"
                    )
                ],
                ANSWER_UPDATES,
            ],
            interviewer=interviewer,
            converter=converter,
            send=send,
            send_photo=send_photo,
            media_root=str(tmp_path),
            vision=vision,
        )

        # Same door: hold line, one vision call, the verdict, the interview.
        assert sent_texts(send)[0] == bot_gateway.PHOTO_HOLD_MSG
        assert vision.check_photo.await_args.args == (PORTRAIT_BYTES, "image/png")
        # Original bytes stored under an extension that matches their type.
        stored = Path(tmp_path, "555", "portrait.png")
        assert stored.read_bytes() == PORTRAIT_BYTES
        # The Converter is told what the file actually is, not "image/jpeg".
        assert converter.calls[0].mime_type == "image/png"
        assert converter.calls[0].portrait == PORTRAIT_BYTES
        assert send_photo.await_count == 1

    @pytest.mark.asyncio
    async def test_a_document_without_an_image_type_gets_the_photo_nudge(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        vision = AsyncMock()
        vision.check_photo = AsyncMock(return_value=ACCEPT_VERDICT)
        vision.close = AsyncMock()

        await run_updates(
            [
                [
                    document_update(
                        1, 555, mime_type="application/pdf", file_name="cv.pdf"
                    )
                ]
            ],
            interviewer=model_finish_script(),
            converter=FakeConverter(),
            send=send,
            send_photo=new_send_photo(),
            media_root=str(tmp_path),
            vision=vision,
        )

        assert sent_texts(send) == [bot_gateway.PHOTO_NUDGE]
        vision.check_photo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_oversized_document_is_refused_before_the_vision_call(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        vision = AsyncMock()
        vision.check_photo = AsyncMock(return_value=ACCEPT_VERDICT)
        vision.close = AsyncMock()

        await run_updates(
            [
                [
                    document_update(
                        1,
                        555,
                        file_size=bouncer.MAX_PHOTO_BYTES + 1,
                        file_name="huge.png",
                    )
                ]
            ],
            interviewer=model_finish_script(),
            converter=FakeConverter(),
            send=send,
            send_photo=new_send_photo(),
            media_root=str(tmp_path),
            vision=vision,
        )

        assert bot_gateway.PHOTO_TOO_LARGE_MSG in sent_texts(send)
        # No transfer, no judgment: the cap is enforced on the declared size.
        vision.check_photo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_document_mid_interview_is_still_not_an_answer(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        vision = AsyncMock()
        vision.check_photo = AsyncMock(return_value=ACCEPT_VERDICT)
        vision.close = AsyncMock()
        interviewer = FakeInterviewer(questions=["First question?", "Second question?"])

        await run_updates(
            [
                [photo_update(1, 555)],
                [document_update(2, 555)],
                [text_update(3, 555, "A1")],
            ],
            interviewer=interviewer,
            converter=FakeConverter(),
            send=send,
            send_photo=new_send_photo(),
            media_root=str(tmp_path),
            vision=vision,
        )

        texts = sent_texts(send)
        assert texts[-2] == f"{bot_gateway.INTERVIEW_NUDGE}\n\nFirst question?"
        assert texts[-1] == "Second question?"

    @pytest.mark.asyncio
    async def test_a_document_after_the_portrait_gets_the_holding_reply(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        vision = AsyncMock()
        vision.check_photo = AsyncMock(return_value=ACCEPT_VERDICT)
        vision.close = AsyncMock()

        await run_updates(
            FINISH_UPDATES + [[document_update(7, 555)]],
            interviewer=model_finish_script(),
            converter=FakeConverter(),
            send=send,
            send_photo=new_send_photo(),
            media_root=str(tmp_path),
            vision=vision,
        )

        assert sent_texts(send)[-1] == bot_gateway.LATER_PHASE_REPLY
        assert vision.check_photo.await_count == 1


class TestPortraitFailurePaths:
    @pytest.mark.asyncio
    async def test_converter_failure_still_narrates_and_holds_afterwards(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        send_photo = new_send_photo()
        converter = FakeConverter([GeminiApiError("image model down")])
        interviewer = model_finish_script()

        await run_updates(
            FINISH_UPDATES + [[text_update(7, 555, "still there?")]],
            interviewer=interviewer,
            converter=converter,
            send=send,
            send_photo=send_photo,
            media_root=str(tmp_path),
        )

        texts = sent_texts(send)
        # The darkroom jammed - but a jammed darkroom never silences the
        # narrator (Phase 5 decision), so the script still goes out.
        assert texts[-3] == bot_gateway.PORTRAIT_FAILURE_MSG
        assert texts[-2] == NARRATION
        # Past SCRIPT: the follow-up gets the holding reply, not a new run.
        assert texts[-1] == bot_gateway.LATER_PHASE_REPLY
        assert send_photo.await_count == 0
        assert len(converter.calls) == 1

    @pytest.mark.asyncio
    async def test_telegram_upload_failure_degrades_in_character(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        send_photo = AsyncMock(side_effect=bot_gateway.TelegramApiError("boom"))
        converter = FakeConverter()
        interviewer = model_finish_script()

        await run_updates(
            FINISH_UPDATES,
            interviewer=interviewer,
            converter=converter,
            send=send,
            send_photo=send_photo,
            media_root=str(tmp_path),
        )

        assert sent_texts(send)[-2] == bot_gateway.PORTRAIT_FAILURE_MSG
        assert sent_texts(send)[-1] == NARRATION
        assert send_photo.await_count == 1

    @pytest.mark.asyncio
    async def test_missing_portrait_file_degrades_in_character(
        self, tmp_path: Any
    ) -> None:
        send = new_send()
        send_photo = new_send_photo()
        converter = FakeConverter()
        interviewer = model_finish_script()

        with patch.object(Path, "read_bytes", side_effect=OSError("gone")):
            await run_updates(
                FINISH_UPDATES,
                interviewer=interviewer,
                converter=converter,
                send=send,
                send_photo=send_photo,
                media_root=str(tmp_path),
            )

        assert sent_texts(send)[-2] == bot_gateway.PORTRAIT_FAILURE_MSG
        assert sent_texts(send)[-1] == NARRATION
        assert converter.calls == []
        assert send_photo.await_count == 0

    @pytest.mark.asyncio
    async def test_the_send_failure_line_never_raises(self, tmp_path: Any) -> None:
        """Even the failure message failing must not kill the loop."""
        send = AsyncMock(side_effect=bot_gateway.TelegramApiError("send down"))
        converter = FakeConverter()
        interviewer = model_finish_script()

        await run_updates(
            FINISH_UPDATES,
            interviewer=interviewer,
            converter=converter,
            send=send,
            send_photo=new_send_photo(),
            media_root=str(tmp_path),
        )

        # run_bot completed (no exception) - the loop stayed alive.
        assert converter.closed is True


class TestConverterLifecycle:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM"])
    async def test_the_converter_pool_is_closed_once_on_shutdown(
        self, tmp_path: Any, signal_name: str
    ) -> None:
        """SIGINT and SIGTERM both land in the same shutdown event."""
        import signal

        converter = FakeConverter()
        interviewer = FakeInterviewer()
        sig = getattr(signal, signal_name)

        async def fire(updates: list[list[Update]]) -> None:
            await run_updates(
                updates,
                interviewer=interviewer,
                converter=converter,
                send=new_send(),
                send_photo=new_send_photo(),
                media_root=str(tmp_path),
            )

        # Real handler registration is asserted separately; here the event the
        # handler sets is raised the moment it is installed (as a signal would).
        def install(event: Any) -> None:
            assert sig in (signal.SIGINT, signal.SIGTERM)
            event.set()

        with patch.object(bot_gateway, "_install_signal_handlers", install):
            await fire([[]])

        assert converter.closed is True

    @pytest.mark.asyncio
    async def test_the_converter_is_closed_on_a_plain_shutdown(
        self, tmp_path: Any
    ) -> None:
        converter = FakeConverter()

        await run_updates(
            [[]],
            interviewer=FakeInterviewer(),
            converter=converter,
            send=new_send(),
            send_photo=new_send_photo(),
            media_root=str(tmp_path),
        )

        assert converter.closed is True


class TestNoSecondRunAfterThePortrait:
    @pytest.mark.asyncio
    async def test_a_photo_after_the_portrait_gets_the_holding_reply(
        self, tmp_path: Any
    ) -> None:
        """Sweep lock: no route re-enters an earlier stage (no new run)."""
        send = new_send()
        send_photo = new_send_photo()
        converter = FakeConverter()
        interviewer = model_finish_script()
        vision = AsyncMock()
        vision.check_photo = AsyncMock(return_value=ACCEPT_VERDICT)
        vision.close = AsyncMock()

        await run_updates(
            FINISH_UPDATES
            + [
                [photo_update(7, 555)],
                [text_update(8, 555, "and another thing")],
            ],
            interviewer=interviewer,
            converter=converter,
            send=send,
            send_photo=send_photo,
            media_root=str(tmp_path),
            vision=vision,
        )

        texts = sent_texts(send)
        assert texts[-2] == bot_gateway.LATER_PHASE_REPLY  # the later photo
        assert texts[-1] == bot_gateway.LATER_PHASE_REPLY  # the late text
        # The Bouncer judged one photo and the Converter ran once: no new run.
        assert vision.check_photo.await_count == 1
        assert send_photo.await_count == 1
        assert len(converter.calls) == 1
