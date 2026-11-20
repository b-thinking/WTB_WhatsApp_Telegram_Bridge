"""Minimal WhatsApp-to-Telegram text and media bridge."""

from __future__ import annotations

import html
import json
import logging
import mimetypes
import os
import queue
import re
import signal
import sqlite3
import sys
import threading
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.utils.enum import ClientName
from neonize.utils.jid import JID

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "bridge.sqlite3"
WHATSAPP_SESSION_PATH = DATA_DIR / "whatsapp.db"
LOGGER = logging.getLogger("wtb")
DATABASE_LOCK: threading.Lock = threading.Lock()
DATABASE: sqlite3.Connection | None = None
CONFIG: dict[str, str] = {}
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_PHOTO_SIZE_LIMIT = 10_000_000
TELEGRAM_VIDEO_SIZE_LIMIT = 50_000_000
TELEGRAM_AUDIO_SIZE_LIMIT = 50_000_000
WHATSAPP_GROUP_NAME_CACHE: dict[str, str | None] = {}
WHATSAPP_GROUP_NAME_CACHE_LOCK = threading.Lock()


def logging_configure(level_name: str) -> None:
    """Configure application logging on stderr."""
    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        raise ValueError(  # noqa: TRY004 - The setting is an invalid value, not a wrong type.
            "LOG_LEVEL must be a valid Python logging level"
        )
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )

    # Configure logging for the Neonize library to avoid excessive debug output.
    neonize_logger = logging.getLogger("neonize")
    neonize_logger.setLevel(logging.WARNING)
    whatsmeos_logger = logging.getLogger("whatsmeow")
    whatsmeos_logger.setLevel(logging.WARNING)


def handle_sigterm(_signum: int, _frame: Any) -> None:
    """Route container termination through the normal cleanup path."""
    LOGGER.info("Received SIGTERM; requesting graceful shutdown")
    raise KeyboardInterrupt


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
    logging_configure(config["LOG_LEVEL"])
    return config


