"""Tests for Telegram API calls and chat registration."""

from __future__ import annotations

import json
from typing import Any, Self
from urllib.error import URLError

import pytest

from src import wtb


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_telegram_request_posts_json_with_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wtb, "CONFIG", {"TELEGRAM_BOT_TOKEN": "test-token"})
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"ok": True, "result": {"message_id": 12}})

    monkeypatch.setattr(wtb, "urlopen", fake_urlopen)

    result = wtb.telegram_request("sendMessage", {"chat_id": 3, "text": "hi"})

    assert result["result"]["message_id"] == 12
    assert captured["url"].endswith("/bottest-token/sendMessage")
    assert captured["body"] == {"chat_id": 3, "text": "hi"}
    assert captured["timeout"] == 40


def test_telegram_request_hides_token_when_transport_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wtb, "CONFIG", {"TELEGRAM_BOT_TOKEN": "secret-token"})
    monkeypatch.setattr(
        wtb, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline"))
    )

    with pytest.raises(RuntimeError, match="Telegram API request failed") as error:
        wtb.telegram_request("getUpdates", {})

    assert "secret-token" not in str(error.value)
    assert "offline" in str(error.value)


def test_telegram_request_raises_api_description(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wtb, "CONFIG", {"TELEGRAM_BOT_TOKEN": "test-token"})
    monkeypatch.setattr(
        wtb,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            {"ok": False, "description": "chat not found"}
        ),
    )

    with pytest.raises(RuntimeError, match="chat not found"):
        wtb.telegram_request("sendMessage", {})


def test_telegram_send_photo_uploads_multipart_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wtb, "CONFIG", {"TELEGRAM_BOT_TOKEN": "test-token"})
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["content_type"] = request.get_header("Content-type")
        captured["body"] = request.data
        captured["timeout"] = timeout
        return FakeResponse({"ok": True, "result": {"message_id": 12}})

    monkeypatch.setattr(wtb, "urlopen", fake_urlopen)
    image_data = b"\x00\x01fake-image-data"

    result = wtb.telegram_send_photo(123, "<b>Sender:</b> Alice", image_data, "image/jpeg")

    assert result["result"]["message_id"] == 12
    assert captured["url"].endswith("/bottest-token/sendPhoto")
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    assert b'name="chat_id"\r\n\r\n123' in captured["body"]
    assert b'name="caption"\r\n\r\n<b>Sender:</b> Alice' in captured["body"]
    assert b'filename="whatsapp-image.jpg"' in captured["body"]
    assert image_data in captured["body"]
    assert captured["timeout"] == 40


@pytest.mark.parametrize(
    ("send_function", "method", "media_field", "filename"),
    [
        ("telegram_send_video", "sendVideo", "video", "whatsapp-video.mp4"),
        (
            "telegram_send_animation",
            "sendAnimation",
            "animation",
            "whatsapp-animation.mp4",
        ),
    ],
)
def test_telegram_video_upload_uses_method_and_file_field(
    monkeypatch: pytest.MonkeyPatch,
    send_function: str,
    method: str,
    media_field: str,
    filename: str,
) -> None:
    monkeypatch.setattr(wtb, "CONFIG", {"TELEGRAM_BOT_TOKEN": "test-token"})
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: int) -> FakeResponse:
        captured["url"] = request.full_url
        captured["body"] = request.data
        return FakeResponse({"ok": True, "result": {"message_id": 12}})

    monkeypatch.setattr(wtb, "urlopen", fake_urlopen)
    media_data = b"fake-mp4-data"

    result = getattr(wtb, send_function)(3, "caption", media_data, "video/mp4")

    assert result["result"]["message_id"] == 12
    assert captured["url"].endswith(f"/bottest-token/{method}")
    assert f'name="{media_field}"'.encode() in captured["body"]
    assert f'filename="{filename}"'.encode() in captured["body"]
    assert media_data in captured["body"]


