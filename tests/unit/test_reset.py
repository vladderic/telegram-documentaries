"""Pure unit tests for the Phase 7 reset boundary: recognition and purge.

No network, no gateway loop: ``parse_reset_command`` is a pure predicate over
a typed ``Message`` and ``purge_chat_media`` is a pure filesystem operation.
Both paths of the recognition contract (typed entities first, narrow first-
token fallback) are asserted explicitly — a silent miss here is exactly the
"stuck state" failure this feature exists to prevent.
"""

from __future__ import annotations

from typing import Any

import pytest

from telegram_documentaries import bot_gateway
from telegram_documentaries.models import Message

#: A ``bot_command`` entity covering exactly the command part of ``text``.
BOT_COMMAND = "bot_command"


def entity(offset: int, length: int, type_: str = BOT_COMMAND) -> dict[str, Any]:
    return {"type": type_, "offset": offset, "length": length}


def text_message(
    text: str | None,
    *,
    entities: list[dict[str, Any]] | None = None,
    caption: str | None = None,
) -> Message:
    """Build a typed ``Message`` from a raw payload (fixtures stay honest)."""
    payload: dict[str, Any] = {
        "message_id": 1,
        "date": 1_760_000_200,
        "chat": {"id": 555, "type": "private"},
    }
    if text is not None:
        payload["text"] = text
    if caption is not None:
        payload["caption"] = caption
    if entities is not None:
        payload["entities"] = entities
    return Message.model_validate(payload)


class TestParseResetCommandEntityPath:
    """The primary path: Telegram's own ``bot_command`` markup at offset 0."""

    @pytest.mark.parametrize(
        ("text", "command_length", "expected"),
        [
            pytest.param("/start", 6, "start", id="start"),
            pytest.param("/restart", 8, "restart", id="restart"),
            pytest.param("/start@MyBot", 12, "start", id="bot-addressed"),
            pytest.param("/RESTART", 8, "restart", id="upper-case"),
            pytest.param("/Start", 6, "start", id="mixed-case"),
            # The entity covers only the command; the arguments are ignored.
            pytest.param("/start with args", 6, "start", id="with-arguments"),
        ],
    )
    def test_a_recognised_command_returns_its_name(
        self, text: str, command_length: int, expected: str
    ) -> None:
        message = text_message(text, entities=[entity(0, command_length)])

        assert bot_gateway.parse_reset_command(message) == expected

    @pytest.mark.parametrize(
        ("text", "command_length"),
        [
            pytest.param("//start", 7, id="double-slash"),
            pytest.param("/starting", 9, id="longer-word"),
            pytest.param("/restarts", 9, id="restart-plural"),
            pytest.param("/foo", 4, id="unknown-command"),
            pytest.param("start", 5, id="no-leading-slash"),
        ],
    )
    def test_a_non_reset_command_returns_none(
        self, text: str, command_length: int
    ) -> None:
        message = text_message(text, entities=[entity(0, command_length)])

        assert bot_gateway.parse_reset_command(message) is None


class TestParseResetCommandTextFallback:
    """The narrow fallback: first token of ``text.strip()``, no entities."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("/start", "start", id="start"),
            pytest.param("/restart", "restart", id="restart"),
            pytest.param("/Start", "start", id="mixed-case"),
            pytest.param("/start@MyBot more text", "start", id="addressed+args"),
            pytest.param("/start with args", "start", id="arguments"),
            pytest.param("/restart   ", "restart", id="trailing-whitespace"),
            pytest.param("  /start", "start", id="leading-whitespace"),
        ],
    )
    def test_a_valid_first_token_is_recognised(self, text: str, expected: str) -> None:
        message = text_message(text)

        assert bot_gateway.parse_reset_command(message) == expected

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("start", id="no-slash"),
            pytest.param("//start", id="double-slash"),
            pytest.param("/starting", id="longer-word"),
            pytest.param("/restarts", id="restart-plural"),
            pytest.param("/foo", id="unknown-command"),
            pytest.param("hello /start", id="leading-text"),
            pytest.param("", id="empty"),
            pytest.param("   ", id="whitespace-only"),
        ],
    )
    def test_anything_else_returns_none(self, text: str) -> None:
        message = text_message(text)

        assert bot_gateway.parse_reset_command(message) is None

    def test_a_recognised_command_is_never_missed_when_the_client_sends_no_entities(
        self,
    ) -> None:
        """The whole point of the fallback: entity-less clients still reset."""
        for text, expected in (("/start", "start"), ("/RESTART", "restart")):
            assert bot_gateway.parse_reset_command(text_message(text)) == expected


class TestParseResetCommandEdgeCases:
    """``text is None`` messages, offset!=0 entities and hostile payloads."""

    @pytest.mark.parametrize(
        ("text", "entities", "caption"),
        [
            pytest.param(None, None, None, id="photo-only"),
            pytest.param(None, [entity(0, 6)], None, id="photo-with-entities"),
            pytest.param(None, None, "/start", id="caption-only"),
        ],
    )
    def test_a_message_without_text_returns_none(
        self,
        text: str | None,
        entities: list[dict[str, Any]] | None,
        caption: str | None,
    ) -> None:
        message = text_message(text, entities=entities, caption=caption)

        assert bot_gateway.parse_reset_command(message) is None

    def test_a_bot_command_entity_not_at_offset_zero_falls_back_narrowly(
        self,
    ) -> None:
        """``say /start``: the entity is elsewhere, and the first token decides."""
        message = text_message("say /start", entities=[entity(4, 6)])

        assert bot_gateway.parse_reset_command(message) is None

    def test_an_out_of_range_entity_is_ignored_and_the_fallback_still_decides(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Hostile payload: never a crash, never a silent miss (``/restart``)."""
        message = text_message("/restart", entities=[entity(0, 999)])

        with caplog.at_level("WARNING"):
            result = bot_gateway.parse_reset_command(message)

        assert result == "restart"
        warning = [r.getMessage() for r in caplog.records if r.levelno >= 30]
        assert len(warning) == 1
        # Numeric lengths only — never the message text itself.
        assert "offset=0" in warning[0]
        assert "length=999" in warning[0]
        assert "/restart" not in caplog.text

    def test_an_out_of_range_entity_over_text_that_is_not_a_command_returns_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        message = text_message("/foo", entities=[entity(0, 999)])

        with caplog.at_level("WARNING"):
            result = bot_gateway.parse_reset_command(message)

        assert result is None
        assert any(r.levelno >= 30 for r in caplog.records)

    def test_an_out_of_range_entity_on_empty_text_is_ignored(self) -> None:
        message = text_message("", entities=[entity(0, 6)])

        assert bot_gateway.parse_reset_command(message) is None

    def test_a_non_bot_command_entity_at_offset_zero_is_not_consulted(self) -> None:
        """A hostile ``bold`` entity at offset 0 must not drive recognition."""
        message = text_message("/start", entities=[entity(0, 6, type_="bold")])

        assert bot_gateway.parse_reset_command(message) == "start"


