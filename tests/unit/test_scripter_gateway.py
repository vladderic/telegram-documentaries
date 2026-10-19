"""Gateway tests for the Scripter (Phase 5).

Telegram, the Bouncer, the Interviewer, the Converter and the Scripter are all
mocked: no real API call is ever made. The happy-path ordering assertion shares
one call log, because *when* the narration goes out - after the portrait - is
the contract, not an implementation detail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram_documentaries import bot_gateway
from telegram_documentaries.gemini import GeminiApiError
from telegram_documentaries.models import (
    BehaviouralProfile,
    GeneratedImage,
    InterviewAction,
    InterviewStep,
    Phase,
    Script,
    SessionState,
    Update,
)
from telegram_documentaries.narrator import VoiceNote

PLACEHOLDER_TOKEN = "123456-PLACEHOLDER-TELEGRAM-BOT-TOKEN"
PLACEHOLDER_GEMINI_KEY = "PLACEHOLDER-GEMINI-API-KEY"

NARRATION = "Our subject rises at dusk and patrols the kitchen with intent."
SCRIPT = Script(text=NARRATION)
PROFILE = BehaviouralProfile(
    profile="The subject hoards stationery.",
    suggested_animal="Eurasian magpie",
    animal_description="A shameless thief of anything shiny.",
)
IMAGE = GeneratedImage(mime_type="image/jpeg", data=b"HYBRID-PORTRAIT-BYTES")
VOICE = VoiceNote(data=b"MP3-BYTES", mime_type="audio/mpeg", duration=7)
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


def text_update(update_id: int, chat_id: int, text: str) -> Update:
    return Update.model_validate(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 1_760_000_200,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Ada"},
                "text": text,
            },
        }
    )


#: The shortest route to the Scripter: one photo, five answers, FINISH.
FINISH_UPDATES = [
    [photo_update(1, 555)],
    [text_update(number, 555, f"A{number - 1}") for number in range(2, 7)],
]


class FakeConverter:
    """Scripted stand-in: a value to return, or an exception to raise."""

    def __init__(self, results: list[Any] | None = None) -> None:
        self.queue: list[Any] = list(results if results is not None else [IMAGE])
        self.calls: list[BehaviouralProfile] = []
        self.closed = False

    async def fuse(
        self, portrait: bytes, mime_type: str, profile: BehaviouralProfile
    ) -> GeneratedImage:
        self.calls.append(profile)
        if not self.queue:
            raise AssertionError("unexpected fuse call (script exhausted)")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


class FakeInterviewer:
    """Five questions, then the dossier (the model finishes by itself)."""

    def __init__(self) -> None:
        self.question_queue: list[Any] = [f"Q{n}?" for n in range(1, 6)]
        self.step_queue: list[Any] = [
            InterviewStep(action=InterviewAction.FINISH, **PROFILE.model_dump())
        ]
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
        raise AssertionError("unexpected distil call (ceiling not reached)")

    async def close(self) -> None:
        self.closed = True


@dataclass
class ScriptCall:
    """One recorded call into the scripter fake."""

    profile: BehaviouralProfile


class FakeScripter:
    """Scripted, recording stand-in for :class:`ScripterClient`."""

    def __init__(
        self, results: list[Any] | None = None, log: list[str] | None = None
    ) -> None:
        self.queue: list[Any] = list(results if results is not None else [SCRIPT])
        self.calls: list[ScriptCall] = []
        self.closed = False
        self._log = log

    async def write_script(self, profile: BehaviouralProfile) -> Script:
        self.calls.append(ScriptCall(profile))
        if self._log is not None:
            self._log.append("script")
        if not self.queue:
            raise AssertionError("unexpected write_script call (script exhausted)")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


@dataclass
class SpeakCall:
    """One recorded call into the narrator fake."""

    text: str


class FakeNarrator:
    """Scripted, recording stand-in for :class:`NarratorClient`."""

    def __init__(
        self, results: list[Any] | None = None, log: list[str] | None = None
    ) -> None:
        self.queue: list[Any] = list(results if results is not None else [VOICE])
        self.calls: list[SpeakCall] = []
        self.closed = False
        self._log = log

    async def speak(self, text: str) -> VoiceNote:
        self.calls.append(SpeakCall(text))
        if self._log is not None:
            self._log.append("voice")
        if not self.queue:
            raise AssertionError("unexpected speak call (script exhausted)")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return cast(VoiceNote, item)

    async def close(self) -> None:
        self.closed = True


def settings_mock() -> Any:
    settings = MagicMock()
    settings.telegram_bot_token = PLACEHOLDER_TOKEN
    settings.gemini_api_key = PLACEHOLDER_GEMINI_KEY
    return settings


def recorder(log: list[str], tag: str) -> Any:
    """An AsyncMock side effect that records that the call happened."""

    async def record(**kwargs: Any) -> dict[str, bool]:
        log.append(tag)
        return {"ok": True}

    return record


def raiser(error: Exception) -> Any:
    """An AsyncMock side effect that fails the way the real client would."""

    async def raise_it(**kwargs: Any) -> Any:
        raise error

    return raise_it


async def run_updates(
    updates: list[list[Update]],
    *,
    interviewer: FakeInterviewer,
    scripter: FakeScripter,
    send: AsyncMock,
    send_photo: AsyncMock,
    converter: FakeConverter,
    media_root: str,
    narrator: FakeNarrator | None = None,
    send_voice: AsyncMock | None = None,
) -> None:
    """Run ``run_bot`` with every external dependency mocked."""
    mock_client = AsyncMock()
    mock_client.get_updates = AsyncMock(side_effect=updates)
    mock_client.send_message = send
    mock_client.send_photo = send_photo
    mock_client.send_voice = send_voice or AsyncMock(return_value={"ok": True})
    mock_client.download_file = AsyncMock(return_value=PORTRAIT_BYTES)
    mock_client.close = AsyncMock()
    vision = AsyncMock()
    vision.close = AsyncMock()

    with (
        patch.object(bot_gateway, "load_settings", return_value=settings_mock()),
        patch.object(bot_gateway, "TelegramClient", return_value=mock_client),
        patch.object(bot_gateway, "GeminiVisionClient", return_value=vision),
        patch.object(bot_gateway, "InterviewerClient", return_value=interviewer),
        patch.object(bot_gateway, "ConverterClient", return_value=converter),
        patch.object(bot_gateway, "ScripterClient", return_value=scripter),
        patch.object(
            bot_gateway, "NarratorClient", return_value=narrator or FakeNarrator()
        ),
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


class TestTheNarrationFollowsThePortrait:
    @pytest.mark.asyncio
    async def test_the_narration_is_the_last_word_after_the_portrait(
        self, tmp_path: Any
    ) -> None:
        log: list[str] = []
        send = AsyncMock(side_effect=recorder(log, "text"))
        scripter = FakeScripter(log=log)

        await run_updates(
            FINISH_UPDATES,
            interviewer=FakeInterviewer(),
            scripter=scripter,
            send=send,
            send_photo=AsyncMock(side_effect=recorder(log, "photo")),
            converter=FakeConverter(),
            media_root=str(tmp_path),
        )

        texts = [call.kwargs["text"] for call in send.await_args_list]
        # The interview's own questions came first; the tail is the contract:
        # reveal, hold line, the image, then the words.
        assert log[-5:] == ["text", "text", "photo", "script", "text"]
        assert texts[-1] == NARRATION
        assert len(scripter.calls) == 1
        assert scripter.calls[0].profile == PROFILE

    @pytest.mark.asyncio
    async def test_the_narration_is_logged_with_its_length(
        self, tmp_path: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        script = Script(text=" ".join(["word"] * 80))
        log: list[str] = []

        with caplog.at_level("INFO"):
            await run_updates(
                FINISH_UPDATES,
                interviewer=FakeInterviewer(),
                scripter=FakeScripter([script]),
                send=AsyncMock(side_effect=recorder(log, "text")),
                send_photo=AsyncMock(side_effect=recorder(log, "photo")),
                converter=FakeConverter(),
                media_root=str(tmp_path),
            )

        delivered = [
            record.getMessage()
            for record in caplog.records
            if "script_delivered" in record.getMessage()
        ]
        assert len(delivered) == 1
        assert "words=80" in delivered[0]
        assert f"chars={len(script.text)}" in delivered[0]

    @pytest.mark.asyncio
    async def test_the_log_records_the_transition_into_script(
        self, caplog: pytest.LogCaptureFixture, tmp_path: Any
    ) -> None:
        with caplog.at_level("INFO"):
            await run_updates(
                FINISH_UPDATES,
                interviewer=FakeInterviewer(),
                scripter=FakeScripter(),
                send=AsyncMock(side_effect=recorder([], "text")),
                send_photo=AsyncMock(side_effect=recorder([], "photo")),
                converter=FakeConverter(),
                media_root=str(tmp_path),
            )

        transitions = [
            record.getMessage()
            for record in caplog.records
            if "phase_transition" in record.getMessage()
        ]
        assert any("CONVERT" in line and "SCRIPT" in line for line in transitions)


class TestWriteScript:
    """The stage in isolation: state, delivery and its failure modes."""

    @pytest.mark.asyncio
    async def test_marks_the_stage_sends_plain_text_and_keeps_it(self) -> None:
        session = SessionState(chat_id=555, phase=Phase.CONVERT)
        client = AsyncMock()
        client.send_message = AsyncMock(return_value={"ok": True})
        scripter = FakeScripter()

        await bot_gateway._write_script(client, cast(Any, scripter), session, PROFILE)

        assert session.phase is Phase.SCRIPT
        assert session.script_text == NARRATION
        assert client.send_message.await_args.kwargs == {
            "chat_id": 555,
            "text": NARRATION,
        }

    async def test_logs_the_convert_to_script_transition(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = SessionState(chat_id=555, phase=Phase.CONVERT)
        client = AsyncMock()
        client.send_message = AsyncMock(return_value={"ok": True})

        with caplog.at_level("INFO"):
            await bot_gateway._write_script(
                client, cast(Any, FakeScripter()), session, PROFILE
            )

        messages = [record.getMessage() for record in caplog.records]
        assert any("CONVERT" in m and "SCRIPT" in m for m in messages)
        assert any("script_delivered" in m for m in messages)

    async def test_a_failed_script_degrades_and_never_runs_twice(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = SessionState(chat_id=555, phase=Phase.CONVERT)
        client = AsyncMock()
        client.send_message = AsyncMock(return_value={"ok": True})
        scripter = FakeScripter([GeminiApiError("text model down")])

        with caplog.at_level("ERROR"):
            await bot_gateway._write_script(
                client, cast(Any, scripter), session, PROFILE
            )

        assert (
            client.send_message.await_args.kwargs["text"]
            == bot_gateway.SCRIPT_FAILURE_MSG
        )
        # The stage is claimed before the call, so the failure cannot re-open
        # the pipeline (and Phase 6 still sees a session past CONVERT).
        assert session.phase is Phase.SCRIPT
        assert session.script_text is None
        assert any(record.levelno >= 40 for record in caplog.records)

    async def test_a_failed_send_stores_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Only a narration that reached the chat is Phase 6's input."""
        session = SessionState(chat_id=555, phase=Phase.CONVERT)
        client = AsyncMock()
        client.send_message = AsyncMock(
            side_effect=raiser(bot_gateway.TelegramApiError("down"))
        )

        await bot_gateway._write_script(
            client, cast(Any, FakeScripter()), session, PROFILE
        )

        assert session.script_text is None
        assert session.phase is Phase.SCRIPT


