from telegram_documentaries.models import Phase, SessionState


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
