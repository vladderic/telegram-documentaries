"""Configuration and logging setup for the Telegram Documentaries bot.

Secrets come from `.env` / the process environment only (TECH.md: never
hardcoded, never logged). Every log line here deliberately reports *presence*
of a secret rather than its value.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from dotenv import find_dotenv, load_dotenv

logger = logging.getLogger(__name__)

#: Replacement used whenever a secret value would otherwise appear in output.
REDACTED = "[REDACTED]"


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class Settings:
    """Runtime settings. Secret values must never be logged."""

    def __init__(
        self,
        telegram_bot_token: str,
        gemini_api_key: str,
    ) -> None:
        self.telegram_bot_token = telegram_bot_token
        self.gemini_api_key = gemini_api_key


class SecretRedactionFilter(logging.Filter):
    """Scrub one secret value from log records, including tracebacks.

    Attached to root *handlers* (see :func:`install_secret_redaction`) so it
    covers records propagated from child loggers and third-party libraries
    (whose error messages may embed URLs containing the bot token).
    """

    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secret:
            return True
        message = record.getMessage()
        if self.secret in message:
            record.msg = message.replace(self.secret, REDACTED)
            record.args = ()
        if record.exc_info:
            formatted = record.exc_text or logging.Formatter().formatException(
                record.exc_info
            )
            if self.secret in formatted:
                record.exc_text = formatted.replace(self.secret, REDACTED)
        return True


def install_secret_redaction(secret: str) -> None:
    """Make every installed root log handler redact ``secret`` from output."""
    for handler in logging.getLogger().handlers:
        already_installed = any(
            isinstance(existing, SecretRedactionFilter) and existing.secret == secret
            for existing in handler.filters
        )
        if not already_installed:
            handler.addFilter(SecretRedactionFilter(secret))


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging once (no-op if a handler is already installed)."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def _load_dotenv_file() -> None:
    """Load ``.env`` from the working directory or its parents.

    Real environment variables always win (``override=False``), and the file's
    contents are never logged.
    """
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)


def _get_required_str(environ: Mapping[str, str], key: str) -> str:
    """Fetch a required string from the environment.

    Args:
        environ: Environment mapping to read from.
        key: Name of the environment variable.

    Returns:
        The stripped value.

    Raises:
        ConfigError: If the value is missing or empty after stripping.
    """
    value = environ.get(key)
    if value is None:
        raise ConfigError(f"Missing required environment variable: {key}")
    stripped = value.strip()
    if stripped == "":
        raise ConfigError(f"Missing required environment variable: {key}")
    return stripped


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load settings from ``.env`` / the process environment.

    Args:
        environ: Environment to read from. Defaults to the process environment
            after loading ``.env``; injectable so tests never touch real
            secrets.

    Returns:
        The loaded settings. Never logged with secret values (TECH.md).

    Raises:
        ConfigError: If ``TELEGRAM_BOT_TOKEN`` or ``GEMINI_API_KEY`` is
            missing or empty (the Bouncer cannot run without Gemini vision).
    """
    if environ is None:
        _load_dotenv_file()
        env: Mapping[str, str] = os.environ
    else:
        env = environ

    telegram_bot_token = _get_required_str(env, "TELEGRAM_BOT_TOKEN")
    gemini_api_key = _get_required_str(env, "GEMINI_API_KEY")

    # Never log secrets (TECH.md): report configuration *presence* only.
    logger.info(
        "Configuration loaded (values never logged): GEMINI_API_KEY present=%s",
        gemini_api_key is not None,
    )
    return Settings(
        telegram_bot_token=telegram_bot_token,
        gemini_api_key=gemini_api_key,
    )