def database_initialize() -> sqlite3.Connection:
    """Create the application database and recover incomplete deliveries."""

    LOGGER.debug(f"Initializing application database: {DATABASE_PATH}")
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
            wa_sender_jid TEXT,
            wa_sender_alt_jid TEXT,
            telegram_message_id INTEGER,
            telegram_chat_id INTEGER,
            direction TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            modified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (wa_chat_jid, wa_message_id)
        );
        CREATE TABLE IF NOT EXISTS telegram_rejected_messages (
            telegram_message_id INTEGER PRIMARY KEY,
            telegram_answer_message_id INTEGER UNIQUE,
            telegram_chat_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            modified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS telegram_replies (
            telegram_message_id INTEGER PRIMARY KEY,
            telegram_chat_id INTEGER NOT NULL,
            replied_to_message_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            modified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Compatibility with older database versions that did not store the WhatsApp sender JIDs.
    message_columns = {
        row["name"] for row in database.execute("PRAGMA table_info(messages)")
    }
    if "whatsapp_event" in message_columns:
        database.execute("PRAGMA secure_delete = ON")
        with database:
            database.execute("UPDATE messages SET whatsapp_event = NULL")
        database.execute("ALTER TABLE messages DROP COLUMN whatsapp_event")
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        database.execute("VACUUM")
        LOGGER.info("Removed stored WhatsApp message events from the database")
        message_columns.remove("whatsapp_event")
    if "wa_sender_jid" not in message_columns:
        database.execute("ALTER TABLE messages ADD COLUMN wa_sender_jid TEXT")
    if "wa_sender_alt_jid" not in message_columns:
        database.execute("ALTER TABLE messages ADD COLUMN wa_sender_alt_jid TEXT")
    with database:
        failed = database.execute(
            "UPDATE messages SET status = 'FAILED', modified_at = CURRENT_TIMESTAMP "
            "WHERE status = 'PROCESSING'"
        ).rowcount
        rejected_messages = database.execute(
            "UPDATE telegram_rejected_messages "
            "SET status = 'FAILED', modified_at = CURRENT_TIMESTAMP "
            "WHERE status = 'PROCESSING'"
        ).rowcount
        interrupted_replies = database.execute(
            "UPDATE telegram_replies SET status = 'FAILED', "
            "modified_at = CURRENT_TIMESTAMP WHERE status = 'PROCESSING'"
        ).rowcount
    if failed:
        LOGGER.error(
            "Marked %d interrupted message delivery attempt(s) as FAILED", failed
        )
    if rejected_messages:
        LOGGER.error(
            "Marked %d interrupted Telegram rejection response(s) as FAILED",
            rejected_messages,
        )
    if interrupted_replies:
        LOGGER.error(
            "Marked %d interrupted Telegram reply attempt(s) as FAILED",
            interrupted_replies,
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
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        raise _telegram_transport_error(method, error) from None
    if not result.get("ok"):
        description = result.get("description", "unknown API error")
        raise RuntimeError(f"Telegram API {method} failed: {description}")
    return result


def _telegram_transport_error(method: str, error: Exception) -> RuntimeError:
    """Describe a Telegram request failure without exposing the bot token."""
    if isinstance(error, HTTPError):
        detail = f"HTTP {error.code}"
    elif isinstance(error, URLError):
        detail = str(error.reason)
    elif isinstance(error, TimeoutError):
        detail = "timeout"
    elif isinstance(error, json.JSONDecodeError):
        detail = "invalid JSON response"
    else:
        detail = type(error).__name__

    token = CONFIG.get("TELEGRAM_BOT_TOKEN", "")
    if token:
        detail = detail.replace(token, "[redacted]")
    detail = " ".join(detail.split())[:160] or type(error).__name__
    return RuntimeError(f"Telegram API request failed ({method}): {detail}")


def telegram_send_media(
    method: str,
    media_field: str,
    chat_id: int,
    caption: str,
    media_data: bytes,
    mime_type: str,
    filename_stem: str,
) -> dict[str, Any]:
    """Upload a media file to Telegram using multipart form data."""
    boundary = f"wtb-{uuid.uuid4().hex}"
    extension = mimetypes.guess_extension(mime_type, strict=False) or ".bin"
    filename = f"{filename_stem}{extension}"
    fields = {
        "chat_id": str(chat_id),
        "caption": caption,
        "parse_mode": "HTML",
    }
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    parts.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{media_field}"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            f"Content-Type: {mime_type}\r\n\r\n".encode(),
            media_data,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = Request(
        f"https://api.telegram.org/bot{CONFIG['TELEGRAM_BOT_TOKEN']}/{method}",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=40) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        raise _telegram_transport_error(method, error) from None
    if not result.get("ok"):
        description = result.get("description", "unknown error")
        raise RuntimeError(f"Telegram API {method} failed: {description}")
    return result


def telegram_send_photo(
    chat_id: int, caption: str, image_data: bytes, mime_type: str
) -> dict[str, Any]:
    """Upload a photo to Telegram."""
    return telegram_send_media(
        "sendPhoto", "photo", chat_id, caption, image_data, mime_type, "whatsapp-image"
    )


def telegram_send_video(
    chat_id: int, caption: str, video_data: bytes, mime_type: str
) -> dict[str, Any]:
    """Upload a video to Telegram."""
    return telegram_send_media(
        "sendVideo", "video", chat_id, caption, video_data, mime_type, "whatsapp-video"
    )


def telegram_send_animation(
    chat_id: int, caption: str, video_data: bytes, mime_type: str
) -> dict[str, Any]:
    """Upload a video with GIF playback to Telegram as an animation."""
    return telegram_send_media(
        "sendAnimation",
        "animation",
        chat_id,
        caption,
        video_data,
        mime_type,
        "whatsapp-animation",
    )


def telegram_send_voice(
    chat_id: int, caption: str, media_data: bytes, mime_type: str
) -> dict[str, Any]:
    """Upload a WhatsApp PTT voice note to Telegram as a voice message."""
    return telegram_send_media(
        "sendVoice", "voice", chat_id, caption, media_data, mime_type, "whatsapp-voice"
    )


def telegram_send_audio(
    chat_id: int, caption: str, media_data: bytes, mime_type: str
) -> dict[str, Any]:
    """Upload regular WhatsApp audio to Telegram as an audio file."""
    return telegram_send_media(
        "sendAudio", "audio", chat_id, caption, media_data, mime_type, "whatsapp-audio"
    )


def telegram_get_registered_chat_id() -> int | None:
    """Return the registered Telegram chat ID, if one exists."""
    assert DATABASE is not None
    row = DATABASE.execute(
        "SELECT value FROM settings WHERE key = 'telegram_chat_id'"
    ).fetchone()
    return int(row["value"]) if row else None


def telegram_request_contact(chat_id: int) -> None:
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


def telegram_process_update(
    update: dict[str, Any], client: NewClient | None = None
) -> None:
    """Process Telegram registration and messages from the registered chat."""
    message = update.get("message")
    if not message:
        return
    chat = message.get("chat", {})
    sender = message.get("from", {})
    chat_id = chat.get("id")
    if chat.get("type") != "private" or not chat_id:
        LOGGER.warning("Ignoring Telegram messages outside a private chat")
        return
    registered_chat_id = telegram_get_registered_chat_id()
    if registered_chat_id is not None:
        if chat_id != registered_chat_id:
            LOGGER.warning("Ignoring Telegram message from an unregistered chat")
            return
        telegram_process_registered_message(message, registered_chat_id, client)
        return

    contact = message.get("contact")
    if not contact:
        telegram_request_contact(chat_id)
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
            "text": "Registration complete. WhatsApp messages will be forwarded here.",
        },
    )
    LOGGER.info("Telegram chat registration completed")


def telegram_rejected_message_exists(chat_id: int, message_id: int) -> bool:
    """Return whether an ID belongs to a rejected message or its bot response."""
    assert DATABASE is not None
    row = DATABASE.execute(
        "SELECT 1 FROM telegram_rejected_messages "
        "WHERE telegram_chat_id = ? "
        "AND (telegram_message_id = ? OR telegram_answer_message_id = ?)",
        (chat_id, message_id, message_id),
    ).fetchone()
    return row is not None


def telegram_message_was_forwarded(chat_id: int, message_id: int) -> bool:
    """Return whether a Telegram message ID maps to a delivered WhatsApp message."""
    assert DATABASE is not None
    row = DATABASE.execute(
        "SELECT 1 FROM messages WHERE telegram_chat_id = ? "
        "AND telegram_message_id = ? AND status = 'SENT'",
        (chat_id, message_id),
    ).fetchone()
    return row is not None


def telegram_download_file(file_id: str) -> bytes:
    """Download a file sent to the bot from Telegram."""
    result = telegram_request("getFile", {"file_id": file_id})["result"]
    file_path = result.get("file_path")
    if not file_path:
        raise RuntimeError("Telegram getFile response did not include a file path")
    url = (
        f"https://api.telegram.org/file/bot{CONFIG['TELEGRAM_BOT_TOKEN']}/"
        f"{file_path}"
    )
    try:
        with urlopen(Request(url, method="GET"), timeout=40) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise RuntimeError(
            f"Telegram file download failed ({type(error).__name__})"
        ) from None


def telegram_forward_reply(
    message: dict[str, Any], chat_id: int, client: NewClient | None
) -> None:
    """Forward an accepted Telegram reply to its original WhatsApp chat."""
    message_id = message.get("message_id")
    reply = message["reply_to_message"]
    replied_to_id = reply.get("message_id")
    assert DATABASE is not None
    with DATABASE_LOCK, DATABASE:
        mapping = DATABASE.execute(
            "SELECT wa_message_id, wa_chat_jid, wa_sender_jid, wa_sender_alt_jid "
            "FROM messages "
            "WHERE telegram_chat_id = ? AND telegram_message_id = ? "
            "AND status = 'SENT'",
            (chat_id, replied_to_id),
        ).fetchone()
        if mapping is None:
            LOGGER.warning(
                "Ignoring Telegram reply %s because message %s was not forwarded from WhatsApp",
                message_id,
                replied_to_id,
            )
            return
        cursor = DATABASE.execute(
            "INSERT OR IGNORE INTO telegram_replies "
            "(telegram_message_id, telegram_chat_id, replied_to_message_id, status) "
            "VALUES (?, ?, ?, 'PROCESSING')",
            (message_id, chat_id, replied_to_id),
        )
        if cursor.rowcount == 0:
            LOGGER.debug("Ignoring replayed Telegram reply %s", message_id)
            return

    try:
        quoted = whatsapp_build_quote_event(
            mapping["wa_message_id"],
            mapping["wa_chat_jid"],
            mapping["wa_sender_jid"],
            mapping["wa_sender_alt_jid"],
        )
        LOGGER.debug(
            "Prepared content-free WhatsApp quote for Telegram reply %s: "
            "original_message_id=%s message_fields=%s recipient_jid_fields=%s "
            "sender_alt_available=%s",
            message_id,
            mapping["wa_message_id"],
            whatsapp_present_message_fields(quoted.Message),
            whatsapp_present_message_fields(quoted.Info.MessageSource.Chat),
            bool(mapping["wa_sender_alt_jid"]),
        )
        if client is None:
            raise RuntimeError("WhatsApp client is not available")
        recipient = quoted.Info.MessageSource.Chat
        caption = message.get("caption", "")
        if "photo" in message:
            photos = message["photo"]
            if not photos:
                raise ValueError("Telegram photo reply has no photo sizes")
            largest_photo = max(
                photos,
                key=lambda photo: (
                    photo.get("width", 0) * photo.get("height", 0),
                    photo.get("file_size", 0),
                ),
            )
            media = telegram_download_file(largest_photo["file_id"])
            client.send_image(recipient, media, caption=caption or None, quoted=quoted)
            content_type = "photo"
        elif "video" in message:
            media = telegram_download_file(message["video"]["file_id"])
            client.send_video(recipient, media, caption=caption or None, quoted=quoted)
            content_type = "video"
        elif "animation" in message:
            media = telegram_download_file(message["animation"]["file_id"])
            client.send_video(
                recipient,
                media,
                caption=caption or None,
                quoted=quoted,
                gifplayback=True,
                is_gif=True,
            )
            content_type = "animation"
        elif message.get("text"):
            client.reply_message(message["text"], quoted=quoted, to=recipient)
            content_type = "text"
        else:
            LOGGER.warning("Ignoring unsupported Telegram reply %s", message_id)
            with DATABASE_LOCK, DATABASE:
                DATABASE.execute(
                    "UPDATE telegram_replies SET status = 'FAILED', "
                    "modified_at = CURRENT_TIMESTAMP WHERE telegram_message_id = ?",
                    (message_id,),
                )
            return
    except Exception:
        with DATABASE_LOCK, DATABASE:
            DATABASE.execute(
                "UPDATE telegram_replies SET status = 'FAILED', "
                "modified_at = CURRENT_TIMESTAMP WHERE telegram_message_id = ?",
                (message_id,),
            )
        raise
    with DATABASE_LOCK, DATABASE:
        DATABASE.execute(
            "UPDATE telegram_replies SET status = 'SENT', "
            "modified_at = CURRENT_TIMESTAMP WHERE telegram_message_id = ?",
            (message_id,),
        )
    LOGGER.info(
        "Forwarded Telegram %s reply %s to WhatsApp chat %s quoting message %s",
        content_type,
        message_id,
        mapping["wa_chat_jid"],
        replied_to_id,
    )


def telegram_process_registered_message(
    message: dict[str, Any], chat_id: int, client: NewClient | None = None
) -> None:
    """Reject standalone messages and recognize replies to prior bridge messages."""
    message_id = message.get("message_id")
    reply = message.get("reply_to_message")
    if reply is not None:
        replied_to_id = reply.get("message_id")
        if replied_to_id is None:
            LOGGER.warning(
                "Ignoring Telegram reply %s without a replied-to message ID", message_id
            )
            return
        if telegram_rejected_message_exists(chat_id, replied_to_id):
            LOGGER.warning(
                "Ignoring Telegram reply %s to rejected standalone message %s",
                message_id,
                replied_to_id,
            )
        elif telegram_message_was_forwarded(chat_id, replied_to_id):
            telegram_forward_reply(message, chat_id, client)
        else:
            LOGGER.warning(
                "Ignoring Telegram reply %s because message %s was not forwarded "
                "from WhatsApp",
                message_id,
                replied_to_id,
            )
        return

    if message_id is None:
        LOGGER.warning("Ignoring a standalone Telegram message without a message ID")
        return

    assert DATABASE is not None
    with DATABASE_LOCK, DATABASE:
        cursor = DATABASE.execute(
            "INSERT OR IGNORE INTO telegram_rejected_messages "
            "(telegram_message_id, telegram_chat_id, status) "
            "VALUES (?, ?, 'PROCESSING')",
            (message_id, chat_id),
        )
    if cursor.rowcount == 0:
        LOGGER.debug("Ignoring replayed standalone Telegram message %s", message_id)
        return

    LOGGER.warning(
        "Rejecting standalone Telegram message %s; only replies to previously "
        "forwarded WhatsApp messages are accepted",
        message_id,
    )
    try:
        result = telegram_request(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": (
                    "Standalone messages are not allowed. Only replies to messages "
                    "previously forwarded from WhatsApp are accepted."
                ),
            },
        )["result"]
        answer_message_id = result["message_id"]
    except Exception:
        with DATABASE_LOCK, DATABASE:
            DATABASE.execute(
                "UPDATE telegram_rejected_messages "
                "SET status = 'FAILED', modified_at = CURRENT_TIMESTAMP "
                "WHERE telegram_message_id = ?",
                (message_id,),
            )
        raise

    with DATABASE_LOCK, DATABASE:
        DATABASE.execute(
            "UPDATE telegram_rejected_messages "
            "SET telegram_answer_message_id = ?, status = 'SENT', "
            "modified_at = CURRENT_TIMESTAMP WHERE telegram_message_id = ?",
            (answer_message_id, message_id),
        )