class TestOneNarrationPerSession:
    @pytest.mark.asyncio
    async def test_a_late_text_gets_the_holding_reply_and_writes_nothing(
        self, tmp_path: Any
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        scripter = FakeScripter()
        updates = FINISH_UPDATES + [[text_update(7, 555, "and another thing")]]

        await run_updates(
            updates,
            interviewer=FakeInterviewer(),
            scripter=scripter,
            send=send,
            send_photo=AsyncMock(return_value={"ok": True}),
            converter=FakeConverter(),
            media_root=str(tmp_path),
        )

        texts = [call.kwargs["text"] for call in send.await_args_list]
        assert texts[-1] == bot_gateway.LATER_PHASE_REPLY
        assert len(scripter.calls) == 1

    @pytest.mark.asyncio
    async def test_a_late_photo_starts_no_new_run(self, tmp_path: Any) -> None:
        send = AsyncMock(return_value={"ok": True})
        send_photo = AsyncMock(return_value={"ok": True})
        scripter = FakeScripter()
        updates = FINISH_UPDATES + [[photo_update(7, 555)]]

        await run_updates(
            updates,
            interviewer=FakeInterviewer(),
            scripter=scripter,
            send=send,
            send_photo=send_photo,
            converter=FakeConverter(),
            media_root=str(tmp_path),
        )

        texts = [call.kwargs["text"] for call in send.await_args_list]
        assert texts[-1] == bot_gateway.LATER_PHASE_REPLY
        assert len(scripter.calls) == 1
        assert send_photo.await_count == 1


class TestTheNarrationSurvivesTheDarkroom:
    """The two final stages are independent (decision, Phase 5 spec)."""

    @pytest.mark.asyncio
    async def test_a_failed_conversion_does_not_silence_the_narrator(
        self, tmp_path: Any
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        send_photo = AsyncMock(return_value={"ok": True})
        scripter = FakeScripter()

        await run_updates(
            FINISH_UPDATES,
            interviewer=FakeInterviewer(),
            scripter=scripter,
            send=send,
            send_photo=send_photo,
            converter=FakeConverter([GeminiApiError("image model down")]),
            media_root=str(tmp_path),
        )

        texts = [call.kwargs["text"] for call in send.await_args_list]
        assert bot_gateway.PORTRAIT_FAILURE_MSG in texts
        assert texts[-1] == NARRATION
        assert send_photo.await_count == 0

    @pytest.mark.asyncio
    async def test_a_failed_upload_does_not_silence_the_narrator(
        self, tmp_path: Any
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        scripter = FakeScripter()

        await run_updates(
            FINISH_UPDATES,
            interviewer=FakeInterviewer(),
            scripter=scripter,
            send=send,
            send_photo=AsyncMock(side_effect=raiser(bot_gateway.TelegramApiError("x"))),
            converter=FakeConverter(),
            media_root=str(tmp_path),
        )

        texts = [call.kwargs["text"] for call in send.await_args_list]
        assert bot_gateway.PORTRAIT_FAILURE_MSG in texts
        assert texts[-1] == NARRATION

    @pytest.mark.asyncio
    async def test_a_failed_script_does_not_hide_a_delivered_portrait(
        self, tmp_path: Any
    ) -> None:
        send = AsyncMock(return_value={"ok": True})
        send_photo = AsyncMock(return_value={"ok": True})
        scripter = FakeScripter([GeminiApiError("text model down")])

        await run_updates(
            FINISH_UPDATES,
            interviewer=FakeInterviewer(),
            scripter=scripter,
            send=send,
            send_photo=send_photo,
            converter=FakeConverter(),
            media_root=str(tmp_path),
        )

        texts = [call.kwargs["text"] for call in send.await_args_list]
        assert texts[-1] == bot_gateway.SCRIPT_FAILURE_MSG
        assert send_photo.await_count == 1
        assert bot_gateway.PORTRAIT_FAILURE_MSG not in texts
        assert len(scripter.calls) == 1


class TestScripterLifecycle:
    @pytest.mark.asyncio
    async def test_the_scripter_is_closed_on_shutdown(self, tmp_path: Any) -> None:
        scripter = FakeScripter()

        await run_updates(
            [[]],
            interviewer=FakeInterviewer(),
            scripter=scripter,
            send=AsyncMock(return_value={"ok": True}),
            send_photo=AsyncMock(return_value={"ok": True}),
            converter=FakeConverter(),
            media_root=str(tmp_path),
        )

        assert scripter.closed is True

    @pytest.mark.asyncio
    async def test_the_narrator_is_closed_on_shutdown(self, tmp_path: Any) -> None:
        narrator = FakeNarrator()

        await run_updates(
            [[]],
            interviewer=FakeInterviewer(),
            scripter=FakeScripter(),
            send=AsyncMock(return_value={"ok": True}),
            send_photo=AsyncMock(return_value={"ok": True}),
            converter=FakeConverter(),
            media_root=str(tmp_path),
            narrator=narrator,
        )

        assert narrator.closed is True


class TestTheVoiceNoteClosesTheDocumentary:
    @pytest.mark.asyncio
    async def test_the_voice_note_is_the_very_last_thing(self, tmp_path: Any) -> None:
        log: list[str] = []
        send = AsyncMock(side_effect=recorder(log, "text"))
        send_voice = AsyncMock(side_effect=recorder(log, "voice"))

        await run_updates(
            FINISH_UPDATES,
            interviewer=FakeInterviewer(),
            scripter=FakeScripter(log=log),
            send=send,
            send_photo=AsyncMock(side_effect=recorder(log, "photo")),
            converter=FakeConverter(),
            media_root=str(tmp_path),
            narrator=FakeNarrator(),
            send_voice=send_voice,
        )

        # The tail is the contract: reveal, hold line, the image, the words,
        # then the voice that speaks those very words.
        assert log[-5:] == ["text", "photo", "script", "text", "voice"]
        voice = send_voice.await_args_list[0].kwargs["voice"]
        assert voice.mime_type == "audio/mpeg"
        assert voice.duration >= 1

    @pytest.mark.asyncio
    async def test_a_failed_voice_does_not_stop_the_words(self, tmp_path: Any) -> None:
        log: list[str] = []
        send = AsyncMock(side_effect=recorder(log, "text"))

        await run_updates(
            FINISH_UPDATES,
            interviewer=FakeInterviewer(),
            scripter=FakeScripter(log=log),
            send=send,
            send_photo=AsyncMock(side_effect=recorder(log, "photo")),
            converter=FakeConverter(),
            media_root=str(tmp_path),
            narrator=FakeNarrator([GeminiApiError("tts down")], log=log),
            send_voice=AsyncMock(side_effect=recorder(log, "voice")),
        )

        texts = [call.kwargs["text"] for call in send.await_args_list]
        assert NARRATION in texts  # the narration still reached the chat
        assert bot_gateway.NARRATOR_FAILURE_MSG in texts


class TestNarrate:
    """The Phase 6 stage in isolation: the voice from the delivered words."""

    @pytest.mark.asyncio
    async def test_speaks_the_delivered_text_and_sends_one_voice_note(self) -> None:
        session = SessionState(chat_id=555, phase=Phase.SCRIPT)
        session.script_text = NARRATION
        client = AsyncMock()
        client.send_message = AsyncMock(return_value={"ok": True})
        client.send_voice = AsyncMock(return_value={"ok": True})
        narrator = FakeNarrator()

        await bot_gateway._narrate(client, cast(Any, narrator), session)

        assert session.phase is Phase.DONE
        assert session.voice_note_sent is True
        assert [call.text for call in narrator.calls] == [NARRATION]
        assert client.send_voice.await_args.kwargs == {"chat_id": 555, "voice": VOICE}

    async def test_logs_the_script_to_narrate_transition(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = SessionState(chat_id=555, phase=Phase.SCRIPT)
        session.script_text = NARRATION
        client = AsyncMock()
        client.send_message = AsyncMock(return_value={"ok": True})
        client.send_voice = AsyncMock(return_value={"ok": True})

        with caplog.at_level("INFO"):
            await bot_gateway._narrate(client, cast(Any, FakeNarrator()), session)

        messages = [record.getMessage() for record in caplog.records]
        assert any("SCRIPT" in m and "NARRATE" in m for m in messages)
        assert any("NARRATE" in m and "DONE" in m for m in messages)
        assert any("voice_note_delivered" in m for m in messages)

    async def test_a_failed_tts_degrades_keeps_the_words_and_stops_at_narrate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = SessionState(chat_id=555, phase=Phase.SCRIPT)
        session.script_text = NARRATION
        client = AsyncMock()
        client.send_message = AsyncMock(return_value={"ok": True})
        narrator = FakeNarrator([GeminiApiError("tts model down")])

        with caplog.at_level("ERROR"):
            await bot_gateway._narrate(client, cast(Any, narrator), session)

        assert (
            client.send_message.await_args.kwargs["text"]
            == bot_gateway.NARRATOR_FAILURE_MSG
        )
        # The stage is claimed before the call, so a failure can never re-open
        # the pipeline; the words survive in the chat.
        assert session.phase is Phase.NARRATE
        assert session.voice_note_sent is False
        assert session.script_text == NARRATION

    async def test_a_failed_upload_never_sends_a_second_note(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = SessionState(chat_id=555, phase=Phase.SCRIPT)
        session.script_text = NARRATION
        client = AsyncMock()
        client.send_message = AsyncMock(return_value={"ok": True})
        client.send_voice = AsyncMock(
            side_effect=raiser(bot_gateway.TelegramApiError("down"))
        )

        await bot_gateway._narrate(client, cast(Any, FakeNarrator()), session)

        assert session.voice_note_sent is False
        assert session.phase is Phase.NARRATE
        assert (
            client.send_message.await_args.kwargs["text"]
            == bot_gateway.NARRATOR_FAILURE_MSG
        )

    async def test_a_session_with_no_script_is_closed_without_speaking(self) -> None:
        session = SessionState(chat_id=555, phase=Phase.SCRIPT)
        client = AsyncMock()
        client.send_message = AsyncMock(return_value={"ok": True})
        client.send_voice = AsyncMock(return_value={"ok": True})
        narrator = FakeNarrator()

        await bot_gateway._narrate(client, cast(Any, narrator), session)

        assert session.phase is Phase.DONE
        assert session.voice_note_sent is False
        assert narrator.calls == []
        client.send_voice.assert_not_awaited()
