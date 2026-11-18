# WhatsApp–Telegram Bridge

I want to build a bidirectional bridge between a WhatsApp account and a Telegram chat.

The bridge will be implemented as a Telegram bot written in Python, with the following requirements.

## Functional requirements

- The bot must connect to a single WhatsApp account (associated with a phone number), using the functionality provided by the [Neonize](https://github.com/krypton-byte/neonize) library.
- The bot must monitor all incoming messages and capture both their text and any attached content (images, files, videos, etc.).
- Upon receiving a message, the bot must forward it, including both text and any attached content, to the Telegram user with the same phone number as the WhatsApp account. To do so, during the registration process the bot will identify and persist the Telegram chat ID for the conversation between the bot and the user.
- The forwarded message must identify the sender using the following format: `Sender: <sender>`. The sender must be the contact's name, if available. Otherwise, the phone number must be used.
- If the message contains an attachment, the bot must forward the attachment to the Telegram user, adding the following text as a header: `File from: <sender>`.
- The forwarded message must include the ID of the original WhatsApp message.
- The bot must monitor replies sent by the user to messages forwarded by the bot.
- If the Telegram user replies to a message sent by the bot, the bot must forward that reply to the WhatsApp user. To do so, it must identify the original message and reply to it.

- The code must include the following safeguards:
  - Prevent infinite loops (the bot must not capture as an incoming WhatsApp message a message that it has sent itself).
  - Respect the public Telegram API limits regarding attachment sizes. If an attachment cannot be sent, the message must be sent without the attachment, with an additional text indicating: `Attachment too large to forward`.
  - The application must be protected against restarts so that messages already forwarded are not processed again when the script or service restarts.

## Technical requirements

### General code and project rules

- The bot must be written in Python, using the [Neonize](https://github.com/krypton-byte/neonize) library.
- For accessing the Telegram API, using direct HTTP requests with `urllib` is initially suggested, but alternative approaches recommended by the agent may also be considered and reviewed.
- The canonical Python version is 3.11.
- Any additional requirements needed for `requirements.txt` must be identified. Each dependency must be checked for compatibility with the selected Python version before being added to that file.
- Code style must follow PEP 8. Any generated python code has to be sintactically validated using ruff python linter before committing or testing.
- Names of code artifacts (functions, variables, classes, etc.) must be in English.
- Code comments must be in English.
- Project files (README.md, CLAUDE.md, AGENTS.md, etc.) must be written in English, including this file.
- The code will run continuously as a service. It must not require user interaction, either through the console or a graphical interface.
- The code must be as simple and minimal as possible. Do not add code or functionality that has not been requested. It is preferable to add parameter validation rather than attempt to support every atypical usage scenario. This does not apply to comments, which should be written with future maintenance in mind, for both AI agents and human developers.

### Directory structure

The project must have the following directory structure:

```text
WTB_WhatsApp_Telegram_Bridge/
│
├── src/
│   └── wtb.py
│
├── scripts/
│
├── tests/
│   ├── unit/
│   └── integration/
│
├── data/
│
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── Dockerfile
└── README.md
```

### Logging

- The code must use Python's `logging` library. It must not contain `print()` or `console.log()`. The following log levels are defined:
  - **INFO:** Forwarded messages, replies, and any other general information.
  - **WARNING:** Other warning conditions. Specifically, this level must include a message indicating that WhatsApp registration is pending (including the 8-digit pairing code). It must also include any message that has been truncated or whose attachments could not be forwarded because of their size.
  - **ERROR:** Any error produced by the bot. In this case, the complete error traceback must be included.
  - **DEBUG:** Development or debugging information.
- Log messages must be written to `stderr` using a logging handler.
- Logs must never include the Telegram token, WhatsApp credentials, or sensitive authentication information.
- The pairing code must only be shown in the logs while registration is pending.

### Configuration

- All application parameters must be read from environment variables. During development, an environment file (`.env`) must be loaded. In production, the variables must be provided by the production environment. At startup, the application must validate that the required variables are defined. If any are missing, it must terminate with an error message and exit code 1.
- The initially identified parameters are the logging level `LOG_LEVEL`, the token generated for the bot `TELEGRAM_BOT_TOKEN`, and the phone number `PHONE_NUMBER` in WhatsApp format (`CCXXXXXXXXX`, where `CC` is the international dialing code without the `+` sign). Any other required parameters will be added in the future.

### Persistence

- All persistent application data must be stored under `data/`.
- Neonize authentication and session data must be persisted using the storage mechanism provided by the library.
- Application state, Telegram registration, and message-processing records must be stored in a separate SQLite database managed by the application.
- The message data currently identified as requiring persistence is:

| Field | Purpose |
|---|---|
| Record ID | Internal identifier |
| WhatsApp message ID | Identify the original message |
| WhatsApp chat JID | Identify the source conversation |
| Telegram message ID | Identify the forwarded message |
| Telegram chat ID | Identify the destination |
| Direction | Identify the message direction |
| Status | Track message processing |
| Creation date | Track the lifetime of the record |
| Modification date | Track the lifetime of the record |

- Both storage locations must survive container recreation.

### WhatsApp registration

- Registration with Neonize must be idempotent. In other words, if the bot has not yet been registered at startup, registration must be performed using `PairPhone`, displaying the pairing code in the console (`stderr`). If the bot has already been registered, it must reuse the existing registration data.

### Telegram registration (chat identification)

- The user must initiate a private conversation with the bot.
- The bot will request the user's phone number using a Telegram contact-sharing button (`request_contact=True`).
- The bot must verify that the shared contact belongs to the Telegram user who sent the message.
- The phone number will be normalized and compared with `PHONE_NUMBER`.
- If both numbers match, the bot will persist the Telegram `chat_id` in SQLite.
- If they do not match, the association will be rejected.
- Only one Telegram account can be associated with the configured WhatsApp account.
- Once registered, the bot will reuse the persisted `chat_id`.

### Error handling

- **Connection errors**
  - If a connection error occurs (the bot cannot connect to WhatsApp or Telegram), the bot must write an error message to the log and terminate with a specific error status.
  - The system may subsequently restart the bot manually. On restart, the bot must resynchronize using the information persisted in the state and message databases.

- **Message persistence**

  Message persistence is intended to address two critical requirements:
  - Ensure that messages already forwarded are not processed again after a restart.
  - Make it possible to identify replies to forwarded messages and reply to the corresponding original message.

  The proposed sequence is:
  - Register the incoming WhatsApp message in SQLite with the status `PROCESSING`.
  - Forward the message to Telegram.
  - Register the Telegram message identifiers.

- Message processing must use a persistent state machine.
  - Each incoming WhatsApp message must be registered in SQLite before attempting delivery to Telegram.
  - The application must persist the Telegram `message_id` returned after a successful delivery.
  - Successfully delivered messages must never be intentionally processed again.
  - Messages left in an incomplete state after an unexpected restart must be handled according to a recovery policy.
  - The implementation must minimize duplicate deliveries.
  - Exactly-once delivery across WhatsApp, Telegram, and SQLite cannot be guaranteed.
  - Suggested message status codes: `PENDING`, `PROCESSING`, `SENT`, and `FAILED`.

- On restart, any message found in the `PROCESSING` state must generate an error message in the log and be marked as `FAILED`.

### Content conversion

The content conversions and APIs to be used are detailed in the following table:

| Content type | WhatsApp → Telegram | Telegram reply → WhatsApp |
|---|---|---|
| **Text** | HTML `<b>Sender:</b> text`, split into multiple messages if it exceeds `4096` characters | `text` |
| **Photos** | `wa_client.download_any` → `sendPhoto` (caption overflow if over `1024` characters) | `photo[-1]` → `wa_client.send_image` |
| **Videos / GIFs** | `sendVideo` / `sendAnimation`, depending on `gifPlayback` | `wa_client.send_video` (`gifplayback=True` for animations) |
| **Documents** | `sendDocument`, preserving `fileName` and `mimetype` | `wa_client.send_document`, preserving the name and MIME type |
| **Voice notes / Audio** | `sendVoice` (if `PTT=True`) or `sendAudio` | Preceding label + `wa_client.send_audio(ptt=True/False)` |
| **Stickers** | `.webp` + sender information + `sendSticker` | Static/video → `send_sticker`; `.tgs` → thumbnail or label |
| **Location / Contacts** | Google Maps link / `[Contact: name]` | Google Maps link / `[Contact]` label |
| **Replies** | `contextInfo.stanzaID` → `reply_parameters` in Telegram | `build_quoted_wa_event` (`SimpleNamespace` compatible with proto2) |

- When a WhatsApp message contains an attachment and text that exceeds the caption limit, the entire text must be sent in a separate message, afterwards followed by the attachment.

### Tests

Unit tests must not require actual connections to WhatsApp or Telegram.

The APIs must therefore be mockable.

The test cases will include:

- Duplicate messages.
- Restart during processing.
- Messages without text content.
- Sending errors.
- Replies to non-existent messages.
- Attachments that exceed the size limits.
- Messages that are not replies.

## Work plan

### Functional phases

Development will be carried out in the following functional stages:

1. Minimum viable product: WhatsApp account registration and forwarding text messages.
2. Extend the application to forward messages with attachments.
3. Extend the application to capture replies and forward replies to WhatsApp.

### Construction phases for the minimum viable product

Development will begin with the minimum viable product, following the stages described below.

1. **Initial phase**
   1. Python code in `wtb.py`, `requirements.txt`, and `README.md`.
   2. Unit tests for the initial phase. Pytest scripts must be placed in the `tests` directory.
   3. SQLite database in the `data` directory, with its initial schema.

2. **Deployment of the initial phase**
   1. Create a `Dockerfile` for container deployment.
   2. Identify the most suitable image for deploying the initial phase.
   3. Confirm that root privileges are not required.
   4. The persistent `data` directory must be mounted as an external volume to ensure persistence.
   5. The code must handle `SIGTERM` and `SIGINT` signals so that it can shut down cleanly when container termination is requested, closing connections and exiting without corrupting the storage.

3. **Code normalization**

   Split the functionality in the initial `wtb.py` script into separate modules, using the following structure:

   ```text
   ├── src/
   │   └── bridge/
   │       ├── __init__.py
   │       ├── main.py
   │       ├── config.py
   │       ├── logging_config.py
   │       ├── whatsapp.py
   │       ├── telegram.py
   │       ├── message_handler.py
   │       └── storage.py
   ```

For each phase to be considered complete, the acceptance criteria are:

- The code must work.
- All tests must pass.
- The documentation must be up to date.
- No functionality belonging to later phases may be left partially implemented.
- The functionality implemented in previous phases must not be broken.

### Pending decisions

- Handling WhatsApp messages with multiple attachments.
- Handling Telegram replies with multiple attachments (albums).

### Out of scope

- Handling messages sent to the bot that are not replies to previously received messages. Initially, these will be ignored, with a warning logged to indicate that the message will not be processed.
- Group conversations. This bridge will only support individual WhatsApp accounts and the chat between the bot and the user on Telegram.
- Messages manually sent by the WhatsApp user. These will be ignored and, where applicable, logged at `DEBUG` level.
- WhatsApp status messages are ignored.
- Edited WhatsApp messages (no update will be sent).
- Edited Telegram replies (no update will be sent).
- Deleted WhatsApp messages (no update will be sent or an update will be sent).
