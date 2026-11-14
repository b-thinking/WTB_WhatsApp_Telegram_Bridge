# WhatsApp–Telegram Bridge

The initial version links one WhatsApp account to one Telegram private chat and
forwards incoming WhatsApp text messages to that chat. It uses Neonize for
WhatsApp and Telegram's Bot API over the Python standard library's `urllib`.

This version covers the first development phase only. Attachments and replies
from Telegram to WhatsApp are not implemented yet.

## Requirements

- Python 3.11
- A Telegram bot token from [BotFather](https://core.telegram.org/bots#botfather)
- A WhatsApp account that can link a device using a phone number

The application dependencies are pinned in `requirements.txt`. Neonize
`0.4.3.post0` publishes a Python-independent wheel and supports the Python 3.11
runtime used by this project. `python-dotenv` loads local development settings
from `.env`.

## Prerequisites

### Telegram Bot

Before first use, create or select a Telegram bot. NOTICE: The bot can be used in a private chat, but its username can still be found in Telegram. The registration flow is what restricts the bridge to the matching phone number.

For creating the bot:

- Open [BotFather](https://core.telegram.org/bots#botfather) as contact in Telegram.
- Send `/newbot` to create a new bot.
  - Use "WhatsApp" as bot displayname
  - Type your bot name. The username must be available, 5–32 characters, case-insensitive, use Latin letters, digits, or underscores, and end in "bot".
  - Notice and annotate the API key. Add it to your environment as `TELEGRAM_BOT_TOKEN`. Keep the BotFather token private.

### WhatsApp account and App

Also, initial use requires a WhatsApp account with a phone number active. For registration, you need a mobile phone with the WhatsApp app installed. WhatsApp says linked devices can log out after 14 days of primary-phone inactivity, so be sure to check the WhatsApp app in the phone every 14 days. See [WhatsApp’s linked devices guide](https://faq.whatsapp.com/378279804439436/?cms_platform=android&helpref=hc_fnav)

## Configuration

Set these variables in the process environment or in a local `.env` file at the
project root:

```dotenv
LOG_LEVEL=INFO
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
PHONE_NUMBER=34123456789
```

`PHONE_NUMBER` must contain the WhatsApp account's international phone number
as digits only, without a leading `+`. The application exits with status 1 if a
required value is missing or invalid.

## Install and run

The current version uses pyenv as virtual environment. The environment is configured using script in `scripts/configure-pyenv.sh`. For using it:

- You need pyenv and pyenv-virtualenv installed
- Declare `PYTHON_VERSION` and `PYENV_ENV_NAME` in `.env`. Be sure that version is 3.11+.

NOTICE: This is approach is not mandatory. You can also use `venv` as virtual env or even none (used in container)


```sh
# Create and validate environment
source ./scripts/configure-pyenv.sh
python src/wtb.py
```

On first startup, the pairing code is written to `stderr` at warning level.
Enter it in WhatsApp under **Settings → Linked Devices → Link a Device → Link
with phone number instead**. The Neonize session is saved under `data/` and
reused on later starts.



Open a private chat with the Telegram bot and send any message. The bot asks you
to share your phone number with Telegram's contact button. Registration
succeeds only if the shared contact belongs to the sending Telegram user and
its phone number matches `PHONE_NUMBER`. The associated Telegram chat ID is
saved in the application database.

Incoming WhatsApp text is sent with the sender name (or phone number) and the
original WhatsApp message ID. Long text is split into Telegram messages. The
application ignores messages sent by its own WhatsApp account, group and
broadcast messages, and messages without text.

## Persistence and logs

All persistent state is kept in `data/`:

- `whatsapp.db` is Neonize's WhatsApp session store.
- `bridge.sqlite3` stores the Telegram association, Telegram polling offset,
  and WhatsApp message delivery records.

The SQLite database prevents already recorded WhatsApp messages from being
forwarded again. If the process restarts while a message is marked
`PROCESSING`, the record is changed to `FAILED` and logged; automatic retries
are not attempted. Delivery across WhatsApp, Telegram, and SQLite cannot be
made exactly once.

Logs are written to `stderr`. They do not include the Telegram token. Exit
status 2 indicates a WhatsApp pairing or Telegram connection/polling failure.
