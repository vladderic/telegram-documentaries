"""Tests for .env-driven settings loading and log-secret hygiene (TECH.md).

Every secret-looking value in this file is a placeholder - tests never read
or use real secrets.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

import pytest

from telegram_documentaries import config as config_module
from telegram_documentaries.config import ConfigError, Settings, load_settings

# Placeholders only - never use or read real secrets in tests.
PLACEHOLDER_TOKEN = "123456:PLACEHOLDER-TELEGRAM-BOT-TOKEN"
PLACEHOLDER_GEMINI_KEY = "PLACEHOLDER-GEMINI-API-KEY"


def _write_dotenv(path: Path, **values: str) -> None:
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    path.write_text(lines, encoding="utf-8")


class TestLoadSettings:
    def test_returns_token_from_provided_environment(self) -> None:
        environ: Mapping[str, str] = {"TELEGRAM_BOT_TOKEN": PLACEHOLDER_TOKEN}

        settings = load_settings(environ)

        assert isinstance(settings, Settings)
        assert settings.telegram_bot_token == PLACEHOLDER_TOKEN

    def test_strips_surrounding_whitespace_from_token(self) -> None:
        environ: Mapping[str, str] = {"TELEGRAM_BOT_TOKEN": f"  {PLACEHOLDER_TOKEN}  "}

        settings = load_settings(environ)

        assert settings.telegram_bot_token == PLACEHOLDER_TOKEN

    @pytest.mark.parametrize(
        "environ",
        [{}, {"TELEGRAM_BOT_TOKEN": ""}, {"TELEGRAM_BOT_TOKEN": "   "}],
        ids=["missing", "empty", "whitespace-only"],
    )
    def test_raises_config_error_when_token_absent_or_empty(
        self, environ: Mapping[str, str]
    ) -> None:
        with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
            load_settings(environ)

    def test_never_logs_the_token_value(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.DEBUG):
            load_settings({"TELEGRAM_BOT_TOKEN": PLACEHOLDER_TOKEN})

        assert PLACEHOLDER_TOKEN not in caplog.text


class TestDotenvLoading:
    """load_settings() with no explicit environment must load the .env file."""

    def test_loads_secrets_from_dotenv_file_relative_to_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_dotenv(
            tmp_path / ".env",
            TELEGRAM_BOT_TOKEN=PLACEHOLDER_TOKEN,
            GEMINI_API_KEY=PLACEHOLDER_GEMINI_KEY,
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        settings = load_settings()

        assert settings.telegram_bot_token == PLACEHOLDER_TOKEN
        assert settings.gemini_api_key == PLACEHOLDER_GEMINI_KEY

    def test_real_environment_variables_win_over_dotenv_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Production overrides (real env) must never be clobbered by .env."""
        env_token = "999:FROM-REAL-ENV-PLACEHOLDER"
        _write_dotenv(tmp_path / ".env", TELEGRAM_BOT_TOKEN=PLACEHOLDER_TOKEN)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", env_token)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        settings = load_settings()

        assert settings.telegram_bot_token == env_token

    def test_never_logs_secret_values_loaded_from_dotenv(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _write_dotenv(
            tmp_path / ".env",
            TELEGRAM_BOT_TOKEN=PLACEHOLDER_TOKEN,
            GEMINI_API_KEY=PLACEHOLDER_GEMINI_KEY,
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        with caplog.at_level(logging.DEBUG):
            load_settings()

        assert PLACEHOLDER_TOKEN not in caplog.text
        assert PLACEHOLDER_GEMINI_KEY not in caplog.text


class TestGeminiApiKey:
    def test_loaded_when_present(self) -> None:
        environ: Mapping[str, str] = {
            "TELEGRAM_BOT_TOKEN": PLACEHOLDER_TOKEN,
            "GEMINI_API_KEY": PLACEHOLDER_GEMINI_KEY,
        }

        settings = load_settings(environ)

        assert settings.gemini_api_key == PLACEHOLDER_GEMINI_KEY

    @pytest.mark.parametrize(
        "extra",
        [None, {"GEMINI_API_KEY": ""}, {"GEMINI_API_KEY": "   "}],
        ids=["missing", "empty", "whitespace-only"],
    )
    def test_is_none_when_not_configured(self, extra: Mapping[str, str] | None) -> None:
        """Phase 1 does not call Gemini yet, so the key must be optional."""
        environ: dict[str, str] = {"TELEGRAM_BOT_TOKEN": PLACEHOLDER_TOKEN}
        if extra is not None:
            environ.update(extra)

        settings = load_settings(environ)

        assert settings.gemini_api_key is None

    def test_never_logs_the_gemini_api_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        environ: Mapping[str, str] = {
            "TELEGRAM_BOT_TOKEN": PLACEHOLDER_TOKEN,
            "GEMINI_API_KEY": PLACEHOLDER_GEMINI_KEY,
        }

        with caplog.at_level(logging.DEBUG):
            load_settings(environ)

        assert PLACEHOLDER_GEMINI_KEY not in caplog.text
        assert PLACEHOLDER_TOKEN not in caplog.text


class TestSecretRedactionFilter:
    """The prevention mechanism: secrets must never reach log output."""

    @staticmethod
    def _record(msg: str, args: tuple[object, ...] = ()) -> logging.LogRecord:
        return logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=args,
            exc_info=None,
        )

    def test_redacts_secret_from_an_already_formatted_message(self) -> None:
        record = self._record(f"connecting with token {PLACEHOLDER_TOKEN}")

        allowed = config_module.SecretRedactionFilter(PLACEHOLDER_TOKEN).filter(record)

        assert allowed is True
        assert PLACEHOLDER_TOKEN not in record.getMessage()
        assert "[REDACTED]" in record.getMessage()

    def test_redacts_secret_from_lazy_percent_format_args(self) -> None:
        record = self._record("token is %s", (PLACEHOLDER_TOKEN,))

        config_module.SecretRedactionFilter(PLACEHOLDER_TOKEN).filter(record)

        assert PLACEHOLDER_TOKEN not in record.getMessage()

    def test_leaves_unrelated_records_untouched(self) -> None:
        record = self._record("all good: nothing secret here")

        config_module.SecretRedactionFilter(PLACEHOLDER_TOKEN).filter(record)

        assert record.getMessage() == "all good: nothing secret here"
        assert record.args == ()

    def test_install_makes_root_handlers_redact_logged_output(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        config_module.install_secret_redaction(PLACEHOLDER_TOKEN)

        with caplog.at_level(logging.DEBUG):
            logging.getLogger("telegram_documentaries.somewhere").warning(
                "upstream said: %s", PLACEHOLDER_TOKEN
            )

        assert PLACEHOLDER_TOKEN not in caplog.text
        assert "[REDACTED]" in caplog.text