def telegram_polling_loop(client: NewClient | None = None) -> None:
    """Poll Telegram updates and persist the confirmed update offset."""
    assert DATABASE is not None
    row = DATABASE.execute(
        "SELECT value FROM settings WHERE key = 'telegram_update_offset'"
    ).fetchone()
    offset = int(row["value"]) if row else None
    LOGGER.info(f"Started Telegram polling loop. Message offset: {offset if offset is not None else 'None'}")
    while True:
        payload: dict[str, Any] = {"timeout": 30, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        updates = telegram_request("getUpdates", payload, timeout=40)["result"]
        for update in updates:
            telegram_process_update(update, client)
            offset = update["update_id"] + 1
            with DATABASE_LOCK, DATABASE:
                DATABASE.execute(
                    "INSERT OR REPLACE INTO settings (key, value) "
                    "VALUES ('telegram_update_offset', ?)",
                    (str(offset),),
                )


def whatsapp_message_text(message: Any) -> str:
    """Extract text from the text-only MVP message types."""
    text = getattr(message, "conversation", "") or ""
    if not text and getattr(message, "extendedTextMessage", None):
        text = message.extendedTextMessage.text
    return text.strip()


def whatsapp_get_message_field(message: Any, field_name: str) -> Any | None:
    """Return a protobuf submessage only when the field is present."""
    has_field = getattr(message, "HasField", None)
    if callable(has_field):
        try:
            if not has_field(field_name):
                return None
        except (TypeError, ValueError):
            pass
    return getattr(message, field_name, None)


def whatsapp_present_message_fields(message: Any) -> list[str]:
    """Return names of populated protobuf message fields without their contents."""
    list_fields = getattr(message, "ListFields", None)
    if not callable(list_fields):
        return []
    get_fields = cast(Callable[[], Iterable[tuple[Any, Any]]], list_fields)
    return [field.name for field, _value in get_fields()]


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


def whatsapp_format_jid(jid: Any) -> str:
    """Format a Neonize JID as a stable string."""
    if jid is None:
        return ""
    if getattr(jid, "IsEmpty", False):
        return ""
    user = getattr(jid, "User", "")
    server = getattr(jid, "Server", "")
    if user and server:
        return f"{user}@{server}"
    return str(jid)


def whatsapp_parse_jid(jid_string: str) -> JID:
    """Build a Neonize JID from the stored user@server identifier."""
    user, separator, server = jid_string.partition("@")
    if not separator or not user or not server:
        raise ValueError(f"Invalid WhatsApp JID: {jid_string!r}")
    return JID(
        User=user,
        RawAgent=0,
        Device=0,
        Integrator=0,
        Server=server,
    )


def whatsapp_build_quote_event(
    message_id: str,
    chat_jid: str,
    sender_jid: str | None,
    sender_alt_jid: str | None,
) -> MessageEv:
    """Build quote metadata using identifiers only, without original content."""
    event = MessageEv()
    event.Info.ID = message_id
    # Neonize raises IndexError when get_message_type() sees no populated field.
    # Mark this as empty text so the quote remains content-free but has a type.
    event.Message.conversation = ""
    source = event.Info.MessageSource
    source.Chat.CopyFrom(whatsapp_parse_jid(chat_jid))
    source.Sender.CopyFrom(whatsapp_parse_jid(sender_jid or chat_jid))
    if sender_alt_jid:
        source.SenderAlt.CopyFrom(whatsapp_parse_jid(sender_alt_jid))
    return event


def whatsapp_get_group_name(client: NewClient, group_jid: Any) -> str | None:
    """Look up and cache a group's name for diagnostic logging."""
    jid = whatsapp_format_jid(group_jid)
    with WHATSAPP_GROUP_NAME_CACHE_LOCK:
        if jid in WHATSAPP_GROUP_NAME_CACHE:
            return WHATSAPP_GROUP_NAME_CACHE[jid]

    try:
        group_info = client.get_group_info(group_jid)
        group_name = getattr(group_info, "GroupName", "").strip() or None
    except Exception as error:  # noqa: BLE001 - Diagnostic lookup must not interrupt message handling.
        LOGGER.debug(
            "Could not look up WhatsApp group name for %s (%s)",
            jid,
            type(error).__name__,
        )
        group_name = None

    with WHATSAPP_GROUP_NAME_CACHE_LOCK:
        WHATSAPP_GROUP_NAME_CACHE[jid] = group_name
    return group_name

#region: Neonize event handlers
def on_whatsapp_message(client: NewClient, event: MessageEv) -> None:
    """Persist and forward one incoming WhatsApp text or media message."""
    try:
        source = event.Info.MessageSource
        if getattr(source, "IsFromMe", False):
            LOGGER.debug("Ignoring a WhatsApp message sent by this client")
            return
        chat_jid = source.Chat
        chat_server = getattr(chat_jid, "Server", "")
        broadcast_owner = getattr(source, "BroadcastListOwner", None)
        is_broadcast = chat_server == "broadcast" or bool(
            getattr(broadcast_owner, "User", "")
        )
        is_group = not is_broadcast and (
            chat_server == "g.us" or getattr(source, "IsGroup", False)
        )
        if is_group or is_broadcast:
            conversation_type = "broadcast" if is_broadcast else "group"
            group_name = (
                whatsapp_get_group_name(client, chat_jid) if is_group else None
            )
            sender_jid = getattr(source, "Sender", None)
            sender_alt_jid = getattr(source, "SenderAlt", None)
            LOGGER.debug(
                "Ignoring WhatsApp %s message: message_id=%r chat_jid=%s "
                "group_name=%r chat_server=%r source_is_group=%s "
                "sender_jid=%s sender_alt_jid=%s pushname=%r "
                "broadcast_list_owner=%s whatsapp_type=%r media_type=%r "
                "populated_fields=%s",
                conversation_type,
                getattr(event.Info, "ID", ""),
                whatsapp_format_jid(chat_jid),
                group_name,
                chat_server,
                bool(getattr(source, "IsGroup", False)),
                whatsapp_format_jid(sender_jid) if sender_jid else "",
                whatsapp_format_jid(sender_alt_jid) if sender_alt_jid else "",
                getattr(event.Info, "Pushname", ""),
                whatsapp_format_jid(broadcast_owner) if broadcast_owner else "",
                getattr(event.Info, "Type", ""),
                getattr(event.Info, "MediaType", ""),
                whatsapp_present_message_fields(event.Message),
            )
            return
        image_message = whatsapp_get_message_field(event.Message, "imageMessage")
        video_message = whatsapp_get_message_field(event.Message, "videoMessage")
        audio_message = whatsapp_get_message_field(event.Message, "audioMessage")
        media_message = image_message or video_message or audio_message
        if image_message is not None:
            media_kind = "image"
            telegram_media_sender = telegram_send_photo
            media_size_limit = TELEGRAM_PHOTO_SIZE_LIMIT
            default_mime_type = "image/jpeg"
        elif video_message is not None:
            media_kind = "video"
            is_animation = bool(getattr(video_message, "gifPlayback", False))
            telegram_media_sender = (
                telegram_send_animation if is_animation else telegram_send_video
            )
            media_size_limit = TELEGRAM_VIDEO_SIZE_LIMIT
            default_mime_type = "video/mp4"
        elif audio_message is not None:
            is_voice_note = bool(getattr(audio_message, "ptt", False))
            media_kind = "voice note" if is_voice_note else "audio"
            telegram_media_sender = (
                telegram_send_voice if is_voice_note else telegram_send_audio
            )
            media_size_limit = TELEGRAM_AUDIO_SIZE_LIMIT
            default_mime_type = "audio/ogg" if is_voice_note else "audio/mpeg"
        else:
            media_kind = ""
            telegram_media_sender = None
            media_size_limit = 0
            default_mime_type = ""
        text = whatsapp_message_text(event.Message)
        if media_message is not None and not text:
            text = getattr(media_message, "caption", "") or ""
        if not text and media_message is None:
            LOGGER.debug(
                "Ignoring WhatsApp message without supported content: "
                "message_id=%r whatsapp_type=%r media_type=%r populated_fields=%s",
                getattr(event.Info, "ID", ""),
                getattr(event.Info, "Type", ""),
                getattr(event.Info, "MediaType", ""),
                whatsapp_present_message_fields(event.Message),
            )
            return

        telegram_chat_id = telegram_get_registered_chat_id()
        if telegram_chat_id is None:
            LOGGER.warning("Received WhatsApp message before Telegram registration")
            return
        wa_message_id = str(event.Info.ID)
        wa_chat_jid = whatsapp_format_jid(chat_jid)
        wa_sender_jid = whatsapp_format_jid(getattr(source, "Sender", None))
        wa_sender_alt_jid = whatsapp_format_jid(getattr(source, "SenderAlt", None))
        assert DATABASE is not None
        with DATABASE_LOCK, DATABASE:
            cursor = DATABASE.execute(
                "INSERT OR IGNORE INTO messages "
                "(wa_message_id, wa_chat_jid, wa_sender_jid, wa_sender_alt_jid, "
                "telegram_chat_id, direction, status) "
                "VALUES (?, ?, ?, ?, ?, 'WHATSAPP_TO_TELEGRAM', 'PROCESSING')",
                (
                    wa_message_id,
                    wa_chat_jid,
                    wa_sender_jid,
                    wa_sender_alt_jid,
                    telegram_chat_id,
                ),
            )
        if cursor.rowcount == 0:
            LOGGER.debug("Ignoring duplicate WhatsApp message %s", wa_message_id)
            return

        push_name = getattr(event.Info, "Pushname", "").strip()
        sender_jid = getattr(source, "Sender", None)
        sender_alt_jid = getattr(source, "SenderAlt", None)
        sender_user = getattr(sender_jid, "User", "")
        sender_server = getattr(sender_jid, "Server", "")
        sender_alt_user = getattr(sender_alt_jid, "User", "")
        sender_alt_server = getattr(sender_alt_jid, "Server", "")
        LOGGER.debug(
            "WhatsApp sender metadata for message %s: pushname_available=%s, "
            "sender_server=%r, sender_alt_available=%s, sender_alt_server=%r",
            wa_message_id,
            bool(push_name),
            sender_server,
            bool(sender_alt_user and sender_alt_server),
            sender_alt_server,
        )

        if push_name:
            sender_name = push_name
            sender_name_source = "pushname"
        elif sender_server == "lid" and sender_alt_user:
            sender_name = sender_alt_user
            sender_name_source = "sender_alt"
        elif sender_user:
            sender_name = sender_user
            sender_name_source = "sender_jid"
        else:
            sender_name = getattr(chat_jid, "User", "unknown sender")
            sender_name_source = "chat_jid_fallback"
            LOGGER.debug(
                "No WhatsApp pushname or sender JID was available for message %s; "
                "using chat JID fallback (server=%r)",
                wa_message_id,
                getattr(chat_jid, "Server", ""),
            )

        LOGGER.debug(
            "Selected WhatsApp sender from %s for message %s",
            sender_name_source,
            wa_message_id,
        )
        header = f"<b>Sender:</b> {html.escape(sender_name)}\n"
        original_id = f"\n\nWhatsApp message ID: {html.escape(wa_message_id)}"
        first_message_id: int | None = None
        if media_message is not None:
            media_data = client.download_any(event.Message)
            if not media_data:
                raise RuntimeError(f"WhatsApp {media_kind} download returned no data")
            mime_type = getattr(media_message, "mimetype", "") or default_mime_type

            if len(media_data) > media_size_limit:
                LOGGER.warning(
                    "%s in WhatsApp message %s exceeds Telegram's upload limit",
                    media_kind.capitalize(),
                    wa_message_id,
                )
                warning = "\n\nAttachment too large to forward"
                max_body_length = (
                    TELEGRAM_TEXT_LIMIT - len(header) - len(original_id) - len(warning)
                )
                text_chunks = split_html_text(text, max_body_length)
                for index, chunk in enumerate(text_chunks):
                    body = header + chunk
                    if index == len(text_chunks) - 1:
                        body += original_id + warning
                    result = telegram_request(
                        "sendMessage",
                        {
                            "chat_id": telegram_chat_id,
                            "text": body,
                            "parse_mode": "HTML",
                        },
                    )["result"]
                    if first_message_id is None:
                        first_message_id = result["message_id"]
            else:
                caption = header + html.escape(text) + original_id
                overflow_chunks: list[str] | None = None
                if len(caption) <= TELEGRAM_CAPTION_LIMIT:
                    photo_caption = caption
                else:
                    max_body_length = TELEGRAM_TEXT_LIMIT - len(header) - len(original_id)
                    overflow_chunks = split_html_text(text, max_body_length)
                    photo_caption = header.rstrip()

                assert telegram_media_sender is not None
                result = telegram_media_sender(
                    telegram_chat_id, photo_caption, media_data, mime_type
                )["result"]
                first_message_id = result["message_id"]
                if overflow_chunks is not None:
                    for index, chunk in enumerate(overflow_chunks):
                        body = header + chunk
                        if index == len(overflow_chunks) - 1:
                            body += original_id
                        telegram_request(
                            "sendMessage",
                            {
                                "chat_id": telegram_chat_id,
                                "text": body,
                                "parse_mode": "HTML",
                            },
                        )
        else:
            max_body_length = TELEGRAM_TEXT_LIMIT - len(header) - len(original_id)
            text_chunks = split_html_text(text, max_body_length)
            for index, chunk in enumerate(text_chunks):
                body = header + chunk
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
    DATABASE = None
    client: NewClient | None = None
    connection_errors: queue.Queue[tuple[Exception, Any]]

    def on_whatsapp_qr(client: NewClient, qr_png: bytes) -> None:
        LOGGER.info("WhatsApp connection initialized (QR received)")
        whatsapp_event.set()

    def on_whatsapp_paired(client: NewClient, event: PairStatusEv) -> None:
        LOGGER.info("WhatsApp device successfully paired")
        whatsapp_event.set()

    def on_whatsapp_connected(client: NewClient, event: ConnectedEv) -> None:
        LOGGER.info("Connected to WhatsApp")
        whatsapp_event.set()

    def connect_whatsapp() -> None:
        try:
            assert client is not None
            client.connect()
        except Exception as error:  # noqa: BLE001 - Relay thread errors to main.
            connection_errors.put((error, error.__traceback__))
            whatsapp_event.set()

    connection_errors = queue.Queue(maxsize=1)

    try:
        CONFIG = load_configuration()
    except ValueError as error:
        logging.basicConfig(stream=sys.stderr, level=logging.ERROR)
        LOGGER.error("Configuration error: %s", error)
        return 1

    signal.signal(signal.SIGTERM, handle_sigterm)

    whatsapp_started = False

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        DATABASE = database_initialize()

        # WhatsApp event is signaled when relevant lifecycle events occur.
        # This allows the main thread to wait for the WhatsApp client to be ready or fail.
        whatsapp_event: threading.Event = threading.Event()

        client = NewClient(str(WHATSAPP_SESSION_PATH))

        client.qr(on_whatsapp_qr)  # Set the event handler for QR generation.
        client.event(PairStatusEv)(on_whatsapp_paired)  # Set the event handler for pairing.
        client.event(ConnectedEv)(on_whatsapp_connected)  # Set the event handler for connection.

        client.event(MessageEv)(on_whatsapp_message)

        # Start WhatsApp connection in a separate thread.
        LOGGER.info("Starting WhatsApp connection...")

        whatsapp_thread = threading.Thread(
            target=connect_whatsapp, name="whatsapp-client", daemon=True
        )
        whatsapp_started = True
        whatsapp_thread.start()

        # Wait until Neonize has initialized the client or failed to connect.
        if not whatsapp_event.wait(timeout=30):
            LOGGER.error("WhatsApp client initialization timed out")
            return 2

        # Check if there was a connection error during the WhatsApp client initialization.
        try:
            connection_error, traceback = connection_errors.get_nowait()
        except queue.Empty:
            connection_error = None

        if connection_error is not None:
            LOGGER.error(
                "WhatsApp connection failed",
                exc_info=(type(connection_error), connection_error, traceback),
            )
            return 3

        # If the client is connected but not logged in, pairing is required.
        # Generate a pairing code and log it.
        if not client.is_logged_in:
            whatsapp_event.clear()  # Clear the event before requesting pairing code.
            pairing_code = client.PairPhone(
                CONFIG["PHONE_NUMBER"],
                show_push_notification=True,
                client_name=ClientName.LINUX,
            )
            LOGGER.warning(
                "WhatsApp registration is pending. Pairing code: %s", pairing_code
            )
            if not whatsapp_event.wait(timeout=300):
                LOGGER.error("WhatsApp pairing timed out")
                return 4

        LOGGER.info("Starting Telegram polling...")
        telegram_polling_loop(client)
    except KeyboardInterrupt:
        LOGGER.info("Shutdown requested")
        return 0
    except Exception:
        LOGGER.exception("WhatsApp startup, pairing, or Telegram polling failed")
        return 5
    finally:
        if client is not None and whatsapp_started:
            try:
                LOGGER.info("Stopping WhatsApp client...")
                client.stop()
            except Exception:
                LOGGER.exception("Failed to stop the WhatsApp client cleanly")
        if DATABASE is not None:
            DATABASE.close()
            DATABASE = None
    return 0


if __name__ == "__main__":
    sys.exit(main())
