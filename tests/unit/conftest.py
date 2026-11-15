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
    database = wtb.initialize_database()
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
    ) -> Any:
        sender = SimpleNamespace(User=sender_user, Server=sender_server)
        sender_alt = SimpleNamespace(
            User=sender_alt_user, Server=sender_alt_server
        )
        chat = SimpleNamespace(User=chat_user, Server=chat_server)
        source = SimpleNamespace(
            IsFromMe=is_from_me,
            Chat=chat,
            Sender=sender,
            SenderAlt=sender_alt,
        )
        info = SimpleNamespace(
            ID=message_id,
            Pushname=pushname,
            MessageSource=source,
        )
        message = SimpleNamespace(
            conversation=text,
            extendedTextMessage=SimpleNamespace(text=""),
        )
        return SimpleNamespace(Info=info, Message=message)

    return build_event
