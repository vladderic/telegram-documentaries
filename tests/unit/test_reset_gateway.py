"""Gateway tests for the Phase 7 ``/start`` / ``/restart`` reset.

Telegram, the Bouncer, and every model client are mocked: no test contacts a
real API (TECH.md). Most rows drive ``_dispatch_update`` directly (explicit
``sessions`` dict -> direct state assertions); the full-loop row drives
``run_bot`` with mocked ``get_updates`` batch side-effects, in the harness
style of ``test_scripter_gateway.py``. All secrets are placeholders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram_documentaries import bot_gateway
from telegram_documentaries.models import (
    BehaviouralProfile,
    BouncerVerdict,
    GeneratedImage,
    InterviewAction,
    InterviewAnswer,
    InterviewState,
    InterviewStep,
    Phase,
    Script,
    SessionState,
    Update,
)
from telegram_documentaries.narrator import VoiceNote

PLACEHOLDER_TOKEN = "123456:PLACEHOLDER-TELEGRAM-BOT-TOKEN"
PLACEHOLDER_GEMINI_KEY = "PLACEHOLDER-GEMINI-API-KEY"

#: The clock every dispatched message predates by a safe margin, so the
#: stale-message guard never eats a fixture answer.
DEFAULT_NOW = 1_760_000_000.0
PORTRAIT_BYTES = b"STORED-PORTRAIT-BYTES"
ACCEPT_VERDICT = BouncerVerdict(
    accepted=True,
    observed_subject="a single person",
    message="Welcome to the shoot - let's begin.",
)
PROFILE = BehaviouralProfile(
    profile="The subject hoards stationery.",
    suggested_animal="Eurasian magpie",
    animal_description="A shameless thief of anything shiny.",
)
NARRATION = "Our subject rises at dusk and patrols the kitchen with intent."
CHAT_ID = 555

#: The contract copy (requirements.md), asserted verbatim below.
EXPECTED_CONFIRM = (
    "Very well - the cutting room floor is clear. Every previous take, "
    "note and frame of you has been struck from the archive."
)
EXPECTED_GREETING = (
    "A fresh subject! The crew has been ready. Send me a photograph and "
    "we will get you on camera."
)
EXPECTED_REPLY = f"{EXPECTED_CONFIRM}\n\n{EXPECTED_GREETING}"


def photo_update(
    update_id: int, chat_id: int = CHAT_ID, *, caption: str | None = None
) -> Update:
    message: dict[str, Any] = {
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
    }
    if caption is not None:
        # Real Telegram shape: a command caption travels in ``caption_entities``.
        message["caption"] = caption
        message["caption_entities"] = [
            {"type": "bot_command", "offset": 0, "length": len(caption)}
        ]
    return Update.model_validate({"update_id": update_id, "message": message})


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


def command_update(
    update_id: int,
    chat_id: int = CHAT_ID,
    text: str = "/start",
    *,
    with_entity: bool = True,
    date: int = 1_760_000_300,
) -> Update:
    """A text message, optionally carrying Telegram's ``bot_command`` entity."""
    payload: dict[str, Any] = {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": date,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
            "text": text,
        },
    }
    if with_entity:
        token = text.split()[0] if text.split() else ""
        payload["message"]["entities"] = [
            {"type": "bot_command", "offset": 0, "length": len(token)}
        ]
    return Update.model_validate(payload)


@dataclass
class InterviewerCall:
    method: str
    answer: str | None


