---
name: send-telegram
description: Send textual messages or files (documents, images, logs, reports) to a Telegram chat using a Telegram Bot. Trigger this skill whenever the user asks to send something via Telegram, says "Telegram this to me", or requests that outputs or files be forwarded to their Telegram account.
compatibility: python3, curl
---

# Send to Telegram Skill

This skill allows you to send text messages and file attachments to a user's Telegram chat.
It uses a bundled Python wrapper around `curl` to properly handle both simple text and file uploads (like logs, PDFs, images).

## Requirements

The user must provide their Telegram Bot Token and Chat ID. Usually, these should be securely stored as environment variables:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

> **Note**: If the user asks you to send something to Telegram and the environment variables are not set or known in your context, prompt the user to provide them (or ask if they are configured).

## How to use

Run the bundled `send_telegram.py` script to dispatch the message or file.

```bash
python scripts/send_telegram.py \
    --token "$TELEGRAM_BOT_TOKEN" \
    --chat-id "$TELEGRAM_CHAT_ID" \
    --message "Your message here" \
    --file "/path/to/an/optional/file.pdf"
```

### Tips for AI Assistant
- Provide detailed `message` context explaining what is being sent, especially if sending a file.
- If a user just says "send this log to telegram", locate the log file and use the `--file` argument along with a helpful message.
- If the token/chat_id is not in bash environment, check if the user previously gave it to you or stored it in a config file, and pass them via the arguments.
- Be careful with large files; Telegram bot API limits file uploads to 50MB.

## Interactive companion: `telegram_interactive.py`

`send_telegram.py` is strictly one-way. A sibling module, `scripts/telegram_interactive.py`, adds two-way messaging for confirmation flows (e.g. the watchlist-order automation in `scripts/watchlist_orders.py`):
- `send_order_card(...)` — `sendMessage` with an `inline_keyboard` of action buttons, returning the `message_id` so the card can later be edited with the outcome.
- `poll_updates(...)` — a `getUpdates` long-poll restricted to `callback_query` updates, for a daemon that listens for button taps.
- `answer_callback` / `edit_card` — clear the tap spinner and rewrite the card.

It reuses the same `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` env vars (and `send_telegram.load_dotenv`). `callback_data` is capped by Telegram at 64 bytes, so it carries only a short `"<action>:<token>"`; the full payload lives in the caller's ledger keyed by that token.
