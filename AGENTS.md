# Project guidance

## Project structure

- `src/wtb.py` contains the WhatsApp–Telegram bridge, including configuration,
  SQLite persistence, Telegram Bot API polling and replies, and WhatsApp event
  handling. Keep bridge behavior here unless a change clearly benefits from a
  separate module.
- `src/sample_pair_phone.py` is a standalone Neonize pairing example, not the
  production bridge entry point.
- `tests/unit/` contains pytest unit tests. `conftest.py` provides shared
  fixtures; the tests mock external services and use temporary databases.
- `tests/MANUAL_INTEGRATION_TEST.md` describes checks that require linked
  WhatsApp and Telegram accounts.
- `container/` contains the container image and systemd unit. `scripts/` holds
  environment setup, publishing, and deployment helpers.
- `prompts/` contains paired English and Spanish project prompts.
- `data/` is runtime state (WhatsApp session and bridge SQLite database); it is
  ignored by Git and must be treated as persistent user data.

## Development

- Use Python 3.11. Runtime dependencies are pinned in `requirements.txt`;
  development tools are in `requirements-dev.txt`.
- Local settings are loaded from a root `.env` file. Never commit or expose
  real bot tokens or other credentials.
- The documented unit-test command is `python -m pytest tests/unit`.
- Follow the existing Python style and naming in `src/wtb.py` and the unit
  tests. Keep tests isolated from live WhatsApp, Telegram, and network services.

## Behavior and data constraints

- Preserve the registration check that validates both the Telegram sender and
  shared phone number before associating a private chat.
- Preserve message deduplication and recovery semantics in the SQLite store;
  delivery across WhatsApp, Telegram, and SQLite is not exactly once.
- Do not persist message content in the bridge database. Logs should not expose
  message contents or bot tokens; JIDs, sender names, and group names can also
  contain sensitive information.
- Avoid modifying or deleting files in `data/` when working on source code or
  tests. Tests should use temporary paths/databases.
- Treat publishing and deployment scripts as operational actions: inspect their
  effects before changing or running them.
