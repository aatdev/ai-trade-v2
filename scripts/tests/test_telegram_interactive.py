"""Tests for skills/send-telegram/scripts/telegram_interactive.py (no network)."""

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "send-telegram" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import telegram_interactive as ti  # noqa: E402


# --------------------------------------------------------------------------- #
# callback_data — 64-byte cap + round-trip
# --------------------------------------------------------------------------- #
def test_callback_data_roundtrip():
    data = ti.callback_data(ti.ACTION_OPEN, "th_nvda_pvt_20260612_abc1")
    assert data == "o:th_nvda_pvt_20260612_abc1"
    assert ti.parse_callback_data(data) == ("o", "th_nvda_pvt_20260612_abc1")


def test_callback_data_rejects_overlong_token():
    with pytest.raises(ValueError):
        ti.callback_data(ti.ACTION_OPEN, "z" * 70)


def test_callback_data_rejects_bad_action():
    with pytest.raises(ValueError):
        ti.callback_data("q", "tok")


def test_inline_keyboard_two_buttons_under_limit():
    kb = ti.inline_keyboard("th_x_pvt_20260101_0001")
    row = kb["inline_keyboard"][0]
    assert len(row) == 2
    for btn in row:
        assert len(btn["callback_data"].encode("utf-8")) <= ti.CALLBACK_DATA_MAX


# --------------------------------------------------------------------------- #
# card_text rendering
# --------------------------------------------------------------------------- #
def test_card_text_long():
    card = {
        "ticker": "NVDA",
        "company": "NVIDIA",
        "side": "long",
        "pivot": 155.23,
        "worst_entry": 158.34,
        "stop": 150.0,
        "target": 167.68,
        "shares": 50,
        "risk_dollars": 417.0,
        "setup": "VCP",
    }
    txt = ti.card_text(card, mode_badge="📝 PAPER")
    assert "NVDA" in txt and "buy-stop" in txt and "📝 PAPER" in txt
    assert "155.23" in txt and "167.68" in txt
    assert "borrow" not in txt  # long has no short warning


def test_card_text_short_has_borrow_warning():
    card = {
        "ticker": "CHTR",
        "side": "short",
        "pivot": 145.0,
        "stop": 153.0,
        "target": 130.0,
        "shares": 100,
    }
    txt = ti.card_text(card)
    assert "sell-stop" in txt and "borrow" in txt


# --------------------------------------------------------------------------- #
# extract_callback
# --------------------------------------------------------------------------- #
def test_extract_callback_normalizes():
    update = {
        "update_id": 42,
        "callback_query": {
            "id": "cq1",
            "data": "o:th_nvda_pvt_20260612_abc1",
            "message": {"message_id": 7, "chat": {"id": -1001}},
        },
    }
    cb = ti.extract_callback(update)
    assert cb["action"] == "o" and cb["token"] == "th_nvda_pvt_20260612_abc1"
    assert cb["callback_query_id"] == "cq1" and cb["update_id"] == 42
    assert cb["chat_id"] == -1001 and cb["message_id"] == 7


def test_extract_callback_ignores_non_callback():
    assert ti.extract_callback({"update_id": 1, "message": {"text": "hi"}}) is None


# --------------------------------------------------------------------------- #
# Network actions (mocked transport)
# --------------------------------------------------------------------------- #
def test_send_order_card_returns_message_id(monkeypatch):
    captured = {}

    def fake_post(bot_token, method, payload, timeout=30):
        captured["method"] = method
        captured["payload"] = payload
        return {"ok": True, "result": {"message_id": 123}}

    monkeypatch.setattr(ti, "_tg_post", fake_post)
    card = {"ticker": "NVDA", "side": "long", "pivot": 1, "stop": 0.5, "target": 2, "shares": 10}
    mid = ti.send_order_card(card, "tok1", bot_token="B", chat_id="C")
    assert mid == 123
    assert captured["method"] == "sendMessage"
    assert captured["payload"]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "o:tok1"


def test_send_order_card_handles_api_error(monkeypatch):
    monkeypatch.setattr(ti, "_tg_post", lambda *a, **k: {"ok": False, "description": "blocked"})
    card = {"ticker": "X", "side": "long", "pivot": 1, "stop": 0.5, "target": 2, "shares": 1}
    assert ti.send_order_card(card, "t", bot_token="B", chat_id="C") is None


def test_poll_updates_passes_offset_and_filter(monkeypatch):
    seen = {}

    def fake_get(bot_token, method, params, timeout=35):
        seen.update(params)
        return {"ok": True, "result": [{"update_id": 5}]}

    monkeypatch.setattr(ti, "_tg_get", fake_get)
    res = ti.poll_updates("B", offset=10, timeout=25)
    assert res == [{"update_id": 5}]
    assert seen["offset"] == 10
    assert seen["allowed_updates"] == '["callback_query"]'


def test_poll_updates_not_ok_returns_empty(monkeypatch):
    monkeypatch.setattr(ti, "_tg_get", lambda *a, **k: {"ok": False})
    assert ti.poll_updates("B", offset=None) == []


def test_edit_card_removes_inline_keyboard(monkeypatch):
    captured = {}

    def fake_post(bot_token, method, payload, timeout=30):
        captured["method"] = method
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(ti, "_tg_post", fake_post)
    ti.edit_card("B", -100, 5, "✋ Пропущено — тезис остаётся ENTRY_READY")
    # editMessageText with an EMPTY inline_keyboard explicitly clears the buttons,
    # and NO parse_mode so underscores (ENTRY_READY) can't break the edit.
    assert captured["method"] == "editMessageText"
    assert captured["payload"]["reply_markup"] == {"inline_keyboard": []}
    assert "parse_mode" not in captured["payload"]
    assert captured["payload"]["message_id"] == 5


def test_card_and_edit_are_plain_text(monkeypatch):
    # Regression: underscores in card content / outcome text must NOT be sent
    # with parse_mode (Markdown would reject ENTRY_READY-style strings).
    txt = ti.card_text(
        {
            "ticker": "NVDA",
            "side": "long",
            "pivot": 1,
            "stop": 0.5,
            "target": 2,
            "shares": 1,
            "setup": "VCP_breakout_test",
        },
    )
    assert "*" not in txt  # no markdown bold markers
    captured = {}
    monkeypatch.setattr(
        ti,
        "_tg_post",
        lambda bt, m, p, timeout=30: (
            captured.update(p=p) or {"ok": True, "result": {"message_id": 1}}
        ),
    )
    ti.send_order_card(
        {"ticker": "NVDA", "side": "long", "pivot": 1, "stop": 0.5, "target": 2, "shares": 1},
        "tok",
        bot_token="B",
        chat_id="C",
    )
    assert "parse_mode" not in captured["p"]
