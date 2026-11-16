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

    wtb.on_whatsapp_message(None, event)

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
        wtb.on_whatsapp_message(None, event)

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

    wtb.on_whatsapp_message(None, event)

    assert "<b>Sender:</b> 34987654321" in telegram_calls[0][1]["text"]


def test_ignored_group_message_logs_group_name_and_sender(
    app_database: Any,
    whatsapp_event_factory: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = whatsapp_event_factory(
        chat_user="diagnostic-group-123", chat_server="g.us", pushname="Group member"
    )
    client = type(
        "Client",
        (),
        {"get_group_info": lambda _self, _jid: type("Info", (), {"GroupName": "Test group"})()},
    )()

    with caplog.at_level(logging.DEBUG, logger="wtb"):
        wtb.on_whatsapp_message(client, event)

    assert "Ignoring WhatsApp group message" in caplog.text
    assert "chat_jid=diagnostic-group-123@g.us" in caplog.text
    assert "group_name='Test group'" in caplog.text
    assert "sender_jid=34111111111@s.whatsapp.net" in caplog.text


def test_ignored_broadcast_message_logs_broadcast_type(
    app_database: Any,
    whatsapp_event_factory: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = whatsapp_event_factory(chat_user="status", chat_server="broadcast")

    with caplog.at_level(logging.DEBUG, logger="wtb"):
        wtb.on_whatsapp_message(None, event)

    assert "Ignoring WhatsApp broadcast message" in caplog.text
    assert "chat_jid=status@broadcast" in caplog.text
    assert "group_name=None" in caplog.text


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

    wtb.on_whatsapp_message(None, event)

    assert telegram_calls == []
    assert message_rows(app_database) == []


def test_message_before_telegram_registration_is_not_persisted(
    app_database: Any, telegram_calls: list[Any], whatsapp_event_factory: Any
) -> None:
    wtb.on_whatsapp_message(None, whatsapp_event_factory())

    assert telegram_calls == []
    assert message_rows(app_database) == []


def test_duplicate_whatsapp_message_is_forwarded_once(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    whatsapp_event_factory: Any,
) -> None:
    event = whatsapp_event_factory(message_id="same-id")

    wtb.on_whatsapp_message(None, event)
    wtb.on_whatsapp_message(None, event)

    assert len(telegram_calls) == 1
    assert len(message_rows(app_database)) == 1


def test_long_message_is_split_within_telegram_limit(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    whatsapp_event_factory: Any,
) -> None:
    event = whatsapp_event_factory(text="x" * 5000)

    wtb.on_whatsapp_message(None, event)

    assert len(telegram_calls) == 2
    assert all(len(payload["text"]) <= 4096 for _, payload in telegram_calls)
    assert "WhatsApp message ID" not in telegram_calls[0][1]["text"]
    assert "WhatsApp message ID" in telegram_calls[1][1]["text"]
    assert message_rows(app_database)[0]["status"] == "SENT"


def test_forwards_image_as_telegram_photo_with_sender_header(
    app_database: Any,
    registered_chat: int,
    telegram_operations: list[tuple[str, Any]],
    whatsapp_event_factory: Any,
) -> None:
    image_data = b"fake-jpeg-data"
    event = whatsapp_event_factory(
        text="", image_caption="", image_mime_type="image/jpeg", pushname="Alice"
    )
    client = type("Client", (), {"download_any": lambda _self, _message: image_data})()

    wtb.on_whatsapp_message(client, event)

    assert len(telegram_operations) == 1
    method, photo = telegram_operations[0]
    assert method == "sendPhoto"
    assert photo[0] == registered_chat
    assert "<b>Sender:</b> Alice" in photo[1]
    assert "WhatsApp message ID: wa-message-1" in photo[1]
    assert photo[2:] == (image_data, "image/jpeg")
    assert message_rows(app_database)[0]["status"] == "SENT"


def test_forwards_image_caption_in_telegram_photo_caption(
    app_database: Any,
    registered_chat: int,
    telegram_operations: list[tuple[str, Any]],
    whatsapp_event_factory: Any,
) -> None:
    event = whatsapp_event_factory(
        text="", image_caption="A caption", pushname="Alice & Bob"
    )
    client = type("Client", (), {"download_any": lambda _self, _message: b"image"})()

    wtb.on_whatsapp_message(client, event)

    assert len(telegram_operations) == 1
    method, photo = telegram_operations[0]
    assert method == "sendPhoto"
    assert "<b>Sender:</b> Alice &amp; Bob" in photo[1]
    assert "A caption" in photo[1]


def test_long_image_caption_is_sent_as_photo_then_text_chunks(
    app_database: Any,
    registered_chat: int,
    telegram_operations: list[tuple[str, Any]],
    whatsapp_event_factory: Any,
) -> None:
    long_caption = "x" * 5000
    event = whatsapp_event_factory(
        text="", image_caption=long_caption, pushname="Alice"
    )
    client = type("Client", (), {"download_any": lambda _self, _message: b"image"})()

    wtb.on_whatsapp_message(client, event)

    assert [method for method, _payload in telegram_operations] == [
        "sendPhoto",
        "sendMessage",
        "sendMessage",
    ]
    photo = telegram_operations[0][1]
    assert photo[0] == registered_chat
    assert "<b>Sender:</b> Alice" in photo[1]
    text_payloads = [payload for _method, payload in telegram_operations[1:]]
    assert all(payload["chat_id"] == registered_chat for payload in text_payloads)
    assert sum(payload["text"].count("x") for payload in text_payloads) == len(
        long_caption
    )
    assert "WhatsApp message ID: wa-message-1" in text_payloads[-1]["text"]
    assert all("<b>Sender:</b> Alice" in payload["text"] for payload in text_payloads)


def test_oversized_image_is_replaced_with_text_notice(
    app_database: Any,
    registered_chat: int,
    telegram_operations: list[tuple[str, Any]],
    whatsapp_event_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wtb, "TELEGRAM_PHOTO_SIZE_LIMIT", 4)
    event = whatsapp_event_factory(text="image caption", image_caption="image caption")
    client = type("Client", (), {"download_any": lambda _self, _message: b"image"})()

    wtb.on_whatsapp_message(client, event)

    assert [method for method, _payload in telegram_operations] == ["sendMessage"]
    payload = telegram_operations[0][1]
    assert payload["chat_id"] == registered_chat
    assert "image caption" in payload["text"]
    assert "WhatsApp message ID: wa-message-1" in payload["text"]
    assert "Attachment too large to forward" in payload["text"]



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
        wtb.on_whatsapp_message(None, whatsapp_event_factory())

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
