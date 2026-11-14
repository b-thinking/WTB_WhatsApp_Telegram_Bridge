"""Minimal WhatsApp-to-Telegram text bridge."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.utils.enum import ClientName

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "bridge.sqlite3"
WHATSAPP_SESSION_PATH = DATA_DIR / "whatsapp.db"
LOGGER = logging.getLogger("wtb")
DATABASE_LOCK: threading.Lock = threading.Lock()
DATABASE: sqlite3.Connection | None = None
CONFIG: dict[str, str] = {}


def configure_logging(level_name: str) -> None:
    """Configure application logging on stderr."""
    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        raise TypeError("LOG_LEVEL must be a valid Python logging level")
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_configuration() -> dict[str, str]:
    """Load and validate the required environment variables."""
    load_dotenv(PROJECT_ROOT / ".env")
    required = ("LOG_LEVEL", "TELEGRAM_BOT_TOKEN", "PHONE_NUMBER")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    config = {name: os.environ[name].strip() for name in required}
    if not re.fullmatch(r"[1-9][0-9]{7,14}", config["PHONE_NUMBER"]):
        raise ValueError("PHONE_NUMBER must contain 8 to 15 digits without a plus sign")
    if not config["TELEGRAM_BOT_TOKEN"]:
        raise ValueError("TELEGRAM_BOT_TOKEN cannot be empty")
    configure_logging(config["LOG_LEVEL"])
    return config


def initialize_database() -> sqlite3.Connection:
    """Create the application database and recover incomplete deliveries."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA journal_mode = WAL")
    database.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            wa_message_id TEXT NOT NULL,
            wa_chat_jid TEXT NOT NULL,
            telegram_message_id INTEGER,
            telegram_chat_id INTEGER,
            direction TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            modified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (wa_chat_jid, wa_message_id)
        );
        """
    )
    with database:
        failed = database.execute(
            "UPDATE messages SET status = 'FAILED', modified_at = CURRENT_TIMESTAMP "
            "WHERE status = 'PROCESSING'"
        ).rowcount
    if failed:
        LOGGER.error(
            "Marked %d interrupted message delivery attempt(s) as FAILED", failed
        )
    return database


def telegram_request(
    method: str, payload: dict[str, Any], *, timeout: int = 40
) -> dict[str, Any]:
    """Call a Telegram Bot API method without exposing the token in errors."""
    url = f"https://api.telegram.org/bot{CONFIG['TELEGRAM_BOT_TOKEN']}/{method}"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        raise RuntimeError(f"Telegram API request failed ({method})") from None
    if not result.get("ok"):
        description = result.get("description", "unknown API error")
        raise RuntimeError(f"Telegram API {method} failed: {description}")
    return result


def get_registered_chat_id() -> int | None:
    """Return the registered Telegram chat ID, if one exists."""
    assert DATABASE is not None
    row = DATABASE.execute(
        "SELECT value FROM settings WHERE key = 'telegram_chat_id'"
    ).fetchone()
    return int(row["value"]) if row else None


def request_contact(chat_id: int) -> None:
    """Ask the private Telegram user to share their own phone number."""
    telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "Please share your phone number to register this bridge.",
            "reply_markup": {
                "keyboard": [[{"text": "Share phone number", "request_contact": True}]],
                "one_time_keyboard": True,
                "resize_keyboard": True,
            },
        },
    )


def normalize_phone_number(number: str) -> str:
    """Return only the digits from a phone number."""
    return "".join(character for character in number if character.isdigit())


def process_telegram_update(update: dict[str, Any]) -> None:
    """Process a registration update; the MVP has no Telegram reply handling."""
    message = update.get("message")
    if not message:
        return
    chat = message.get("chat", {})
    sender = message.get("from", {})
    chat_id = chat.get("id")
    if chat.get("type") != "private" or not chat_id:
        LOGGER.warning("Ignoring Telegram messages outside a private chat")
        return
    if get_registered_chat_id() is not None:
        return

    contact = message.get("contact")
    if not contact:
        request_contact(chat_id)
        return
    if contact.get("user_id") != sender.get("id"):
        telegram_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "Registration rejected: share your own contact.",
            },
        )
        return
    if (
        normalize_phone_number(contact.get("phone_number", ""))
        != CONFIG["PHONE_NUMBER"]
    ):
        telegram_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "Registration rejected: the phone numbers do not match.",
            },
        )
        return

    assert DATABASE is not None
    with DATABASE_LOCK, DATABASE:
        DATABASE.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('telegram_chat_id', ?)",
            (str(chat_id),),
        )
    telegram_request(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "Registration complete. WhatsApp text messages will be forwarded here.",
        },
    )
    LOGGER.info("Telegram chat registration completed")


def telegram_polling_loop() -> None:
    """Poll Telegram updates and persist the confirmed update offset."""
    assert DATABASE is not None
    row = DATABASE.execute(
        "SELECT value FROM settings WHERE key = 'telegram_update_offset'"
    ).fetchone()
    offset = int(row["value"]) if row else None
    while True:
        payload: dict[str, Any] = {"timeout": 30, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        updates = telegram_request("getUpdates", payload, timeout=40)["result"]
        for update in updates:
            process_telegram_update(update)
            offset = update["update_id"] + 1
            with DATABASE_LOCK, DATABASE:
                DATABASE.execute(
                    "INSERT OR REPLACE INTO settings (key, value) "
                    "VALUES ('telegram_update_offset', ?)",
                    (str(offset),),
                )


def message_text(message: Any) -> str:
    """Extract text from the text-only MVP message types."""
    text = getattr(message, "conversation", "") or ""
    if not text and getattr(message, "extendedTextMessage", None):
        text = message.extendedTextMessage.text
    return text.strip()


def split_html_text(text: str, max_length: int) -> list[str]:
    """Split escaped text without breaking HTML entities."""
    chunks: list[str] = []
    current = ""
    for character in text:
        escaped_character = html.escape(character)
        if current and len(current) + len(escaped_character) > max_length:
            chunks.append(current)
            current = ""
        current += escaped_character
    if current or not chunks:
        chunks.append(current)
    return chunks


def format_jid(jid: Any) -> str:
    """Format a Neonize JID as a stable string."""
    user = getattr(jid, "User", "")
    server = getattr(jid, "Server", "")
    if user and server:
        return f"{user}@{server}"
    return str(jid)

#region: Neonize event handlers
def handle_whatsapp_message(client: NewClient, event: MessageEv) -> None:
    """Persist and forward one incoming WhatsApp text message."""
    try:
        source = event.Info.MessageSource
        if getattr(source, "IsFromMe", False):
            LOGGER.debug("Ignoring a WhatsApp message sent by this client")
            return
        chat_jid = source.Chat
        if getattr(chat_jid, "Server", "") in {"g.us", "broadcast"}:
            LOGGER.debug("Ignoring group or broadcast WhatsApp message")
            return
        text = message_text(event.Message)
        if not text:
            LOGGER.debug("Ignoring a WhatsApp message without text content")
            return

        telegram_chat_id = get_registered_chat_id()
        if telegram_chat_id is None:
            LOGGER.warning("Received WhatsApp text before Telegram registration")
            return
        wa_message_id = str(event.Info.ID)
        wa_chat_jid = format_jid(chat_jid)
        assert DATABASE is not None
        with DATABASE_LOCK, DATABASE:
            cursor = DATABASE.execute(
                "INSERT OR IGNORE INTO messages "
                "(wa_message_id, wa_chat_jid, telegram_chat_id, direction, status) "
                "VALUES (?, ?, ?, 'WHATSAPP_TO_TELEGRAM', 'PROCESSING')",
                (wa_message_id, wa_chat_jid, telegram_chat_id),
            )
        if cursor.rowcount == 0:
            LOGGER.debug("Ignoring duplicate WhatsApp message %s", wa_message_id)
            return

        sender = getattr(event.Info, "PushName", "") or getattr(source, "Sender", None)
        sender_jid = getattr(sender, "User", "") if sender else ""
        sender_name = str(sender) if sender and not sender_jid else sender_jid
        if not sender_name:
            sender_name = getattr(chat_jid, "User", "unknown sender")
        header = f"<b>Sender:</b> {html.escape(sender_name)}\n"
        original_id = f"\n\nWhatsApp message ID: {html.escape(wa_message_id)}"
        prefix = header
        max_body_length = 4096 - len(prefix) - len(original_id)
        text_chunks = split_html_text(text, max_body_length)
        first_message_id: int | None = None
        for index, chunk in enumerate(text_chunks):
            body = prefix + chunk
            if index == len(text_chunks) - 1:
                body += original_id
            result = telegram_request(
                "sendMessage",
                {"chat_id": telegram_chat_id, "text": body, "parse_mode": "HTML"},
            )["result"]
            if first_message_id is None:
                first_message_id = result["message_id"]
        with DATABASE_LOCK, DATABASE:
            DATABASE.execute(
                "UPDATE messages SET telegram_message_id = ?, status = 'SENT', "
                "modified_at = CURRENT_TIMESTAMP WHERE wa_chat_jid = ? AND wa_message_id = ?",
                (first_message_id, wa_chat_jid, wa_message_id),
            )
        LOGGER.info("Forwarded WhatsApp message %s", wa_message_id)
    except Exception:
        LOGGER.exception("Failed to process an incoming WhatsApp message")
        if "wa_message_id" in locals() and "wa_chat_jid" in locals() and DATABASE:
            with DATABASE_LOCK, DATABASE:
                DATABASE.execute(
                    "UPDATE messages SET status = 'FAILED', modified_at = CURRENT_TIMESTAMP "
                    "WHERE wa_chat_jid = ? AND wa_message_id = ?",
                    (wa_chat_jid, wa_message_id),
                )



#endregion: Neonize event handlers
def main() -> int:
    """Start Telegram polling and the persistent Neonize session."""
    global CONFIG, DATABASE

    def on_qr(client: NewClient, qr_png: bytes) -> None:
        LOGGER.info("WhatsApp connection initialized (QR received)")
        qr_ready.set()

    def on_paired(client: NewClient, event: PairStatusEv) -> None:
        LOGGER.info("WhatsApp device successfully paired")
        paired.set()

    def on_connected(client: NewClient, event: ConnectedEv) -> None:
        LOGGER.info("Connected to WhatsApp")
        connected.set()
        qr_ready.set()

    try:
        CONFIG = load_configuration()
    except ValueError as error:
        logging.basicConfig(stream=sys.stderr, level=logging.ERROR)
        LOGGER.error("Configuration error: %s", error)
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE = initialize_database()

    # Create threading events
    qr_ready: threading.Event = threading.Event()
    connected: threading.Event = threading.Event()
    paired: threading.Event = threading.Event()

    client: NewClient = NewClient(str(WHATSAPP_SESSION_PATH))

    client.qr(on_qr) # Set the event handler for QR generation
    client.event(PairStatusEv)(on_paired) # Set the event handler for pairing
    client.event(ConnectedEv)(on_connected) # Set the event handler for connection

    client.event(MessageEv)(handle_whatsapp_message)

    # Start WhatsApp connection in a separate thread.
    LOGGER.info("Starting WhatsApp connection...")

    whatsapp_thread = threading.Thread(
        target=client.connect, name="whatsapp-client", daemon=True
    )
    whatsapp_thread.start()

    # Wait until the initial QR handshake is ready.
    if not qr_ready.wait(timeout=30):
        LOGGER.error("WhatsApp QR initialization timed out")
        raise TimeoutError("WhatsApp QR initialization timed out")

    if not client.is_logged_in:
        try:
            pairing_code = client.PairPhone(
                CONFIG["PHONE_NUMBER"], 
                show_push_notification=True,
                client_name=ClientName.LINUX
            )
        except Exception:
            LOGGER.exception("WhatsApp phone pairing could not be started")
            DATABASE.close()
            return 2
        LOGGER.warning(
            "WhatsApp registration is pending. Pairing code: %s", pairing_code
        )


    try:
        LOGGER.info("Starting Telegram polling...")
        telegram_polling_loop()
    except Exception:
        LOGGER.exception("Telegram connection or polling failed")
        return 2
    finally:
        try:
            LOGGER.info("Stoping WhatsApp client...")
            client.stop()
        except Exception:
            LOGGER.exception("Failed to stop the WhatsApp client cleanly")
        DATABASE.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
