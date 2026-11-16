# Manual Integration Tests

This checklist is for end-to-end tests with the running WhatsApp and Telegram
accounts. Record the date, build/image tag, test result, and relevant log or
message IDs for each run. Do not include bot tokens or authentication data in
test evidence.

**Current implementation:** incoming WhatsApp text forwarding and Telegram
registration are available. Attachment forwarding and Telegram-to-WhatsApp
replies are not implemented yet; those cases are listed for later phases.

## Setup, registration, and persistence

- [x] **I-01 — First WhatsApp pairing** *(only if testing with a clean session)*: Start without a saved Neonize session. Confirm an 8-character pairing code is logged at WARNING, pair the phone, and confirm the service connects.
- [x] **I-02 — Reuse WhatsApp session**: Restart with the existing `data/whatsapp.db`. Confirm the client reconnects without requesting a new pairing code.
- [x] **I-03 — Request Telegram contact**: Start a private conversation with the bot and send a text message. Confirm it asks for contact sharing with a button that shares the user's own contact.
- [x] **I-04 — Accept matching Telegram contact**: Share the Telegram user's own contact with a phone number matching `PHONE_NUMBER`, including a `+`, spaces, or punctuation. Confirm registration succeeds and the chat ID is persisted.
- [ ] **I-05 — Reject another person's contact**: Share a contact whose Telegram `user_id` differs from the sender. Confirm registration is rejected and no chat ID is stored.
- [ ] **I-06 — Reject phone mismatch**: Share the sender's own contact with a different phone number. Confirm registration is rejected and no chat ID is stored.
- [ ] **I-07 — Keep the single registered account**: After registration, attempt registration from another Telegram account. Confirm the persisted chat association is not replaced.
- [ ] **I-08 — Persist app state across container recreation**: Stop and recreate the container with the same host volume. Confirm the Telegram association, update offset, message records, and WhatsApp session remain available.

## WhatsApp text to Telegram — current implementation

- [x] **I-09 — Simple text message**: Send a short text from another WhatsApp account to the linked account. Confirm one Telegram message arrives with `Sender: <name>` and the original WhatsApp message ID. **Passed in the first smoke test.**
- [x] **I-10 — Text with HTML-special characters**: Send text containing `<`, `>`, and `&`. Confirm the Telegram message displays those characters literally and the sender header remains formatted correctly.
- [x] **I-11 — Text near Telegram's message limit**: Send a message close to 4096 characters. Confirm all text and the original message ID are delivered without a Telegram length error.
- [x] **I-12 — Text longer than 4096 characters**: Send a message longer than 4096 characters. Confirm it is split into multiple Telegram messages, all parts arrive in order, and the original WhatsApp message ID is included.
- [x] **I-13 — Unicode long text**: Send a long message containing accented characters, emoji, and multi-code-point emoji. Confirm the message is split without losing or corrupting characters.
- [x] **I-14 — Sender Pushname available**: Send a message from a WhatsApp profile with a visible Pushname. Confirm that name appears after `Sender:`.
- [ ] **I-15 — Sender Pushname unavailable**: Send from an account that supplies no Pushname. Confirm the sender falls back to a phone-number JID when available.
- [ ] **I-16 — LID sender with alternate phone JID**: Send from an account whose primary sender JID uses LID addressing. Confirm `SenderAlt` supplies the phone number instead of the long LID.
- [ ] **I-17 — Sender metadata diagnostics**: With `LOG_LEVEL=DEBUG`, send one message with Pushname and one without. Confirm logs identify Pushname availability, sender JID server, alternate JID availability, and the selected sender source without logging the phone number or Pushname.
- [ ] **I-18 — Duplicate WhatsApp message**: Cause the same WhatsApp message event to be delivered again, for example by replaying the same event in a controlled test environment. Confirm it is not forwarded twice.
- [x] **I-19 — Own WhatsApp messages are ignored**: Send a message manually from the linked WhatsApp account. Confirm it is not forwarded back to Telegram.
- [ ] **I-20 — Group and broadcast messages are ignored**: Send text in a WhatsApp group and, if available, a broadcast/status context. Confirm no Telegram message is produced.
- [ ] **I-21 — Message without text is ignored**: Send a media-only message while testing the text-only build. Confirm no blank Telegram message is produced and the log records that non-text content was ignored.
- [ ] **I-22 — WhatsApp status is ignored**: Post or receive a WhatsApp status update. Confirm it is not forwarded.
- [ ] **I-23 — WhatsApp edited message**: Edit a previously forwarded WhatsApp message. Confirm no Telegram update is sent in this phase.
- [ ] **I-24 — Restart after successful delivery**: Send a text, confirm delivery, then restart the service and replay/synchronize the event if possible. Confirm the message is not forwarded again.
- [ ] **I-25 — Restart during processing**: Stop the process while a message is marked `PROCESSING`, then restart. Confirm the record is logged and changed to `FAILED`, and the message is not automatically retried.
- [ ] **I-26 — Telegram delivery failure**: Make Telegram delivery fail in a controlled way. Confirm the error traceback is logged, the message record becomes `FAILED`, and no token appears in logs.

## WhatsApp attachments to Telegram — later phase

