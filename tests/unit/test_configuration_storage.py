"""Tests for application configuration and SQLite persistence."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from src import wtb


def set_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("PHONE_NUMBER", "34123456789")


def test_load_configuration_reads_and_validates_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_valid_environment(monkeypatch)
    monkeypatch.setattr(wtb, "load_dotenv", lambda _path: False)
    configured_levels: list[str] = []
    monkeypatch.setattr(wtb, "configure_logging", configured_levels.append)

    config = wtb.load_configuration()

    assert config == {
        "LOG_LEVEL": "DEBUG",
        "TELEGRAM_BOT_TOKEN": "test-token",
        "PHONE_NUMBER": "34123456789",
    }
    assert configured_levels == ["DEBUG"]


def test_load_configuration_rejects_missing_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wtb, "load_dotenv", lambda _path: False)
    for name in ("LOG_LEVEL", "TELEGRAM_BOT_TOKEN", "PHONE_NUMBER"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="Missing required environment variables"):
        wtb.load_configuration()


@pytest.mark.parametrize("phone", ["+34123456789", "123", "0123456789", "34abc56789"])
def test_load_configuration_rejects_invalid_phone_number(
    monkeypatch: pytest.MonkeyPatch, phone: str
) -> None:
    set_valid_environment(monkeypatch)
    monkeypatch.setenv("PHONE_NUMBER", phone)
    monkeypatch.setattr(wtb, "load_dotenv", lambda _path: False)

    with pytest.raises(ValueError, match="PHONE_NUMBER"):
        wtb.load_configuration()


def test_load_configuration_rejects_invalid_log_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_valid_environment(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    monkeypatch.setattr(wtb, "load_dotenv", lambda _path: False)

    with pytest.raises(ValueError, match="LOG_LEVEL"):
        wtb.load_configuration()


def test_configure_logging_forces_requested_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(wtb.logging, "basicConfig", lambda **kwargs: calls.append(kwargs))

    wtb.configure_logging("DEBUG")

    assert calls[0]["level"] == logging.DEBUG
    assert calls[0]["force"] is True


def test_sigterm_handler_requests_the_graceful_shutdown_path() -> None:
    with pytest.raises(KeyboardInterrupt):
        wtb.handle_sigterm(15, None)


def test_initialize_database_creates_schema_and_recovers_processing_rows(
    app_database: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with app_database:
        app_database.execute(
            "INSERT INTO messages "
            "(wa_message_id, wa_chat_jid, direction, status) "
            "VALUES ('unfinished', '34111111111@s.whatsapp.net', 'WHATSAPP_TO_TELEGRAM', 'PROCESSING')"
        )
        app_database.execute(
            "INSERT INTO messages "
            "(wa_message_id, wa_chat_jid, direction, status) "
            "VALUES ('done', '34111111111@s.whatsapp.net', 'WHATSAPP_TO_TELEGRAM', 'SENT')"
        )

    with caplog.at_level(logging.ERROR, logger="wtb"):
        recovered_database = wtb.initialize_database()
    try:
        statuses = {
            row["wa_message_id"]: row["status"]
            for row in recovered_database.execute(
                "SELECT wa_message_id, status FROM messages"
            )
        }
        assert statuses == {"unfinished": "FAILED", "done": "SENT"}
        assert "interrupted message delivery" in caplog.text
    finally:
        recovered_database.close()


def test_get_registered_chat_id_returns_stored_chat(
    app_database: Any, registered_chat: int
) -> None:
    assert wtb.get_registered_chat_id() == registered_chat


def test_get_registered_chat_id_returns_none_when_unregistered(
    app_database: Any,
) -> None:
    assert wtb.get_registered_chat_id() is None


def test_phone_normalization_removes_non_digits() -> None:
    assert wtb.normalize_phone_number("+34 (612) 345-678") == "34612345678"


def test_message_text_supports_plain_and_extended_text() -> None:
    class Message:
        conversation = "  plain text  "
        extendedTextMessage = type("Extended", (), {"text": "extended"})()

    assert wtb.message_text(Message()) == "plain text"
    Message.conversation = ""
    assert wtb.message_text(Message()) == "extended"


def test_split_html_text_escapes_and_splits_without_breaking_entities() -> None:
    escaped_chunks = wtb.split_html_text("a<&", max_length=5)

    assert "".join(escaped_chunks) == "a&lt;&amp;"
    assert all(len(chunk) <= 5 for chunk in escaped_chunks)


def test_format_jid_uses_user_and_server() -> None:
    jid = type("JID", (), {"User": "34123456789", "Server": "s.whatsapp.net"})()

    assert wtb.format_jid(jid) == "34123456789@s.whatsapp.net"


def test_format_jid_falls_back_to_string() -> None:
    jid = type("JID", (), {"User": "", "Server": ""})()

    assert wtb.format_jid(jid) == str(jid)