class RecordingInterviewer:
    """Scripted, recording stand-in for :class:`InterviewerClient`."""

    def __init__(
        self,
        questions: list[str] | None = None,
        steps: list[Any] | None = None,
    ) -> None:
        self.question_queue: list[str] = list(questions or ["Q1?", "Q2?", "Q3?"])
        self.step_queue: list[Any] = list(steps or [])
        self.calls: list[InterviewerCall] = []

    def _take(self, queue: list[Any], method: str) -> Any:
        if not queue:
            raise AssertionError(f"unexpected {method} call (script exhausted)")
        return queue.pop(0)

    async def next_question(
        self, interview: InterviewState, answer: str | None = None
    ) -> str:
        self.calls.append(InterviewerCall("next_question", answer))
        return cast(str, self._take(self.question_queue, "next_question"))

    async def next_step(
        self, interview: InterviewState, answer: str | None = None
    ) -> InterviewStep:
        self.calls.append(InterviewerCall("next_step", answer))
        return cast(InterviewStep, self._take(self.step_queue, "next_step"))

    async def distil(
        self, interview: InterviewState, answer: str | None = None
    ) -> BehaviouralProfile:
        self.calls.append(InterviewerCall("distil", answer))
        raise AssertionError("unexpected distil call (ceiling not reached)")

    async def close(self) -> None:
        return None


class FakeConverter:
    async def fuse(
        self, portrait: bytes, mime_type: str, profile: BehaviouralProfile
    ) -> GeneratedImage:
        return GeneratedImage(mime_type="image/jpeg", data=b"HYBRID-BYTES")

    async def close(self) -> None:
        return None


class FakeScripter:
    async def write_script(self, profile: BehaviouralProfile) -> Script:
        return Script(text=NARRATION)

    async def close(self) -> None:
        return None


class FakeNarrator:
    async def speak(self, text: str) -> VoiceNote:
        return VoiceNote(data=b"MP3-BYTES", mime_type="audio/mpeg", duration=7)

    async def close(self) -> None:
        return None


def new_client() -> AsyncMock:
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"ok": True})
    client.send_photo = AsyncMock(return_value={"ok": True})
    client.send_voice = AsyncMock(return_value={"ok": True})
    client.download_file = AsyncMock(return_value=PORTRAIT_BYTES)
    return client


def accept_vision() -> AsyncMock:
    vision = AsyncMock()
    vision.check_photo = AsyncMock(return_value=ACCEPT_VERDICT)
    return vision


async def dispatch(
    update: Update,
    *,
    sessions: dict[int, SessionState],
    media_root: str | Path,
    client: AsyncMock | None = None,
    interviewer: Any = None,
    vision: AsyncMock | None = None,
) -> AsyncMock:
    """Dispatch one update straight through the router (direct state access)."""
    client = client or new_client()
    with patch.object(bot_gateway, "_now", return_value=DEFAULT_NOW):
        await bot_gateway._dispatch_update(
            client,
            vision or accept_vision(),
            cast(Any, FakeConverter()),
            cast(Any, FakeScripter()),
            cast(Any, FakeNarrator()),
            cast(Any, interviewer or RecordingInterviewer()),
            sessions,
            media_root,
            update,
        )
    return client


def sent_texts(client: AsyncMock) -> list[str]:
    return [call.kwargs["text"] for call in client.send_message.await_args_list]


def running_session(chat_id: int, phase: Phase, media_dir: Path) -> SessionState:
    """A mid-run session with media on disk and a populated interview buffer."""
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "portrait.jpg").write_bytes(b"STORED-BYTES")
    session = SessionState(
        chat_id=chat_id,
        phase=phase,
        photo_file_id="fid-1",
        photo_local_path=str(media_dir / "portrait.jpg"),
        photo_mime_type="image/png",
        temp_media_path=str(media_dir),
        script_text="The subject stirs at dawn.",
        voice_note_sent=True,
    )
    session.interview.answers.append(InterviewAnswer(question="Q1?", answer="A1"))
    session.interview.pending_question = "Q2?"
    session.interview.question_sent_at = DEFAULT_NOW
    session.interview.profile = PROFILE
    return session


def assert_session_cleared(session: SessionState) -> None:
    assert session.phase is Phase.AWAITING_PHOTO
    assert session.photo_file_id is None
    assert session.photo_local_path is None
    assert session.photo_mime_type == "image/jpeg"
    assert session.temp_media_path is None
    assert session.script_text is None
    assert session.voice_note_sent is False
    assert session.interview.answers == []
    assert session.interview.pending_question is None
    assert session.interview.profile is None
    assert session.interview.question_sent_at is None
    assert session.sent_message_ids == []


