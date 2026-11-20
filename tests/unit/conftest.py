"""Shared fixtures for unit tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src import wtb


@pytest.fixture
def app_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Use a temporary application database for each test."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(wtb, "DATA_DIR", data_dir)
    monkeypatch.setattr(wtb, "DATABASE_PATH", data_dir / "bridge.sqlite3")
    database = wtb.database_initialize()
    monkeypatch.setattr(wtb, "DATABASE", database)
    monkeypatch.setattr(
        wtb,
        "CONFIG",
        {
            "LOG_LEVEL": "DEBUG",
            "TELEGRAM_BOT_TOKEN": "unit-test-token",
            "PHONE_NUMBER": "34123456789",
        },
    )
    yield database
    database.close()


@pytest.fixture
def registered_chat(app_database: Any) -> int:
    """Return a database with a Telegram destination already registered."""
    chat_id = 123456
    with app_database:
        app_database.execute(
            "INSERT INTO settings (key, value) VALUES ('telegram_chat_id', ?)",
            (str(chat_id),),
        )
    return chat_id


@pytest.fixture
def telegram_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Replace Telegram HTTP calls and record their method and payload."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_request(
        method: str, payload: dict[str, Any], *, timeout: int = 40
    ) -> dict[str, Any]:
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 700 + len(calls)}}

    monkeypatch.setattr(wtb, "telegram_request", fake_request)
    return calls


@pytest.fixture
def telegram_operations(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Record Telegram text and media requests in their send order."""
    operations: list[tuple[str, Any]] = []

    def fake_request(
        method: str, payload: dict[str, Any], *, timeout: int = 40
    ) -> dict[str, Any]:
        operations.append((method, payload))
        return {"ok": True, "result": {"message_id": 700 + len(operations)}}

    def fake_send_photo(
        chat_id: int, caption: str, image_data: bytes, mime_type: str
    ) -> dict[str, Any]:
        operations.append(
            ("sendPhoto", (chat_id, caption, image_data, mime_type))
        )
        return {"ok": True, "result": {"message_id": 700 + len(operations)}}

    def fake_send_video(
        chat_id: int, caption: str, video_data: bytes, mime_type: str
    ) -> dict[str, Any]:
        operations.append(
            ("sendVideo", (chat_id, caption, video_data, mime_type))
        )
        return {"ok": True, "result": {"message_id": 700 + len(operations)}}

    def fake_send_animation(
        chat_id: int, caption: str, video_data: bytes, mime_type: str
    ) -> dict[str, Any]:
        operations.append(
            ("sendAnimation", (chat_id, caption, video_data, mime_type))
        )
        return {"ok": True, "result": {"message_id": 700 + len(operations)}}

    def fake_send_voice(
        chat_id: int, caption: str, media_data: bytes, mime_type: str
    ) -> dict[str, Any]:
        operations.append(("sendVoice", (chat_id, caption, media_data, mime_type)))
        return {"ok": True, "result": {"message_id": 700 + len(operations)}}

    def fake_send_audio(
        chat_id: int, caption: str, media_data: bytes, mime_type: str
    ) -> dict[str, Any]:
        operations.append(("sendAudio", (chat_id, caption, media_data, mime_type)))
        return {"ok": True, "result": {"message_id": 700 + len(operations)}}

    monkeypatch.setattr(wtb, "telegram_request", fake_request)
    monkeypatch.setattr(wtb, "telegram_send_photo", fake_send_photo)
    monkeypatch.setattr(wtb, "telegram_send_video", fake_send_video)
    monkeypatch.setattr(wtb, "telegram_send_animation", fake_send_animation)
    monkeypatch.setattr(wtb, "telegram_send_voice", fake_send_voice)
    monkeypatch.setattr(wtb, "telegram_send_audio", fake_send_audio)
    return operations


@pytest.fixture
def whatsapp_event_factory():
    """Build lightweight Neonize-like message events for handler tests."""

    def build_event(
        *,
        message_id: str = "wa-message-1",
        text: str = "hello",
        pushname: str = "",
        sender_user: str = "34111111111",
        sender_server: str = "s.whatsapp.net",
        sender_alt_user: str = "",
        sender_alt_server: str = "",
        chat_user: str = "34111111111",
        chat_server: str = "s.whatsapp.net",
        is_from_me: bool = False,
        is_group: bool | None = None,
        image_caption: str | None = None,
        image_mime_type: str = "image/jpeg",
        video_caption: str | None = None,
        video_mime_type: str = "video/mp4",
        gif_playback: bool = False,
        audio_caption: str | None = None,
        audio_mime_type: str = "audio/ogg; codecs=opus",
        audio_ptt: bool = False,
    ) -> Any:
        sender = SimpleNamespace(User=sender_user, Server=sender_server)
        sender_alt = SimpleNamespace(
            User=sender_alt_user, Server=sender_alt_server
        )
        chat = SimpleNamespace(User=chat_user, Server=chat_server)
        source = SimpleNamespace(
            IsFromMe=is_from_me,
            IsGroup=chat_server == "g.us" if is_group is None else is_group,
            Chat=chat,
            Sender=sender,
            SenderAlt=sender_alt,
        )
        info = SimpleNamespace(
            ID=message_id,
            Pushname=pushname,
            MessageSource=source,
        )
        image_message = (
            SimpleNamespace(caption=image_caption, mimetype=image_mime_type)
            if image_caption is not None
            else None
        )
        video_message = (
            SimpleNamespace(
                caption=video_caption,
                mimetype=video_mime_type,
                gifPlayback=gif_playback,
            )
            if video_caption is not None
            else None
        )
        audio_message = (
            SimpleNamespace(
                caption=audio_caption, mimetype=audio_mime_type, ptt=audio_ptt
            )
            if audio_caption is not None
            else None
        )
        message = SimpleNamespace(
            conversation=text,
            extendedTextMessage=SimpleNamespace(text=""),
            imageMessage=image_message,
            videoMessage=video_message,
            audioMessage=audio_message,
        )
        return SimpleNamespace(Info=info, Message=message)

    return build_event
