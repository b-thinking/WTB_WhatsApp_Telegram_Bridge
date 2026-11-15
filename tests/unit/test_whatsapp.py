"""Tests for WhatsApp message filtering, sender selection, and forwarding."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import pytest

from src import wtb


def message_rows(database: Any) -> list[Any]:
    return database.execute("SELECT * FROM messages ORDER BY record_id").fetchall()


def test_forwards_message_with_pushname_and_original_id(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    whatsapp_event_factory: Any,
) -> None:
    event = whatsapp_event_factory(
        pushname="Alice & Bob", text="Hello <there>", message_id="wa-123"
    )

    wtb.handle_whatsapp_message(None, event)

    assert len(telegram_calls) == 1
    method, payload = telegram_calls[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == registered_chat
    assert payload["parse_mode"] == "HTML"
    assert "<b>Sender:</b> Alice &amp; Bob" in payload["text"]
    assert "Hello &lt;there&gt;" in payload["text"]
    assert "WhatsApp message ID: wa-123" in payload["text"]
    row = message_rows(app_database)[0]
    assert row["status"] == "SENT"
    assert row["telegram_message_id"] == 701


def test_uses_sender_alternate_when_primary_jid_is_lid(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    whatsapp_event_factory: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = whatsapp_event_factory(
        pushname="",
        sender_user="987654321012345",
        sender_server="lid",
        sender_alt_user="34123456789",
        sender_alt_server="s.whatsapp.net",
    )

    with caplog.at_level(logging.DEBUG, logger="wtb"):
        wtb.handle_whatsapp_message(None, event)

    assert "<b>Sender:</b> 34123456789" in telegram_calls[0][1]["text"]
    assert "sender_alt_available=True" in caplog.text
    assert "Selected WhatsApp sender from sender_alt" in caplog.text
    assert "987654321012345" not in caplog.text


def test_falls_back_to_sender_jid_when_pushname_is_empty(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    whatsapp_event_factory: Any,
) -> None:
    event = whatsapp_event_factory(pushname="", sender_user="34987654321")

    wtb.handle_whatsapp_message(None, event)

    assert "<b>Sender:</b> 34987654321" in telegram_calls[0][1]["text"]


@pytest.mark.parametrize(
    "event_options",
    [
        {"is_from_me": True},
        {"chat_server": "g.us"},
        {"chat_server": "broadcast"},
        {"text": ""},
    ],
    ids=["from-me", "group", "broadcast", "empty-text"],
)
def test_ignores_unsupported_whatsapp_messages(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    whatsapp_event_factory: Any,
    event_options: dict[str, Any],
) -> None:
    event = whatsapp_event_factory(**event_options)

    wtb.handle_whatsapp_message(None, event)

    assert telegram_calls == []
    assert message_rows(app_database) == []


def test_message_before_telegram_registration_is_not_persisted(
    app_database: Any, telegram_calls: list[Any], whatsapp_event_factory: Any
) -> None:
    wtb.handle_whatsapp_message(None, whatsapp_event_factory())

    assert telegram_calls == []
    assert message_rows(app_database) == []


def test_duplicate_whatsapp_message_is_forwarded_once(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    whatsapp_event_factory: Any,
) -> None:
    event = whatsapp_event_factory(message_id="same-id")

    wtb.handle_whatsapp_message(None, event)
    wtb.handle_whatsapp_message(None, event)

    assert len(telegram_calls) == 1
    assert len(message_rows(app_database)) == 1


def test_long_message_is_split_within_telegram_limit(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    whatsapp_event_factory: Any,
) -> None:
    event = whatsapp_event_factory(text="x" * 5000)

    wtb.handle_whatsapp_message(None, event)

    assert len(telegram_calls) == 2
    assert all(len(payload["text"]) <= 4096 for _, payload in telegram_calls)
    assert "WhatsApp message ID" not in telegram_calls[0][1]["text"]
    assert "WhatsApp message ID" in telegram_calls[1][1]["text"]
    assert message_rows(app_database)[0]["status"] == "SENT"


def test_telegram_send_failure_marks_message_failed(
    app_database: Any,
    registered_chat: int,
    monkeypatch: pytest.MonkeyPatch,
    whatsapp_event_factory: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_request(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(wtb, "telegram_request", fail_request)

    with caplog.at_level(logging.ERROR, logger="wtb"):
        wtb.handle_whatsapp_message(None, whatsapp_event_factory())

    assert message_rows(app_database)[0]["status"] == "FAILED"
    assert "Failed to process an incoming WhatsApp message" in caplog.text
    assert "telegram unavailable" in caplog.text


def test_duplicate_database_key_prevents_second_insert(
    app_database: Any,
) -> None:
    with app_database:
        app_database.execute(
            "INSERT INTO messages "
            "(wa_message_id, wa_chat_jid, direction, status) "
            "VALUES ('same-id', 'chat@s.whatsapp.net', 'WHATSAPP_TO_TELEGRAM', 'SENT')"
        )

    with pytest.raises(sqlite3.IntegrityError), app_database:
        app_database.execute(
            "INSERT INTO messages "
            "(wa_message_id, wa_chat_jid, direction, status) "
            "VALUES ('same-id', 'chat@s.whatsapp.net', 'WHATSAPP_TO_TELEGRAM', 'SENT')"
        )