class TestResetCopy:
    """The reply copy is a contract (requirements.md), down to the dash."""

    def test_the_reply_is_one_message_of_confirmation_then_greeting(self) -> None:
        assert bot_gateway.RESET_CONFIRM_MSG == EXPECTED_CONFIRM
        assert bot_gateway.GREETING_MSG == EXPECTED_GREETING
        assert bot_gateway.RESET_REPLY == EXPECTED_REPLY

    def test_the_confirmation_keeps_the_house_ascii_dash_convention(self) -> None:
        """Every other ``*_MSG`` constant uses " - ", never an em dash."""
        assert " - " in bot_gateway.RESET_CONFIRM_MSG
        assert "—" not in bot_gateway.RESET_REPLY


class TestDeleteSentMessages:
    """Phase 7 chat-clear slice: best-effort deletion of the bot's messages."""

    async def test_deletes_every_tracked_id_and_returns_counts(self) -> None:
        session = SessionState(chat_id=CHAT_ID, sent_message_ids=[10, 11, 12])
        client = new_client()

        deleted, attempted = await bot_gateway._delete_sent_messages(client, session)

        assert (deleted, attempted) == (3, 3)
        assert [
            c.kwargs["message_id"] for c in client.delete_message.await_args_list
        ] == [
            10,
            11,
            12,
        ]
        assert all(
            c.kwargs["chat_id"] == CHAT_ID
            for c in client.delete_message.await_args_list
        )

    async def test_a_failure_on_one_id_does_not_stop_the_others(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = SessionState(chat_id=CHAT_ID, sent_message_ids=[10, 11, 12])
        client = new_client()

        async def flaky(chat_id: int, message_id: int) -> None:
            if message_id == 11:
                raise bot_gateway.TelegramApiError("message to delete not found")

        client.delete_message = AsyncMock(side_effect=flaky)

        with caplog.at_level(logging.WARNING):
            deleted, attempted = await bot_gateway._delete_sent_messages(
                client, session
            )

        assert (deleted, attempted) == (2, 3)
        assert "failed to delete" in caplog.text
        assert "11" in caplog.text
        # The other two were still attempted.
        assert client.delete_message.await_count == 3

    async def test_a_brand_new_chat_deletes_nothing(self) -> None:
        session = SessionState(chat_id=CHAT_ID)
        client = new_client()

        deleted, attempted = await bot_gateway._delete_sent_messages(client, session)

        assert (deleted, attempted) == (0, 0)
        client.delete_message.assert_not_awaited()

    async def test_it_never_raises_and_leaks_no_secrets(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = SessionState(chat_id=CHAT_ID, sent_message_ids=[10])
        client = new_client()
        client.delete_message = AsyncMock(
            side_effect=bot_gateway.TelegramApiError(
                f"Telegram API deleteMessage failed: {PLACEHOLDER_TOKEN}"
            )
        )

        with caplog.at_level(logging.DEBUG):
            deleted, attempted = await bot_gateway._delete_sent_messages(
                client, session
            )

        assert (deleted, attempted) == (0, 1)
        assert PLACEHOLDER_TOKEN not in caplog.text


class TestFirstContactAndAliases:
    async def test_first_ever_start_replies_once_and_leaves_a_clean_session(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sessions: dict[int, SessionState] = {}

        with caplog.at_level(logging.INFO):
            client = await dispatch(
                command_update(1, text="/start"),
                sessions=sessions,
                media_root=tmp_path,
            )

        assert sent_texts(client) == [EXPECTED_REPLY]
        session = sessions[CHAT_ID]
        assert session.phase is Phase.AWAITING_PHOTO
        assert session.chat_id == CHAT_ID
        assert any(
            "session_reset chat_id=555 media_files=0" in record.getMessage()
            for record in caplog.records
        )

    async def test_start_and_restart_replies_are_byte_identical(
        self, tmp_path: Path
    ) -> None:
        """Locked decision: no separate welcome-only path."""
        start_sessions: dict[int, SessionState] = {}
        restart_sessions: dict[int, SessionState] = {}

        start_client = await dispatch(
            command_update(1, text="/start"),
            sessions=start_sessions,
            media_root=tmp_path / "a",
        )
        restart_client = await dispatch(
            command_update(2, text="/restart"),
            sessions=restart_sessions,
            media_root=tmp_path / "b",
        )

        [start_reply] = sent_texts(start_client)
        [restart_reply] = sent_texts(restart_client)
        assert start_reply == restart_reply == EXPECTED_REPLY


class TestResetFromEveryPhase:
    @pytest.mark.parametrize("phase", list(Phase), ids=lambda p: p.value)
    async def test_reset_clears_every_field_purges_media_and_replies(
        self, tmp_path: Path, phase: Phase
    ) -> None:
        chat_id = 555
        session = running_session(chat_id, phase, tmp_path / str(chat_id))
        sessions = {chat_id: session}

        client = await dispatch(
            command_update(1, text="/restart"),
            sessions=sessions,
            media_root=tmp_path,
        )

        assert sent_texts(client) == [EXPECTED_REPLY]
        assert_session_cleared(session)
        assert not (tmp_path / str(chat_id)).exists()


class TestMidInterviewReset:
    async def test_restart_mid_interview_is_never_an_answer(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sessions: dict[int, SessionState] = {}
        interviewer = RecordingInterviewer(questions=["Q1?", "Q2?"])
        client = new_client()
        vision = accept_vision()

        await dispatch(
            photo_update(1),
            sessions=sessions,
            media_root=tmp_path,
            client=client,
            interviewer=interviewer,
            vision=vision,
        )
        session = sessions[CHAT_ID]
        assert session.phase is Phase.INTERVIEW
        assert session.interview.pending_question == "Q1?"
        assert (tmp_path / str(CHAT_ID) / "portrait.jpg").exists()
        calls_before = len(interviewer.calls)

        with caplog.at_level(logging.INFO):
            await dispatch(
                command_update(2, text="/restart"),
                sessions=sessions,
                media_root=tmp_path,
                client=client,
                interviewer=interviewer,
                vision=vision,
            )

        # The command never reached the Interviewer: no model call, no answer.
        assert len(interviewer.calls) == calls_before
        assert all(call.answer != "/restart" for call in interviewer.calls)
        assert not any(
            "interview_answer" in record.getMessage() for record in caplog.records
        )
        assert session.interview.answers == []
        assert session.interview.pending_question is None
        assert session.interview.profile is None
        assert not (tmp_path / str(CHAT_ID)).exists()
        assert sent_texts(client) == [
            bot_gateway.PHOTO_HOLD_MSG,
            ACCEPT_VERDICT.message,
            "Q1?",
            EXPECTED_REPLY,
        ]

    async def test_a_new_photo_in_the_same_process_starts_a_fresh_run(
        self, tmp_path: Path
    ) -> None:
        """The no-restart guarantee, unit level: reset then a second photo."""
        sessions: dict[int, SessionState] = {}
        interviewer = RecordingInterviewer(questions=["Q1?", "Q2?"])
        client = new_client()
        vision = accept_vision()

        await dispatch(
            photo_update(1),
            sessions=sessions,
            media_root=tmp_path,
            client=client,
            interviewer=interviewer,
            vision=vision,
        )
        await dispatch(
            command_update(2, text="/restart"),
            sessions=sessions,
            media_root=tmp_path,
            client=client,
            interviewer=interviewer,
            vision=vision,
        )
        await dispatch(
            photo_update(3),
            sessions=sessions,
            media_root=tmp_path,
            client=client,
            interviewer=interviewer,
            vision=vision,
        )

        session = sessions[CHAT_ID]
        assert vision.check_photo.await_count == 2
        assert session.phase is Phase.INTERVIEW
        # A brand-new interview: nothing carried over from the first run.
        assert session.interview.answers == []
        assert session.interview.pending_question is not None
        assert (tmp_path / str(CHAT_ID) / "portrait.jpg").exists()

    async def test_a_stale_message_queued_before_the_reset_is_nudged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sessions: dict[int, SessionState] = {}
        interviewer = RecordingInterviewer(questions=["Q1?", "Q2?"])
        client = new_client()

        await dispatch(
            photo_update(1),
            sessions=sessions,
            media_root=tmp_path,
            client=client,
            interviewer=interviewer,
        )
        await dispatch(
            command_update(2, text="/restart"),
            sessions=sessions,
            media_root=tmp_path,
            client=client,
            interviewer=interviewer,
        )

        with caplog.at_level(logging.INFO):
            await dispatch(
                text_update(3, CHAT_ID, "my answer before the reset"),
                sessions=sessions,
                media_root=tmp_path,
                client=client,
                interviewer=interviewer,
            )

        assert sent_texts(client)[-1] == bot_gateway.PHOTO_NUDGE
        assert not any(
            "interview_answer" in record.getMessage() for record in caplog.records
        )


class TestIdempotency:
    async def test_a_second_reset_returns_the_identical_reply_with_zero_media(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        chat_id = 555
        session = running_session(chat_id, Phase.INTERVIEW, tmp_path / str(chat_id))
        sessions = {chat_id: session}
        client = new_client()

        with caplog.at_level(logging.INFO):
            await dispatch(
                command_update(1, text="/start"),
                sessions=sessions,
                media_root=tmp_path,
                client=client,
            )
            await dispatch(
                command_update(2, text="/start"),
                sessions=sessions,
                media_root=tmp_path,
                client=client,
            )

        first, second = sent_texts(client)
        assert first == second == EXPECTED_REPLY
        resets = [
            record.getMessage()
            for record in caplog.records
            if "session_reset" in record.getMessage()
        ]
        assert len(resets) == 2
        assert "media_files=1" in resets[0]
        assert "media_files=0" in resets[1]
        assert_session_cleared(session)


class TestNotCommandsKeepExistingBehaviour:
    @pytest.mark.parametrize(
        ("text", "with_entity"),
        [
            pytest.param("/starting", True, id="starting"),
            pytest.param("/restarts", True, id="restarts"),
            pytest.param("/foo", True, id="unknown"),
            pytest.param("start", False, id="no-slash"),
            pytest.param("//start", False, id="double-slash"),
            pytest.param("hello /start", False, id="leading-text"),
        ],
    )
    async def test_non_commands_still_get_the_photo_nudge(
        self, tmp_path: Path, text: str, with_entity: bool
    ) -> None:
        sessions: dict[int, SessionState] = {}

        client = await dispatch(
            command_update(1, text=text, with_entity=with_entity),
            sessions=sessions,
            media_root=tmp_path,
        )

        assert sent_texts(client) == [bot_gateway.PHOTO_NUDGE]

    async def test_plain_text_mid_interview_is_still_recorded_as_an_answer(
        self, tmp_path: Path
    ) -> None:
        """No routing regression for the interview stage."""
        sessions: dict[int, SessionState] = {}
        interviewer = RecordingInterviewer(questions=["Q1?", "Q2?"])
        client = new_client()

        await dispatch(
            photo_update(1),
            sessions=sessions,
            media_root=tmp_path,
            client=client,
            interviewer=interviewer,
        )
        await dispatch(
            text_update(2, CHAT_ID, "I prefer tea at midnight"),
            sessions=sessions,
            media_root=tmp_path,
            client=client,
            interviewer=interviewer,
        )

        session = sessions[CHAT_ID]
        assert interviewer.calls[-1].answer == "I prefer tea at midnight"
        assert len(session.interview.answers) == 1
        assert session.interview.answers[0].answer == "I prefer tea at midnight"
        assert session.interview.pending_question == "Q2?"

    async def test_an_entity_less_command_still_resets(self, tmp_path: Path) -> None:
        """The narrow fallback row: a client without entities still resets."""
        sessions: dict[int, SessionState] = {}

        client = await dispatch(
            command_update(1, text="/start", with_entity=False),
            sessions=sessions,
            media_root=tmp_path,
        )

        assert sent_texts(client) == [EXPECTED_REPLY]

    async def test_a_photo_with_a_command_caption_follows_the_portrait_path(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A command *caption* is never a reset — the photo is a portrait.

        requirements.md:132: ``parse_reset_command`` reads ``text`` only, so
        the caption falls through to the normal flow: Bouncer verdict, first
        interview question, portrait stored — no reset reply or log line.
        """
        sessions: dict[int, SessionState] = {}
        interviewer = RecordingInterviewer(questions=["Q1?"])
        vision = accept_vision()

        with caplog.at_level(logging.INFO):
            client = await dispatch(
                photo_update(1, caption="/start"),
                sessions=sessions,
                media_root=tmp_path,
                interviewer=interviewer,
                vision=vision,
            )

        # The whole portrait path ran — the caption never intercepted it.
        assert vision.check_photo.await_count == 1
        assert sent_texts(client) == [
            bot_gateway.PHOTO_HOLD_MSG,
            ACCEPT_VERDICT.message,
            "Q1?",
        ]
        session = sessions[CHAT_ID]
        assert session.phase is Phase.INTERVIEW
        assert session.interview.pending_question == "Q1?"
        assert (tmp_path / str(CHAT_ID) / "portrait.jpg").exists()
        # And nothing about the message was recognised as a reset.
        messages = [record.getMessage() for record in caplog.records]
        assert EXPECTED_REPLY not in sent_texts(client)
        assert not any("reset_command" in m for m in messages)
        assert not any("session_reset" in m for m in messages)


class TestInterceptionOrder:
    async def test_the_command_never_reaches_the_interviewer_or_build_reply(
        self, tmp_path: Path
    ) -> None:
        chat_id = 555
        session = running_session(chat_id, Phase.INTERVIEW, tmp_path / str(chat_id))
        sessions = {chat_id: session}
        client = new_client()

        with (
            patch.object(bot_gateway, "_handle_interview") as handle_interview,
            patch.object(bot_gateway, "build_reply") as build,
        ):
            await dispatch(
                command_update(1, text="/start"),
                sessions=sessions,
                media_root=tmp_path,
                client=client,
            )

        handle_interview.assert_not_called()
        build.assert_not_called()
        assert sent_texts(client) == [EXPECTED_REPLY]


class TestPurgeFailureDegrades:
    async def test_an_oserror_from_the_purge_still_clears_and_replies(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        chat_id = 555
        session = running_session(chat_id, Phase.NARRATE, tmp_path / str(chat_id))
        sessions = {chat_id: session}
        client = new_client()

        def explode(target: Any) -> None:
            raise OSError("disk on fire")

        monkeypatch.setattr(bot_gateway.shutil, "rmtree", explode)

        with caplog.at_level(logging.ERROR):
            await dispatch(
                command_update(1, text="/start"),
                sessions=sessions,
                media_root=tmp_path,
                client=client,
            )

        assert sent_texts(client) == [EXPECTED_REPLY]
        assert_session_cleared(session)
        messages = [record.getMessage() for record in caplog.records]
        assert any("temp media purge failed chat_id=555" in m for m in messages)

    async def test_a_containment_guard_trip_still_clears_and_replies(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        chat_id = 555
        media_root = tmp_path / "media"
        media_root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "loot.txt").write_text("not yours")
        # A poisoned chat directory: a symlink pointing outside the media root.
        (media_root / str(chat_id)).symlink_to(outside, target_is_directory=True)
        session = SessionState(chat_id=chat_id, phase=Phase.DONE)
        sessions = {chat_id: session}
        client = new_client()

        with caplog.at_level(logging.ERROR):
            await dispatch(
                command_update(1, text="/restart"),
                sessions=sessions,
                media_root=media_root,
                client=client,
            )

        assert sent_texts(client) == [EXPECTED_REPLY]
        assert_session_cleared(session)
        assert (outside / "loot.txt").exists()
        assert any(
            "temp media purge failed" in record.getMessage()
            for record in caplog.records
        )


class TestCrossChatScoping:
    async def test_another_chats_media_and_session_are_untouched(
        self, tmp_path: Path
    ) -> None:
        target = running_session(555, Phase.CONVERT, tmp_path / "555")
        other = running_session(777, Phase.INTERVIEW, tmp_path / "777")
        sessions = {555: target, 777: other}

        client = await dispatch(
            command_update(1, chat_id=555, text="/restart"),
            sessions=sessions,
            media_root=tmp_path,
        )

        assert sent_texts(client) == [EXPECTED_REPLY]
        assert_session_cleared(target)
        # The other chat keeps its phase, interview buffer and media.
        assert other.phase is Phase.INTERVIEW
        assert len(other.interview.answers) == 1
        assert other.interview.pending_question == "Q2?"
        assert other.script_text == "The subject stirs at dawn."
        assert (tmp_path / "777" / "portrait.jpg").read_bytes() == b"STORED-BYTES"


class TestLogContract:
    async def test_the_three_log_lines_appear_in_order(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        chat_id = 555
        session = running_session(chat_id, Phase.INTERVIEW, tmp_path / str(chat_id))
        sessions = {chat_id: session}
        client = new_client()

        with caplog.at_level(logging.INFO):
            await dispatch(
                command_update(1, text="/start"),
                sessions=sessions,
                media_root=tmp_path,
                client=client,
            )

        messages = [record.getMessage() for record in caplog.records]
        assert any(
            m == "reset_command chat_id=555 command=start from_phase=INTERVIEW"
            for m in messages
        )
        assert any(m == "session_reset chat_id=555 media_files=1" for m in messages)
        assert any(
            m == "phase_transition chat_id=555 INTERVIEW -> AWAITING_PHOTO"
            for m in messages
        )
        # The phase transition only appears when the phase actually changed.
        start = messages.index(
            "reset_command chat_id=555 command=start from_phase=INTERVIEW"
        )
        reset = messages.index("session_reset chat_id=555 media_files=1")
        assert start < reset

    async def test_no_transition_is_logged_when_already_awaiting_photo(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sessions: dict[int, SessionState] = {}

        with caplog.at_level(logging.INFO):
            await dispatch(
                command_update(1, text="/start"),
                sessions=sessions,
                media_root=tmp_path,
            )

        assert not any(
            "phase_transition" in record.getMessage() for record in caplog.records
        )

    async def test_no_message_text_or_secrets_are_ever_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        secret_tail = "private words that must never be logged"
        chat_id = 555
        session = running_session(chat_id, Phase.INTERVIEW, tmp_path / str(chat_id))
        sessions = {chat_id: session}
        client = new_client()

        with caplog.at_level(logging.DEBUG):
            await dispatch(
                command_update(1, text=f"/restart {secret_tail}"),
                sessions=sessions,
                media_root=tmp_path,
                client=client,
            )

        assert secret_tail not in caplog.text
        assert "/restart" not in caplog.text
        assert PLACEHOLDER_TOKEN not in caplog.text
        assert PLACEHOLDER_GEMINI_KEY not in caplog.text
        assert sent_texts(client) == [EXPECTED_REPLY]


def settings_mock() -> Any:
    settings = MagicMock()
    settings.telegram_bot_token = PLACEHOLDER_TOKEN
    settings.gemini_api_key = PLACEHOLDER_GEMINI_KEY
    return settings


async def run_updates(
    updates: list[list[Update]],
    *,
    send: AsyncMock,
    media_root: str,
    interviewer: RecordingInterviewer,
    vision: AsyncMock | None = None,
    send_photo: AsyncMock | None = None,
    send_voice: AsyncMock | None = None,
) -> None:
    """Run ``run_bot`` with every external dependency mocked (scripter harness)."""
    mock_client = AsyncMock()
    mock_client.get_updates = AsyncMock(side_effect=updates)
    mock_client.send_message = send
    mock_client.send_photo = send_photo or AsyncMock(return_value={"ok": True})
    mock_client.send_voice = send_voice or AsyncMock(return_value={"ok": True})
    mock_client.download_file = AsyncMock(return_value=PORTRAIT_BYTES)
    mock_client.close = AsyncMock()

    with (
        patch.object(bot_gateway, "load_settings", return_value=settings_mock()),
        patch.object(bot_gateway, "TelegramClient", return_value=mock_client),
        patch.object(
            bot_gateway, "GeminiVisionClient", return_value=vision or accept_vision()
        ),
        patch.object(bot_gateway, "InterviewerClient", return_value=interviewer),
        patch.object(bot_gateway, "ConverterClient", return_value=FakeConverter()),
        patch.object(bot_gateway, "ScripterClient", return_value=FakeScripter()),
        patch.object(bot_gateway, "NarratorClient", return_value=FakeNarrator()),
        patch.object(bot_gateway, "_now", return_value=DEFAULT_NOW),
    ):
        await bot_gateway.run_bot(
            poll_timeout=0,
            max_iterations=len(updates),
            error_backoff=0.0,
            media_root=media_root,
        )


class TestFullLoop:
    async def test_run_reset_and_a_second_full_run_need_no_restart(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The TA scenario, unit level: run -> /restart -> run, one process."""
        interviewer = RecordingInterviewer(
            questions=[f"Q{n}?" for n in range(1, 11)],
            steps=[
                InterviewStep(action=InterviewAction.FINISH, **PROFILE.model_dump()),
                InterviewStep(action=InterviewAction.FINISH, **PROFILE.model_dump()),
            ],
        )
        send = AsyncMock(return_value={"ok": True})
        send_photo = AsyncMock(return_value={"ok": True})
        send_voice = AsyncMock(return_value={"ok": True})
        vision = accept_vision()
        updates = [
            [photo_update(1)],
            [text_update(n, CHAT_ID, f"A{n}") for n in range(2, 7)],
            [command_update(7, text="/restart")],
            [photo_update(8)],
            [text_update(n, CHAT_ID, f"A{n}") for n in range(9, 14)],
        ]

        with caplog.at_level(logging.INFO):
            await run_updates(
                updates,
                send=send,
                media_root=str(tmp_path),
                interviewer=interviewer,
                vision=vision,
                send_photo=send_photo,
                send_voice=send_voice,
            )

        # ``send`` is the ``send_message`` mock itself (the harness wires it
        # straight onto the client), so read its calls directly.
        texts = [call.kwargs["text"] for call in send.await_args_list]
        # Two complete happy paths, one reset in between - same process.
        assert vision.check_photo.await_count == 2
        assert send_photo.await_count == 2
        assert send_voice.await_count == 2
        assert texts.count(EXPECTED_REPLY) == 1
        assert (tmp_path / "555" / "portrait.jpg").exists()
        assert any(
            "session_reset chat_id=555 media_files=1" in record.getMessage()
            for record in caplog.records
        )
        starts = [
            record.getMessage()
            for record in caplog.records
            if "Starting Telegram gateway" in record.getMessage()
        ]
        assert starts == ["Starting Telegram gateway (Phase 7 - Resilience)"]

    async def test_the_banner_names_phase_7(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        send = AsyncMock(return_value={"ok": True})

        with caplog.at_level(logging.INFO):
            await run_updates(
                [[]],
                send=send,
                media_root=str(tmp_path),
                interviewer=RecordingInterviewer(),
            )

        assert any(
            record.getMessage() == "Starting Telegram gateway (Phase 7 - Resilience)"
            for record in caplog.records
        )