- [x] **I-27 — Simple image**: Send one image. Confirm it arrives as a Telegram photo with the sender header.
- [x] **I-28 — Image with caption**: Send an image with a short caption. Confirm sender information and caption are preserved.
- [x] **I-29 — Image with text beyond caption limit**: Send an image with text exceeding Telegram's caption limit. Confirm the full text is sent separately before the attachment.
- [ ] **I-30 — Simple video**: Send one video. Confirm it arrives as a Telegram video with the sender header.
- [ ] **I-31 — GIF/animation**: Send a WhatsApp video with GIF playback enabled. Confirm Telegram receives it as an animation.
- [ ] **I-32 — Document**: Send a document. Confirm it arrives with its filename and MIME type preserved.
- [ ] **I-33 — Voice note**: Send a PTT voice note. Confirm Telegram receives it as a voice message.
- [ ] **I-34 — Audio file**: Send a non-PTT audio file. Confirm Telegram receives it as audio rather than a voice note.
- [ ] **I-35 — Static sticker**: Send a static WhatsApp sticker. Confirm the sticker and sender information arrive in Telegram.
- [ ] **I-36 — Location**: Send a WhatsApp location. Confirm Telegram receives the expected Google Maps link.
- [ ] **I-37 — Contact card**: Send a WhatsApp contact. Confirm Telegram receives the contact label and name where available.
- [ ] **I-38 — Attachment larger than Telegram's supported upload size**: Send an oversized attachment. Confirm the attachment is omitted, the text is still delivered, and `Attachment too large to forward` is reported.
- [ ] **I-39 — Multiple WhatsApp attachments**: Send a message containing multiple attachments. This remains a pending product decision; record the observed event shape and agree on expected ordering/grouping before marking it passed.
- [ ] **I-40 — Attachment plus long text**: Send an attachment with text exceeding the caption limit. Confirm the entire text is sent separately, followed by the attachment.
- [ ] **I-41 — WhatsApp reply context**: Reply to a WhatsApp message with text or an attachment. Confirm the Telegram message references the original WhatsApp message where supported.

## Telegram replies to WhatsApp — later phase

- [ ] **I-42 — Text reply**: Reply in Telegram to a forwarded WhatsApp text. Confirm the reply is sent to the correct WhatsApp chat and quotes the original WhatsApp message.
- [ ] **I-43 — Reply to non-existent mapping**: Reply to a Telegram message with no stored WhatsApp mapping. Confirm it is ignored and a warning is logged.
- [ ] **I-44 — Telegram message that is not a reply**: Send a normal message to the bot. Confirm it is ignored with a warning.
- [ ] **I-45 — Photo reply**: Reply with a Telegram photo. Confirm the largest photo is sent to WhatsApp.
- [ ] **I-46 — Video reply**: Reply with a Telegram video. Confirm it is sent to WhatsApp as video.
- [ ] **I-47 — Animation reply**: Reply with a Telegram animation. Confirm it is sent to WhatsApp with GIF playback enabled.
- [ ] **I-48 — Document reply**: Reply with a Telegram document. Confirm its filename and MIME type are preserved in WhatsApp.
- [ ] **I-49 — Voice and audio replies**: Reply with a voice note and separately with an audio file. Confirm the WhatsApp messages use the correct PTT setting.
- [ ] **I-50 — Sticker reply**: Reply with a static sticker and, if supported, a video sticker. Confirm WhatsApp receives the corresponding sticker type.
- [ ] **I-51 — Location reply**: Reply with a Telegram location. Confirm WhatsApp receives the matching location.
- [ ] **I-52 — Contact reply**: Reply with a Telegram contact. Confirm WhatsApp receives the contact label or contact card supported by the implementation.
- [ ] **I-53 — Oversized Telegram attachment reply**: Reply with an attachment that exceeds WhatsApp's supported upload size. Confirm a clear error is logged and no partial message is sent.
- [ ] **I-54 — Telegram album reply**: Reply with an album containing multiple attachments. This remains a pending product decision; agree on expected grouping and ordering before marking it passed.
- [ ] **I-55 — Telegram edited reply**: Edit a previously forwarded Telegram reply. Confirm no WhatsApp update is sent.

## Runtime and shutdown

- [ ] **I-56 — Telegram API unavailable at startup**: Start with Telegram unreachable. Confirm an error traceback is logged and the process exits with status 2.
- [ ] **I-57 — WhatsApp connection unavailable**: Start with WhatsApp unreachable. Confirm an error traceback is logged and the process exits with status 2.
- [x] **I-58 — Ctrl+C shutdown**: Stop a local run with Ctrl+C. Confirm it logs a normal shutdown, stops Neonize, closes SQLite, and exits without a traceback.
- [ ] **I-59 — Container SIGTERM shutdown**: Stop the Quadlet service/container normally. Confirm SIGTERM triggers the same cleanup path, the process exits before `StopTimeout=60`, and no SQLite or Neonize corruption appears after restart.
- [ ] **I-60 — Sensitive values are absent from logs**: Review startup, pairing, message, error, and shutdown logs. Confirm no Telegram token or WhatsApp authentication data is present; the pairing code appears only while registration is pending.
