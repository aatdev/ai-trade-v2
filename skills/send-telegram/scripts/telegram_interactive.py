#!/usr/bin/env python3
"""Interactive Telegram helpers: order cards with inline buttons + callback polling.

The companion ``send_telegram.py`` is strictly one-way (curl -> sendMessage /
sendDocument). This module adds the two-way pieces the watchlist-order flow
needs, kept separate so the simple sender stays dependency-free:

  * ``send_order_card`` — sendMessage with an ``inline_keyboard`` of two buttons
    ("✅ Открыть" / "✋ Не открывать"), returning the Telegram ``message_id`` so
    the card can later be edited to show the outcome.
  * ``poll_updates`` — a ``getUpdates`` long-poll restricted to ``callback_query``
    updates, for a daemon that listens for button taps.
  * ``answer_callback`` / ``edit_card`` — clear the client spinner and rewrite the
    card text once an order is placed or skipped.

``callback_data`` is capped by Telegram at 64 bytes, so it carries only a compact
``"<action>:<token>"`` (action ∈ {o, x}); the full order spec lives in the
caller's ledger keyed by that token. Thesis ids (``th_<tkr>_<abbr>_<date>_<h4>``)
are short enough to be the token directly.

Env (via ``send_telegram.load_dotenv``): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import json
import os
from typing import Any

# Reuse the dotenv loader from the one-way sender (sibling module on sys.path[0]).
from send_telegram import load_dotenv

CALLBACK_DATA_MAX = 64  # Telegram hard limit (bytes)
ACTION_OPEN = "o"
ACTION_SKIP = "x"
DEFAULT_POLL_TIMEOUT = 25


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
def resolve_credentials(
    bot_token: str | None = None, chat_id: str | None = None
) -> tuple[str, str]:
    """Resolve (bot_token, chat_id) from args, then ``.env``/environment."""
    load_dotenv()
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    return bot_token, chat_id


# --------------------------------------------------------------------------- #
# Thin HTTP wrappers (monkeypatched in tests)
# --------------------------------------------------------------------------- #
def _api_url(bot_token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/{method}"


def _tg_post(bot_token: str, method: str, payload: dict, timeout: float = 30) -> dict:
    import requests  # type: ignore

    resp = requests.post(_api_url(bot_token, method), json=payload, timeout=timeout)
    return resp.json()


def _tg_get(bot_token: str, method: str, params: dict, timeout: float = 35) -> dict:
    import requests  # type: ignore

    resp = requests.get(_api_url(bot_token, method), params=params, timeout=timeout)
    return resp.json()


# --------------------------------------------------------------------------- #
# Pure builders (no network — fully unit-testable)
# --------------------------------------------------------------------------- #
def callback_data(action: str, token: str) -> str:
    """Build and length-check the ``"<action>:<token>"`` callback payload."""
    if action not in (ACTION_OPEN, ACTION_SKIP):
        raise ValueError(f"action must be {ACTION_OPEN!r} or {ACTION_SKIP!r}")
    data = f"{action}:{token}"
    if len(data.encode("utf-8")) > CALLBACK_DATA_MAX:
        raise ValueError(f"callback_data exceeds {CALLBACK_DATA_MAX} bytes: {data!r}")
    return data


def parse_callback_data(data: str) -> tuple[str, str]:
    """Inverse of ``callback_data``: split into (action, token)."""
    action, _, token = data.partition(":")
    return action, token


def inline_keyboard(
    token: str, *, confirm_label: str = "✅ Открыть", decline_label: str = "✋ Не открывать"
) -> dict:
    """Two-button inline keyboard (confirm / decline) for one card.

    Labels vary by card kind (open vs +2R scale), but both encode the same
    ``o:<token>`` / ``x:<token>`` callback_data — the daemon routes by the
    ledger entry's ``kind``, not by the label.
    """
    return {
        "inline_keyboard": [
            [
                {"text": confirm_label, "callback_data": callback_data(ACTION_OPEN, token)},
                {"text": decline_label, "callback_data": callback_data(ACTION_SKIP, token)},
            ]
        ]
    }


def card_text(card: dict, mode_badge: str = "📝 PAPER") -> str:
    """Human-readable order card body (PLAIN text — no markdown).

    Sent and edited without ``parse_mode`` on purpose: outcome/resolution texts
    routinely contain underscores (``ENTRY_READY``, ``IB_ALLOW_ORDER_PLACEMENT``)
    which Markdown would read as an unterminated italic entity and reject the
    whole edit — leaving the buttons stuck on the card. ``card`` keys match the
    ledger.
    """
    side = (card.get("side") or "long").lower()
    arrow = "🟢 LONG" if side == "long" else "🔴 SHORT"
    verb = "buy-stop" if side == "long" else "sell-stop"
    lines = [
        f"{mode_badge}  {arrow}  {card['ticker']}"
        + (f" — {card['company']}" if card.get("company") else ""),
        f"Вход ({verb}): ${_fmt(card['pivot'])}"
        + (f"  (макс. ${_fmt(card['worst_entry'])})" if card.get("worst_entry") else ""),
        f"Стоп: ${_fmt(card['stop'])}   Цель: ${_fmt(card['target'])}",
        f"Акций: {_fmt(card['shares'])}"
        + (
            f"   Риск: ${_fmt(card['risk_dollars'])}"
            if card.get("risk_dollars") is not None
            else ""
        ),
    ]
    if card.get("setup"):
        lines.append(str(card["setup"]))
    if side == "short":
        lines.append("⚠️ Шорт: проверь доступность бумаги (borrow/SSR) у брокера.")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# --------------------------------------------------------------------------- #
# Network actions
# --------------------------------------------------------------------------- #
def send_order_card(
    card: dict, token: str, *, bot_token: str, chat_id: str, mode_badge: str = "📝 PAPER"
) -> int | None:
    """Send one order card with buttons; return its ``message_id`` (None on error)."""
    payload = {
        "chat_id": chat_id,
        "text": card_text(card, mode_badge),  # plain text — see card_text docstring
        "reply_markup": inline_keyboard(token),
    }
    resp = _tg_post(bot_token, "sendMessage", payload)
    if not resp.get("ok"):
        return None
    return resp.get("result", {}).get("message_id")


def scale_card_text(card: dict, mode_badge: str = "📝 PAPER") -> str:
    """+2R scale-out card body (PLAIN text). Keys: ticker/side/shares/entry_price/current_price."""
    side = (card.get("side") or "long").lower()
    arrow = "🟢 LONG" if side == "long" else "🔴 SHORT"
    shares = card.get("shares") or 0
    half = max(1, int(shares // 2))
    lines = [
        f"{mode_badge}  💰 +2R  {card['ticker']}  ({arrow})",
        f"Цена ~${_fmt(card.get('current_price'))} достигла +2R.",
        f"Действие: продать 50% ({half} из {_fmt(shares)}) рыночным "
        f"+ перенести стоп остатка в безубыток ${_fmt(card.get('entry_price'))}.",
    ]
    return "\n".join(lines)


def send_scale_card(
    card: dict, token: str, *, bot_token: str, chat_id: str, mode_badge: str = "📝 PAPER"
) -> int | None:
    """Send a +2R scale-out card with confirm/decline buttons; return message_id."""
    payload = {
        "chat_id": chat_id,
        "text": scale_card_text(card, mode_badge),
        "reply_markup": inline_keyboard(
            token, confirm_label="💰 Зафиксировать 50%", decline_label="✋ Не сейчас"
        ),
    }
    resp = _tg_post(bot_token, "sendMessage", payload)
    if not resp.get("ok"):
        return None
    return resp.get("result", {}).get("message_id")


def close_card_text(card: dict, mode_badge: str = "📝 PAPER") -> str:
    """Position-management exit card body (PLAIN text). Keys: ticker/side/shares/reason."""
    side = (card.get("side") or "long").lower()
    arrow = "🟢 LONG" if side == "long" else "🔴 SHORT"
    lines = [
        f"{mode_badge}  ⛔️ ЗАКРЫТЬ  {card['ticker']}  ({arrow})",
        f"Причина: {card.get('reason')}",
        f"Действие: закрыть {_fmt(card.get('shares'))} шт рыночным + снять защитные ордера.",
    ]
    return "\n".join(lines)


def send_close_card(
    card: dict, token: str, *, bot_token: str, chat_id: str, mode_badge: str = "📝 PAPER"
) -> int | None:
    """Send a position-management exit card with confirm/decline buttons; return message_id."""
    payload = {
        "chat_id": chat_id,
        "text": close_card_text(card, mode_badge),
        "reply_markup": inline_keyboard(
            token, confirm_label="⛔️ Закрыть", decline_label="✋ Оставить"
        ),
    }
    resp = _tg_post(bot_token, "sendMessage", payload)
    if not resp.get("ok"):
        return None
    return resp.get("result", {}).get("message_id")


def close_detected_card_text(card: dict, mode_badge: str = "📝 PAPER") -> str:
    """Detected-external-close confirmation card body (PLAIN text).

    Unlike the rule-violation close card, the position is ALREADY flat at the
    broker (it disappeared from the IB snapshot). Confirming only RECORDS the
    close in the journal and generates the postmortem — no order is placed.
    Keys: ticker/side/shares/reason/price."""
    side = (card.get("side") or "long").lower()
    arrow = "🟢 LONG" if side == "long" else "🔴 SHORT"
    lines = [
        f"{mode_badge}  📍 ЗАКРЫТИЕ ОБНАРУЖЕНО  {card['ticker']}  ({arrow})",
        f"Позиции нет в IB ({card.get('reason') or 'закрыта вне системы'}).",
    ]
    if card.get("price"):
        lines.append(
            f"Цена выхода (≈): ${_fmt(card.get('price'))} — поправь в журнале при необходимости."
        )
    lines.append("Действие: записать закрытие в журнал + постмортем (ордер НЕ выставляется).")
    return "\n".join(lines)


def send_close_detected_card(
    card: dict, token: str, *, bot_token: str, chat_id: str, mode_badge: str = "📝 PAPER"
) -> int | None:
    """Send a detected-external-close confirmation card; return message_id."""
    payload = {
        "chat_id": chat_id,
        "text": close_detected_card_text(card, mode_badge),
        "reply_markup": inline_keyboard(
            token, confirm_label="✅ Записать закрытие", decline_label="✋ Не сейчас"
        ),
    }
    resp = _tg_post(bot_token, "sendMessage", payload)
    if not resp.get("ok"):
        return None
    return resp.get("result", {}).get("message_id")


def poll_updates(
    bot_token: str, offset: int | None, timeout: int = DEFAULT_POLL_TIMEOUT
) -> list[dict]:
    """Long-poll ``getUpdates`` for callback_query updates only."""
    params = {
        "timeout": timeout,
        "allowed_updates": json.dumps(["callback_query"]),
    }
    if offset is not None:
        params["offset"] = offset
    resp = _tg_get(bot_token, "getUpdates", params, timeout=timeout + 10)
    if not resp.get("ok"):
        return []
    return resp.get("result", []) or []


def extract_callback(update: dict) -> dict | None:
    """Normalize a callback_query update into the fields the daemon needs."""
    cq = update.get("callback_query")
    if not isinstance(cq, dict):
        return None
    action, token = parse_callback_data(cq.get("data", ""))
    msg = cq.get("message", {}) or {}
    chat = msg.get("chat", {}) or {}
    return {
        "update_id": update.get("update_id"),
        "callback_query_id": cq.get("id"),
        "action": action,
        "token": token,
        "chat_id": chat.get("id"),
        "message_id": msg.get("message_id"),
    }


def answer_callback(bot_token: str, callback_query_id: str, text: str = "") -> dict:
    """Clear the client-side loading spinner on a tapped button."""
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return _tg_post(bot_token, "answerCallbackQuery", payload)


def edit_card(bot_token: str, chat_id: Any, message_id: Any, text: str) -> dict:
    """Rewrite a card's text AND remove its inline buttons (resolved outcome).

    Two deliberate choices, both verified against the live Bot API:
    - NO ``parse_mode``: outcome texts contain underscores (``ENTRY_READY``,
      ``IB_ALLOW_ORDER_PLACEMENT``); under Markdown those are unterminated italic
      entities, the edit is rejected, and the buttons stay stuck on the card.
    - explicit empty ``reply_markup`` (``inline_keyboard: []``): unambiguously
      clears the keyboard so a resolved/expired card can no longer be tapped.
    """
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "reply_markup": {"inline_keyboard": []},
    }
    return _tg_post(bot_token, "editMessageText", payload)