class TestPurgeChatMedia:
    def test_purges_the_chat_directory_and_returns_the_file_count(
        self, tmp_path: Any
    ) -> None:
        (tmp_path / "555").mkdir()
        (tmp_path / "555" / "portrait.jpg").write_bytes(b"JPEG")
        (tmp_path / "555" / "notes.txt").write_text("notes")

        removed = bot_gateway.purge_chat_media(tmp_path, 555)

        assert removed == 2
        assert not (tmp_path / "555").exists()

    def test_a_missing_directory_returns_zero_and_never_raises(
        self, tmp_path: Any
    ) -> None:
        """The idempotent path: a chat that never stored anything."""
        assert bot_gateway.purge_chat_media(tmp_path, 555) == 0
        assert bot_gateway.purge_chat_media(tmp_path, 555) == 0

    def test_an_empty_chat_directory_is_removed_and_returns_zero(
        self, tmp_path: Any
    ) -> None:
        (tmp_path / "555").mkdir()

        assert bot_gateway.purge_chat_media(tmp_path, 555) == 0
        assert not (tmp_path / "555").exists()

    def test_a_sibling_chat_directory_and_the_media_root_survive(
        self, tmp_path: Any
    ) -> None:
        (tmp_path / "555").mkdir()
        (tmp_path / "555" / "portrait.jpg").write_bytes(b"JPEG")
        (tmp_path / "777").mkdir()
        (tmp_path / "777" / "portrait.jpg").write_bytes(b"KEEP-ME")

        bot_gateway.purge_chat_media(tmp_path, 555)

        assert (tmp_path / "777" / "portrait.jpg").read_bytes() == b"KEEP-ME"
        assert tmp_path.exists()

    def test_a_symlinked_chat_directory_escaping_the_root_is_refused(
        self, tmp_path: Any
    ) -> None:
        """Containment guard: a poisoned directory is never followed, kept intact."""
        media_root = tmp_path / "media"
        media_root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "loot.txt").write_text("not yours")
        (media_root / "555").symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError):
            bot_gateway.purge_chat_media(media_root, 555)

        assert (outside / "loot.txt").exists()
        assert (media_root / "555").is_symlink()

    def test_a_chat_directory_symlinked_to_the_root_itself_is_refused(
        self, tmp_path: Any
    ) -> None:
        """The target must be a *strict* child of the root, never the root."""
        media_root = tmp_path / "media"
        media_root.mkdir()
        (media_root / "555").symlink_to(media_root, target_is_directory=True)

        with pytest.raises(ValueError):
            bot_gateway.purge_chat_media(media_root, 555)

        assert media_root.exists()

    def test_a_refusal_logs_an_error_before_raising(
        self, tmp_path: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        media_root = tmp_path / "media"
        media_root.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (media_root / "555").symlink_to(outside, target_is_directory=True)

        with caplog.at_level("ERROR"), pytest.raises(ValueError):
            bot_gateway.purge_chat_media(media_root, 555)

        assert any(r.levelno >= 40 for r in caplog.records)

    def test_a_non_file_not_found_oserror_propagates(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail loud: only *absence* is idempotent; real failures reach the handler."""
        (tmp_path / "555").mkdir()
        (tmp_path / "555" / "portrait.jpg").write_bytes(b"JPEG")

        def explode(target: Any) -> None:
            raise OSError("disk on fire")

        monkeypatch.setattr(bot_gateway.shutil, "rmtree", explode)

        with pytest.raises(OSError):
            bot_gateway.purge_chat_media(tmp_path, 555)

    def test_a_file_not_found_from_the_filesystem_returns_zero(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file already gone between counting and deletion is still idempotent."""
        (tmp_path / "555").mkdir()
        (tmp_path / "555" / "portrait.jpg").write_bytes(b"JPEG")

        def vanish(target: Any) -> None:
            raise FileNotFoundError(target)

        monkeypatch.setattr(bot_gateway.shutil, "rmtree", vanish)

        assert bot_gateway.purge_chat_media(tmp_path, 555) == 0

    def test_the_deletion_is_derived_from_chat_id_not_a_session_path(
        self, tmp_path: Any
    ) -> None:
        """Schemas over heuristics: only ``media_root`` + ``chat_id`` decide."""
        (tmp_path / "555").mkdir()
        (tmp_path / "555" / "portrait.jpg").write_bytes(b"JPEG")

        bot_gateway.purge_chat_media(str(tmp_path), 555)

        assert not (tmp_path / "555").exists()
