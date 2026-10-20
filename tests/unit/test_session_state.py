from telegram_documentaries.models import (
    BehaviouralProfile,
    InterviewAnswer,
    InterviewState,
    Phase,
    SessionState,
)


def test_initial_state_is_awaiting_photo() -> None:
    state = SessionState(chat_id=123)
    assert state.chat_id == 123
    assert state.phase == Phase.AWAITING_PHOTO
    assert state.photo_file_id is None
    assert state.photo_local_path is None


def test_clear_resets_to_awaiting_photo_and_purges_media() -> None:
    state = SessionState(
        chat_id=123,
        phase=Phase.BOUNCER_CHECK,
        photo_file_id="fid",
        photo_local_path="/tmp/path.jpg",
        temp_media_path="/tmp",
    )
    state.clear()
    assert state.phase == Phase.AWAITING_PHOTO
    assert state.photo_file_id is None
    assert state.photo_local_path is None
    assert state.temp_media_path is None


def test_transition_sequence_accept() -> None:
    state = SessionState(chat_id=123)
    state.phase = Phase.BOUNCER_CHECK
    state.phase = Phase.AWAITING_INTERVIEW
    assert state.phase == Phase.AWAITING_INTERVIEW


def test_transition_sequence_reject_returns_to_awaiting_photo() -> None:
    state = SessionState(chat_id=123, phase=Phase.BOUNCER_CHECK, photo_file_id="fid")
    state.clear()
    assert state.phase == Phase.AWAITING_PHOTO


class TestInterviewState:
    def test_defaults_to_an_empty_interview(self) -> None:
        state = SessionState(chat_id=123)
        assert state.interview.answers == []
        assert state.interview.pending_question is None
        assert state.interview.profile is None
        assert state.interview.question_sent_at is None

    def test_clear_resets_the_question_send_time(self) -> None:
        state = SessionState(chat_id=123, phase=Phase.INTERVIEW)
        state.interview.pending_question = "Q1?"
        state.interview.question_sent_at = 1_760_000_000.0

        state.clear()

        assert state.interview.question_sent_at is None

    def test_derives_the_next_question_number_from_the_answers(self) -> None:
        """Single source of truth: no stored counter that could drift."""
        interview = InterviewState()
        assert interview.next_question_number == 1

        interview.answers.append(InterviewAnswer(question="Q1?", answer="A1"))
        interview.answers.append(InterviewAnswer(question="Q2?", answer="A2"))
        interview.answers.append(InterviewAnswer(question="Q3?", answer="A3"))
        assert interview.next_question_number == 4

    def test_derives_finished_from_the_stored_profile(self) -> None:
        interview = InterviewState()
        assert interview.is_finished is False

        interview.profile = BehaviouralProfile(
            profile="The subject hoards tea cups.",
            suggested_animal="Red fox",
            animal_description="A lone hunter of the small hours.",
        )
        assert interview.is_finished is True

    def test_clear_resets_the_interview(self) -> None:
        state = SessionState(chat_id=123, phase=Phase.INTERVIEW)
        state.interview.answers.append(InterviewAnswer(question="Q1?", answer="A1"))
        state.interview.pending_question = "Q2?"
        state.interview.profile = BehaviouralProfile(
            profile="Finished early.",
            suggested_animal="Red fox",
            animal_description="A lone hunter of the small hours.",
        )

        state.clear()

        assert state.interview.answers == []
        assert state.interview.pending_question is None
        assert state.interview.profile is None

    def test_sessions_do_not_share_interview_state(self) -> None:
        """A shared mutable default would leak one user's dossier to another."""
        first = SessionState(chat_id=1)
        second = SessionState(chat_id=2)

        first.interview.answers.append(InterviewAnswer(question="Q?", answer="A"))
        first.interview.pending_question = "Q?"

        assert second.interview.answers == []
        assert second.interview.pending_question is None


class TestStoredPortraitMediaType:
    """Phase 4b: what the stored portrait *is* travels with the session."""

    def test_defaults_to_jpeg_for_a_telegram_photo(self) -> None:
        assert SessionState(chat_id=555).photo_mime_type == "image/jpeg"

    def test_records_the_media_type_of_a_file_portrait(self) -> None:
        state = SessionState(chat_id=555, photo_mime_type="image/png")

        assert state.photo_mime_type == "image/png"

    def test_clear_resets_it_along_with_the_media_path(self) -> None:
        state = SessionState(
            chat_id=555, photo_local_path="/tmp/p.png", photo_mime_type="image/png"
        )

        state.clear()

        assert state.photo_mime_type == "image/jpeg"
        assert state.photo_local_path is None


class TestStoredScript:
    """Phase 5: the delivered narration is kept for Phase 6's voice synthesis."""

    def test_defaults_to_no_script(self) -> None:
        assert SessionState(chat_id=555).script_text is None

    def test_records_the_delivered_narration(self) -> None:
        state = SessionState(
            chat_id=555, phase=Phase.SCRIPT, script_text="Our subject stirs."
        )

        assert state.script_text == "Our subject stirs."

    def test_clear_resets_it(self) -> None:
        state = SessionState(chat_id=555, phase=Phase.SCRIPT, script_text="Gone.")

        state.clear()

        assert state.script_text is None

    def test_sessions_do_not_share_a_script(self) -> None:
        first = SessionState(chat_id=1)
        second = SessionState(chat_id=2)

        first.script_text = "Only mine."

        assert second.script_text is None


class TestVoiceNoteSent:
    """Phase 6: one narration means exactly one voice note per session."""

    def test_defaults_to_no_voice_note(self) -> None:
        assert SessionState(chat_id=555).voice_note_sent is False

    def test_records_a_delivered_voice_note(self) -> None:
        state = SessionState(chat_id=555, voice_note_sent=True)

        assert state.voice_note_sent is True

    def test_clear_resets_it(self) -> None:
        state = SessionState(chat_id=555, voice_note_sent=True)

        state.clear()

        assert state.voice_note_sent is False

    def test_sessions_do_not_share_the_flag(self) -> None:
        first = SessionState(chat_id=1)
        second = SessionState(chat_id=2)

        first.voice_note_sent = True

        assert second.voice_note_sent is False