def test_private_message_without_contact_requests_contact(
    app_database: Any, monkeypatch: pytest.MonkeyPatch, telegram_calls: list[Any]
) -> None:
    wtb.telegram_process_update(
        {
            "message": {
                "chat": {"id": 100, "type": "private"},
                "from": {"id": 100},
                "text": "hello",
            }
        }
    )

    assert telegram_calls[0][0] == "sendMessage"
    keyboard = telegram_calls[0][1]["reply_markup"]["keyboard"]
    assert keyboard[0][0]["request_contact"] is True
    assert wtb.telegram_get_registered_chat_id() is None


def test_registration_rejects_contact_belonging_to_another_user(
    app_database: Any, telegram_calls: list[Any]
) -> None:
    wtb.telegram_process_update(
        {
            "message": {
                "chat": {"id": 100, "type": "private"},
                "from": {"id": 100},
                "contact": {"user_id": 200, "phone_number": "+34123456789"},
            }
        }
    )

    assert "share your own contact" in telegram_calls[0][1]["text"]
    assert wtb.telegram_get_registered_chat_id() is None


def test_registration_rejects_phone_mismatch(
    app_database: Any, telegram_calls: list[Any]
) -> None:
    wtb.telegram_process_update(
        {
            "message": {
                "chat": {"id": 100, "type": "private"},
                "from": {"id": 100},
                "contact": {"user_id": 100, "phone_number": "+34987654321"},
            }
        }
    )

    assert "phone numbers do not match" in telegram_calls[0][1]["text"]
    assert wtb.telegram_get_registered_chat_id() is None


def test_registration_accepts_matching_user_and_phone(
    app_database: Any, telegram_calls: list[Any]
) -> None:
    wtb.telegram_process_update(
        {
            "message": {
                "chat": {"id": 100, "type": "private"},
                "from": {"id": 100},
                "contact": {"user_id": 100, "phone_number": "+34 123 456 789"},
            }
        }
    )

    assert wtb.telegram_get_registered_chat_id() == 100
    assert telegram_calls[-1][1]["text"].startswith("Registration complete")


def test_registration_ignores_non_private_chats(
    app_database: Any, telegram_calls: list[Any], caplog: pytest.LogCaptureFixture
) -> None:
    wtb.telegram_process_update(
        {"message": {"chat": {"id": -100, "type": "group"}, "from": {"id": 1}}}
    )

    assert telegram_calls == []
    assert "outside a private chat" in caplog.text


def test_registered_chat_is_not_replaced(
    app_database: Any, registered_chat: int, telegram_calls: list[Any]
) -> None:
    wtb.telegram_process_update(
        {
            "message": {
                "chat": {"id": 999, "type": "private"},
                "from": {"id": 999},
                "contact": {"user_id": 999, "phone_number": "34123456789"},
            }
        }
    )

    assert wtb.telegram_get_registered_chat_id() == registered_chat
    assert telegram_calls == []


