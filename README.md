# WhatsApp–Telegram Bridge

WhatsApp-Telegram Bridge connects a WhatsApp account to one registered Telegram
private chat. It forwards incoming WhatsApp text, images, videos, and GIF-style
animations to Telegram. Replies in Telegram can be sent to the original WhatsApp
chat as text, photos, videos, or animations. The bridge uses Neonize for WhatsApp
and Telegram's Bot API through Python's standard-library `urllib`.

## Quick start

### Requirements

#### Telegram Bot

Before first use, create or select a Telegram bot. Anyone who knows the bot's
username can start a chat with it. The registration flow restricts bridge use to
the Telegram account that shares the matching phone number.

To create a bot:

- Search for and open [BotFather](https://core.telegram.org/bots#botfather) in Telegram.
- Send `/newbot` to create a new bot.
  - Choose a display name, such as `WhatsApp`.
  - Choose an available username that meets Telegram's requirements and ends in `bot`.
  - Copy the API token into your environment as `TELEGRAM_BOT_TOKEN`. Keep it private.

#### WhatsApp account and app

You need an active WhatsApp account and a mobile phone with the WhatsApp app
installed to link the bridge. WhatsApp may log out linked devices after 14 days
of primary-phone inactivity. See [WhatsApp's linked devices guide](https://faq.whatsapp.com/378279804439436/?cms_platform=android&helpref=hc_fnav).

### Run the container

To run the packaged application, use a container runtime such as
[Docker Desktop](https://www.docker.com/products/docker-desktop/) or
[Podman](https://podman.io/).

#### Create a local folder

The application stores persistent data in this folder, including the WhatsApp
pairing session and SQLite database.

```sh
mkdir -p "$HOME/.wtb/data"
```

#### Run the application

```sh
podman run --rm --name "wtb-bot" \
    -e LOG_LEVEL="INFO" \
    -e TELEGRAM_BOT_TOKEN="<YOUR-TELEGRAM-BOT-TOKEN>" \
    -e PHONE_NUMBER="34123456789" \
    -v "$HOME/.wtb/data:/app/data" \
    ghcr.io/b-thinking/wtb-bot:latest
```

This example uses Podman. To use Docker, replace `podman` with `docker`.

Replace the example token and phone number with your own values. Use the
international phone number with 8–15 digits and no leading `+`.

On Podman systems using SELinux, the volume may need the `:Z` label option. In a
rootless user-namespace setup, `:U` may also be needed to map ownership to the
container user. For example, use
`-v "$HOME/.wtb/data:/app/data:Z,U"`. The `:U` option changes ownership of the
host directory and its contents.

#### First startup

- The pairing code appears in the console. In WhatsApp, enter it under **Settings → Linked Devices → Link a Device → Link with phone number instead**.
- Open a private chat with the bot and send a message. The bot asks you to share your phone number; tap the contact-sharing button.

## Development setup

- Python 3.11
- `pyenv` and `pyenv-virtualenv` to use the provided environment script
- FFmpeg and libmagic for local video and image handling (the container image includes these)
- A Telegram bot token from [BotFather](https://core.telegram.org/bots#botfather). See [Quick start - Requirements](#telegram-bot)
- A WhatsApp account that can link a device using a phone number. See [Quick start - Requirements](#whatsapp-account-and-app)

Runtime dependencies are pinned in `requirements.txt`. `python-dotenv` loads
local development settings from `.env`.

Additional development dependencies are listed in `requirements-dev.txt`,
including `pytest` and `ruff`.

### Configuration

Set these variables in the process environment or in a local `.env` file at the
project root. Keep real bot tokens private; `.env` is ignored by Git.

```dotenv
# Application settings
LOG_LEVEL=INFO
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
PHONE_NUMBER=34123456789

# Used by scripts/configure-pyenv.sh
PYTHON_VERSION=3.11
PYENV_ENV_NAME=wtb
```

`PHONE_NUMBER` must contain the WhatsApp account's international phone number
as digits only, without a leading `+`. The application exits with status 1 if a
required value is missing or invalid.

### Create an environment and run the Python script locally

The provided setup script uses `pyenv-virtualenv` to create and activate the
project environment, then installs the runtime and development dependencies.
To use it:

- Install `pyenv` and `pyenv-virtualenv`.
- Set `PYTHON_VERSION` (use `3.11`) and `PYENV_ENV_NAME` in `.env`.

This setup is optional. You can use Python's built-in `venv` instead. The
container image runs Python directly without a virtual environment.

```sh
# Create/activate the environment and install dependencies
source ./scripts/configure-pyenv.sh
python src/wtb.py
```

On first startup, the pairing code is written to `stderr` at warning level.
Enter it in WhatsApp under **Settings → Linked Devices → Link a Device → Link
with phone number instead**. The Neonize session is saved under `data/` and
reused on later starts.

Open a private chat with the Telegram bot and send any message. The bot asks you
to share your phone number using the contact-sharing button. Registration
succeeds only if the shared contact belongs to the sending Telegram user and
its phone number matches `PHONE_NUMBER`. The associated Telegram chat ID is
saved in the application database.

Incoming WhatsApp text, images, videos, and GIF-style animations are forwarded
with sender information and the original WhatsApp message ID. Long text is split
into Telegram messages; image text that exceeds Telegram's caption limit follows
the image in separate messages. Replies in Telegram to forwarded WhatsApp
messages are supported for text, photos, videos, and animations. Standalone
Telegram messages are rejected; replies with unsupported types are ignored. The
bridge ignores messages sent by its own WhatsApp account, messages from groups
and broadcasts, and unsupported WhatsApp message types.

### Persistence and logs

All persistent state is kept in `data/`:

- `whatsapp.db` is Neonize's WhatsApp session store.
- `bridge.sqlite3` stores the Telegram association, Telegram polling offset,
  message mappings, and delivery records. It stores message and sender IDs, not
  message content. SQLite may also create `bridge.sqlite3-wal` and
  `bridge.sqlite3-shm` sidecar files while the application is running.

The bridge database prevents already recorded WhatsApp messages from being
forwarded again. If the process restarts while a message is marked
`PROCESSING`, the record is changed to `FAILED` and logged; automatic retries
are not attempted. Delivery across WhatsApp, Telegram, and SQLite cannot be
made exactly once.

Logs are written to `stderr`. Application logs do not include bot tokens or
message contents, but can include message IDs, WhatsApp JIDs (which may contain
phone numbers), sender names, and group names. Protect logs as potentially
sensitive data.

### Exit codes

0. Normal shutdown after Ctrl+C or SIGTERM.
1. Missing or invalid configuration.
2. WhatsApp client initialization timeout.
3. WhatsApp connection error during startup.
4. WhatsApp pairing timeout.
5. Other startup, Telegram API, or runtime error; check the logs.

### Unit tests

Run the unit tests from the project root with the configured Python environment:

```sh
python -m pytest tests/unit
```

The tests mock WhatsApp and Telegram APIs and do not require live accounts or
network connections. They cover configuration validation, Telegram registration,
polling and replies, SQLite recovery and deduplication, and WhatsApp text and
media forwarding.