def test_standalone_message_is_warned_and_recorded_with_answer_id(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    update = {
        "message": {
            "message_id": 301,
            "chat": {"id": registered_chat, "type": "private"},
            "from": {"id": registered_chat},
            "text": "Hello",
        }
    }

    with caplog.at_level("WARNING", logger="wtb"):
        wtb.telegram_process_update(update)

    assert len(telegram_calls) == 1
    method, payload = telegram_calls[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == registered_chat
    assert "Standalone messages are not allowed" in payload["text"]
    assert "previously forwarded from WhatsApp" in payload["text"]
    assert "Rejecting standalone Telegram message 301" in caplog.text
    row = app_database.execute(
        "SELECT * FROM telegram_rejected_messages WHERE telegram_message_id = 301"
    ).fetchone()
    assert row["telegram_answer_message_id"] == 701
    assert row["telegram_chat_id"] == registered_chat
    assert row["status"] == "SENT"


def test_replayed_standalone_message_is_not_answered_twice(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    update = {
        "message": {
            "message_id": 302,
            "chat": {"id": registered_chat, "type": "private"},
            "from": {"id": registered_chat},
            "text": "Do not answer this twice",
        }
    }

    wtb.telegram_process_update(update)
    with caplog.at_level("DEBUG", logger="wtb"):
        wtb.telegram_process_update(update)

    assert len(telegram_calls) == 1
    assert "Ignoring replayed standalone Telegram message 302" in caplog.text
    count = app_database.execute(
        "SELECT COUNT(*) AS count FROM telegram_rejected_messages "
        "WHERE telegram_message_id = 302"
    ).fetchone()["count"]
    assert count == 1


@pytest.mark.parametrize("reply_target", [302, 701])
def test_reply_to_rejected_message_or_bot_answer_is_not_reprocessed(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    caplog: pytest.LogCaptureFixture,
    reply_target: int,
) -> None:
    wtb.telegram_process_update(
        {
            "message": {
                "message_id": 302,
                "chat": {"id": registered_chat, "type": "private"},
                "from": {"id": registered_chat},
                "text": "Standalone",
            }
        }
    )
    reply = {
        "message": {
            "message_id": 303,
            "chat": {"id": registered_chat, "type": "private"},
            "from": {"id": registered_chat},
            "reply_to_message": {"message_id": reply_target},
            "text": "This is a reply to the rejection",
        }
    }

    with caplog.at_level("WARNING", logger="wtb"):
        wtb.telegram_process_update(reply)

    assert len(telegram_calls) == 1
    assert "Ignoring Telegram reply 303 to rejected standalone message" in caplog.text


def test_reply_to_forwarded_whatsapp_message_is_not_rejected_as_standalone(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with app_database:
        app_database.execute(
            "INSERT INTO messages "
            "(wa_message_id, wa_chat_jid, telegram_message_id, telegram_chat_id, "
            "direction, status) VALUES ('wa-1', 'chat@s.whatsapp.net', 801, ?, "
            "'WHATSAPP_TO_TELEGRAM', 'SENT')",
            (registered_chat,),
        )

    with caplog.at_level("INFO", logger="wtb"):
        wtb.telegram_process_update(
            {
                "message": {
                    "message_id": 304,
                    "chat": {"id": registered_chat, "type": "private"},
                    "from": {"id": registered_chat},
                    "reply_to_message": {"message_id": 801},
                    "text": "Reply to forwarded WhatsApp message",
                }
            }
        )

    assert telegram_calls == []
    assert "reply forwarding is not implemented yet" in caplog.text
    assert app_database.execute(
        "SELECT COUNT(*) AS count FROM telegram_rejected_messages"
    ).fetchone()["count"] == 0


def test_standalone_message_replay_is_deduplicated_after_database_reopen(
    app_database: Any,
    registered_chat: int,
    telegram_calls: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = {
        "message": {
            "message_id": 304,
            "chat": {"id": registered_chat, "type": "private"},
            "from": {"id": registered_chat},
            "text": "Persist this rejection",
        }
    }
    wtb.telegram_process_update(update)

    reopened_database = wtb.database_initialize()
    monkeypatch.setattr(wtb, "DATABASE", reopened_database)
    try:
        wtb.telegram_process_update(update)
        assert len(telegram_calls) == 1
    finally:
        reopened_database.close()


def test_telegram_polling_persists_next_update_offset(
    app_database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []

    class PollFinished(Exception):
        pass

    def fake_request(
        method: str, payload: dict[str, Any], *, timeout: int = 40
    ) -> dict[str, Any]:
        assert method == "getUpdates"
        requests.append(payload)
        if len(requests) == 1:
            return {"result": [{"update_id": 40, "message": {"text": "first"}}]}
        raise PollFinished

    monkeypatch.setattr(wtb, "telegram_request", fake_request)
    monkeypatch.setattr(wtb, "telegram_process_update", processed.append)

    with pytest.raises(PollFinished):
        wtb.telegram_polling_loop()

    offset = app_database.execute(
        "SELECT value FROM settings WHERE key = 'telegram_update_offset'"
    ).fetchone()["value"]
    assert processed == [{"update_id": 40, "message": {"text": "first"}}]
    assert offset == "41"
    assert requests[0]["timeout"] == 30
    assert requests[1]["offset"] == 41
