"""Tests for scripts/watchlist_orders.py (send producer + pure matching)."""

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))

import watchlist_orders as wo  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _thesis(tid, ticker, side="long", status="ENTRY_READY"):
    return {"thesis_id": tid, "ticker": ticker, "side": side, "status": status}


def _cand(ticker, side="long", thesis_id=None, **geo):
    base = {
        "ticker": ticker,
        "side": side,
        "pivot": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "shares": 10,
        "risk_dollars": 50.0,
    }
    base.update(geo)
    if thesis_id:
        base["thesis_id"] = thesis_id
    return base


# --------------------------------------------------------------------------- #
# Pure matching
# --------------------------------------------------------------------------- #
def test_select_cards_matches_by_thesis_id():
    wl = {"candidates": [_cand("NVDA", thesis_id="th_nvda_pvt_20260612_aaaa")]}
    theses = [_thesis("th_nvda_pvt_20260612_aaaa", "NVDA")]
    cards = wo.select_cards(wl, theses, "2026-06-15", "allow")
    assert len(cards) == 1
    assert cards[0]["thesis_id"] == "th_nvda_pvt_20260612_aaaa"
    assert cards[0]["coid"] == "wl-th_nvda_pvt_20260612_aaaa-2026-06-15"


def test_select_cards_fallback_ticker_side():
    wl = {"candidates": [_cand("AMD", side="long")]}  # no thesis_id on candidate
    theses = [_thesis("th_amd_pvt_20260612_bbbb", "AMD", "long")]
    cards = wo.select_cards(wl, theses, "2026-06-15", "allow")
    assert len(cards) == 1 and cards[0]["thesis_id"] == "th_amd_pvt_20260612_bbbb"


def test_select_cards_skips_non_entry_ready():
    wl = {"candidates": [_cand("NVDA", thesis_id="th_nvda_pvt_20260612_aaaa")]}
    theses = [_thesis("th_nvda_pvt_20260612_aaaa", "NVDA", status="ACTIVE")]
    assert wo.select_cards(wl, theses, "2026-06-15", "allow") == []


def test_select_cards_skips_incomplete_geometry():
    wl = {"candidates": [_cand("NVDA", thesis_id="th_nvda_pvt_20260612_aaaa", target=None)]}
    theses = [_thesis("th_nvda_pvt_20260612_aaaa", "NVDA")]
    assert wo.select_cards(wl, theses, "2026-06-15", "allow") == []


def test_select_cards_side_mismatch_no_match():
    wl = {"candidates": [_cand("CHTR", side="short")]}
    theses = [_thesis("th_chtr_pvt_20260612_cccc", "CHTR", "long")]  # long thesis, short candidate
    assert wo.select_cards(wl, theses, "2026-06-15", "restrict") == []


def test_select_cards_short_carded_under_restrict():
    wl = {
        "candidates": [
            _cand(
                "CHTR",
                side="short",
                thesis_id="th_chtr_pvt_20260612_cccc",
                pivot=145.0,
                stop=153.0,
                target=130.0,
            )
        ]
    }
    theses = [_thesis("th_chtr_pvt_20260612_cccc", "CHTR", "short")]
    cards = wo.select_cards(wl, theses, "2026-06-15", "restrict")
    assert len(cards) == 1 and cards[0]["side"] == "short"
    cards_cash = wo.select_cards(wl, theses, "2026-06-15", "cash-priority")
    assert len(cards_cash) == 1  # shorts also allowed under cash-priority


def test_select_cards_long_filtered_out_under_restrict():
    wl = {"candidates": [_cand("NVDA", side="long", thesis_id="th_nvda_pvt_20260612_aaaa")]}
    theses = [_thesis("th_nvda_pvt_20260612_aaaa", "NVDA", "long")]
    assert (
        wo.select_cards(wl, theses, "2026-06-15", "restrict") == []
    )  # no new longs under restrict


def test_select_cards_short_filtered_out_under_allow():
    wl = {
        "candidates": [
            _cand(
                "CHTR",
                side="short",
                thesis_id="th_chtr_pvt_20260612_cccc",
                pivot=145.0,
                stop=153.0,
                target=130.0,
            )
        ]
    }
    theses = [_thesis("th_chtr_pvt_20260612_cccc", "CHTR", "short")]
    assert wo.select_cards(wl, theses, "2026-06-15", "allow") == []  # no shorts under allow


# --------------------------------------------------------------------------- #
# cmd_send guards + happy path
# --------------------------------------------------------------------------- #
class _Args:
    def __init__(self, **kw):
        self.date = "2026-06-15"
        self.dry_run = False
        self.no_telegram = False
        self.__dict__.update(kw)


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Point the ledger at tmp and stub the scheduler helpers."""
    monkeypatch.setattr(wo.sched, "TRADING_DATA_DIR", tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wo.sched, "decision_path", lambda d: tmp_path / "gate.json")
    monkeypatch.setattr(wo.sched, "latest_watchlist", lambda: tmp_path / "wl.json")
    monkeypatch.setattr(wo.sched, "_watchlist_is_fresh", lambda wl, today: True)
    monkeypatch.setattr(wo.pib, "is_paper", lambda: True)
    monkeypatch.setattr(wo.pib, "mode_badge", lambda: "📝 PAPER")
    monkeypatch.setattr(wo.ti, "resolve_credentials", lambda *a, **k: ("BOT", "CHAT"))
    return tmp_path


def test_cmd_send_short_under_restrict_sends(monkeypatch, patched):
    monkeypatch.setattr(
        wo.sched, "read_decision", lambda p: {"decision": "restrict", "degraded": False}
    )
    monkeypatch.setattr(
        wo.sched,
        "_read_json",
        lambda p: {
            "date": "2026-06-15",
            "candidates": [
                _cand(
                    "CHTR",
                    side="short",
                    thesis_id="th_chtr_pvt_20260612_cccc",
                    pivot=145.0,
                    stop=153.0,
                    target=130.0,
                )
            ],
        },
    )
    monkeypatch.setattr(
        wo.sched, "_list_theses", lambda: [_thesis("th_chtr_pvt_20260612_cccc", "CHTR", "short")]
    )
    sent = []
    monkeypatch.setattr(wo.ti, "send_order_card", lambda *a, **k: sent.append(a) or 1)
    assert wo.cmd_send(_Args()) == 0
    assert len(sent) == 1  # short carded under restrict


def test_cmd_send_long_under_restrict_no_cards(monkeypatch, patched):
    monkeypatch.setattr(
        wo.sched, "read_decision", lambda p: {"decision": "restrict", "degraded": False}
    )
    monkeypatch.setattr(
        wo.sched,
        "_read_json",
        lambda p: {
            "date": "2026-06-15",
            "candidates": [_cand("NVDA", side="long", thesis_id="th_nvda_pvt_20260612_aaaa")],
        },
    )
    monkeypatch.setattr(
        wo.sched, "_list_theses", lambda: [_thesis("th_nvda_pvt_20260612_aaaa", "NVDA", "long")]
    )
    sent = []
    monkeypatch.setattr(wo.ti, "send_order_card", lambda *a, **k: sent.append(a) or 1)
    assert wo.cmd_send(_Args()) == 0
    assert sent == []  # no new longs under restrict


def test_cmd_send_skips_when_degraded(monkeypatch, patched):
    monkeypatch.setattr(
        wo.sched, "read_decision", lambda p: {"decision": "allow", "degraded": True}
    )
    monkeypatch.setattr(wo.ti, "send_order_card", lambda *a, **k: 1)
    assert wo.cmd_send(_Args()) == 0


def test_cmd_send_skips_when_stale(monkeypatch, patched):
    monkeypatch.setattr(
        wo.sched, "read_decision", lambda p: {"decision": "allow", "degraded": False}
    )
    monkeypatch.setattr(wo.sched, "_read_json", lambda p: {"date": "2020-01-01", "candidates": []})
    monkeypatch.setattr(wo.sched, "_watchlist_is_fresh", lambda wl, today: False)
    monkeypatch.setattr(wo.ti, "send_order_card", lambda *a, **k: 1)
    assert wo.cmd_send(_Args()) == 0


def test_cmd_send_happy_path_writes_ledger(monkeypatch, patched):
    monkeypatch.setattr(
        wo.sched, "read_decision", lambda p: {"decision": "allow", "degraded": False}
    )
    monkeypatch.setattr(
        wo.sched,
        "_read_json",
        lambda p: {
            "date": "2026-06-15",
            "candidates": [_cand("NVDA", thesis_id="th_nvda_pvt_20260612_aaaa")],
        },
    )
    monkeypatch.setattr(
        wo.sched, "_list_theses", lambda: [_thesis("th_nvda_pvt_20260612_aaaa", "NVDA")]
    )

    sent = {}

    def fake_send(card, token, *, bot_token, chat_id, mode_badge):
        sent["token"] = token
        sent["badge"] = mode_badge
        return 555

    monkeypatch.setattr(wo.ti, "send_order_card", fake_send)

    rc = wo.cmd_send(_Args())
    assert rc == 0
    assert sent["token"] == "th_nvda_pvt_20260612_aaaa"
    ledger = wo.load_ledger("2026-06-15")
    entry = ledger["orders"]["th_nvda_pvt_20260612_aaaa"]
    assert entry["status"] == "pending"
    assert entry["message_id"] == 555
    assert entry["coid"] == "wl-th_nvda_pvt_20260612_aaaa-2026-06-15"
    assert ledger["mode"] == "paper"


def test_cmd_send_idempotent_no_duplicate(monkeypatch, patched):
    monkeypatch.setattr(
        wo.sched, "read_decision", lambda p: {"decision": "allow", "degraded": False}
    )
    monkeypatch.setattr(
        wo.sched,
        "_read_json",
        lambda p: {
            "date": "2026-06-15",
            "candidates": [_cand("NVDA", thesis_id="th_nvda_pvt_20260612_aaaa")],
        },
    )
    monkeypatch.setattr(
        wo.sched, "_list_theses", lambda: [_thesis("th_nvda_pvt_20260612_aaaa", "NVDA")]
    )
    calls = []
    monkeypatch.setattr(wo.ti, "send_order_card", lambda *a, **k: calls.append(1) or 1)

    wo.cmd_send(_Args())
    wo.cmd_send(_Args())  # second run must not resend
    assert len(calls) == 1


def test_cmd_send_dry_run_sends_nothing(monkeypatch, patched):
    monkeypatch.setattr(
        wo.sched, "read_decision", lambda p: {"decision": "allow", "degraded": False}
    )
    monkeypatch.setattr(
        wo.sched,
        "_read_json",
        lambda p: {
            "date": "2026-06-15",
            "candidates": [_cand("NVDA", thesis_id="th_nvda_pvt_20260612_aaaa")],
        },
    )
    monkeypatch.setattr(
        wo.sched, "_list_theses", lambda: [_thesis("th_nvda_pvt_20260612_aaaa", "NVDA")]
    )
    monkeypatch.setattr(
        wo.ti, "send_order_card", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no send"))
    )
    assert wo.cmd_send(_Args(dry_run=True)) == 0
    assert not wo.ledger_path("2026-06-15").exists()


# --------------------------------------------------------------------------- #
# Daemon: heat gate
# --------------------------------------------------------------------------- #
def _entry(status="pending", **kw):
    e = {
        "thesis_id": "th_nvda_pvt_20260612_aaaa",
        "ticker": "NVDA",
        "side": "long",
        "pivot": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "shares": 10,
        "risk_dollars": 50.0,
        "coid": "wl-th_nvda_pvt_20260612_aaaa-2026-06-15",
        "message_id": None,
        "chat_id": None,
        "status": status,
        "order_ids": [],
        "entry_order_id": None,
        "placed_at": None,
        "fill_price": None,
        "error": None,
    }
    e.update(kw)
    return e


def test_heat_ok_for_no_file_passes(monkeypatch):
    monkeypatch.setattr(wo.sched, "_latest", lambda d, p: None)
    ok, reason = wo.heat_ok_for(_entry())
    assert ok is True and "heat-гейт пропущен" in reason


def test_heat_ok_for_blocks_no_slots(monkeypatch, tmp_path):
    monkeypatch.setattr(wo.sched, "_latest", lambda d, p: tmp_path / "h.json")
    monkeypatch.setattr(
        wo,
        "_read_json_file",
        lambda p: {"remaining_position_slots": 0, "remaining_heat_dollars": 9000},
    )
    ok, reason = wo.heat_ok_for(_entry())
    assert ok is False and "слотов" in reason


def test_heat_ok_for_blocks_insufficient_heat(monkeypatch, tmp_path):
    monkeypatch.setattr(wo.sched, "_latest", lambda d, p: tmp_path / "h.json")
    monkeypatch.setattr(
        wo,
        "_read_json_file",
        lambda p: {"remaining_position_slots": 3, "remaining_heat_dollars": 40},
    )
    ok, reason = wo.heat_ok_for(_entry(risk_dollars=50.0))
    assert ok is False and "heat" in reason


def test_heat_ok_for_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(wo.sched, "_latest", lambda d, p: tmp_path / "h.json")
    monkeypatch.setattr(
        wo,
        "_read_json_file",
        lambda p: {"remaining_position_slots": 3, "remaining_heat_dollars": 9000},
    )
    ok, _ = wo.heat_ok_for(_entry())
    assert ok is True


# --------------------------------------------------------------------------- #
# Daemon: handle_open
# --------------------------------------------------------------------------- #
def test_handle_open_preview_when_not_live(monkeypatch):
    monkeypatch.setattr(wo, "heat_ok_for", lambda e: (True, "ok"))
    monkeypatch.setattr(
        wo.pib, "submit_brackets", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no place"))
    )
    entry = _entry()
    wo.handle_open(entry, port=9000, live=False, bot_token="B")
    assert entry["status"] == "preview"


def test_handle_open_blocked_by_heat(monkeypatch):
    monkeypatch.setattr(wo, "heat_ok_for", lambda e: (False, "нет свободных слотов позиций (heat)"))
    entry = _entry()
    wo.handle_open(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "skipped" and "слот" in entry["error"]


def test_handle_open_gateway_down(monkeypatch):
    monkeypatch.setattr(wo, "heat_ok_for", lambda e: (True, "ok"))
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    entry = _entry()
    wo.handle_open(entry, port=None, live=True, bot_token="B")
    assert entry["status"] == "error" and "gateway" in entry["error"].lower()


def test_handle_open_success(monkeypatch):
    monkeypatch.setattr(wo, "heat_ok_for", lambda e: (True, "ok"))
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    monkeypatch.setattr(wo.pib, "live_order_refs", lambda port: set())
    monkeypatch.setattr(wo.pib, "resolve_conid", lambda port, t: 265598)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU1")
    monkeypatch.setattr(
        wo.pib,
        "submit_brackets",
        lambda *a, **k: {
            "ok": True,
            "order_ids": ["100", "101", "102"],
            "entry_order_id": "100",
            "entry_order_ids": ["100"],
            "raw": [],
        },
    )
    entry = _entry()
    wo.handle_open(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "placed"
    assert entry["entry_order_id"] == "100" and entry["order_ids"] == ["100", "101", "102"]
    assert entry["entry_order_ids"] == ["100"]
    assert entry["placed_at"] is not None


def test_handle_open_surfaces_broker_reason(monkeypatch):
    monkeypatch.setattr(wo, "heat_ok_for", lambda e: (True, "ok"))
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    monkeypatch.setattr(wo.pib, "live_order_refs", lambda port: set())
    monkeypatch.setattr(wo.pib, "resolve_conid", lambda port, t: 265598)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU1")
    monkeypatch.setattr(
        wo.pib,
        "submit_brackets",
        lambda *a, **k: {
            "ok": False,
            "order_ids": [],
            "entry_order_id": None,
            "entry_order_ids": [],
            "reason": "Stop price must be above the current price",
            "raw": {"error": "Stop price must be above the current price"},
        },
    )
    entry = _entry()
    wo.handle_open(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "error"
    assert entry["error"] == "Stop price must be above the current price"


def test_handle_open_uses_unique_attempt_coid(monkeypatch):
    # The cOID sent to IB carries a per-attempt nonce so re-placing after a cancel
    # never collides with the already-registered (cancelled) Local order ID — but
    # it still starts with the stable base coid used for detection/idempotency.
    monkeypatch.setattr(wo, "heat_ok_for", lambda e: (True, "ok"))
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    monkeypatch.setattr(wo.pib, "live_order_refs", lambda port: set())
    monkeypatch.setattr(wo.pib, "resolve_conid", lambda port, t: 1)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU1")
    seen = {}

    def fake_build(side, conid, shares, pivot, stop, target, coid, **k):
        seen["coid"] = coid
        return [[{"x": 1}]]

    monkeypatch.setattr(wo.pib, "build_sub_brackets", fake_build)
    monkeypatch.setattr(
        wo.pib,
        "submit_brackets",
        lambda *a, **k: {
            "ok": True,
            "order_ids": ["1"],
            "entry_order_id": "1",
            "entry_order_ids": ["1"],
        },
    )
    entry = _entry()
    wo.handle_open(entry, port=9000, live=True, bot_token="B")
    assert seen["coid"].startswith(entry["coid"]) and seen["coid"] != entry["coid"]


def test_handle_open_idempotent_existing_coid(monkeypatch):
    monkeypatch.setattr(wo, "heat_ok_for", lambda e: (True, "ok"))
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    entry = _entry()
    monkeypatch.setattr(wo.pib, "live_order_refs", lambda port: {entry["coid"]})
    monkeypatch.setattr(
        wo.pib,
        "submit_brackets",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no double place")),
    )
    wo.handle_open(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "placed"


def test_handle_open_idempotent_across_sessions_gtc(monkeypatch):
    # A GTC entry placed on an EARLIER day still rests at the broker under that
    # day's coid (`wl-<id>-<older-date>-…`). Today's run builds a coid with today's
    # date — date-agnostic prefix matching must still recognize the resting entry
    # and refuse to stack a duplicate.
    monkeypatch.setattr(wo, "heat_ok_for", lambda e: (True, "ok"))
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    entry = _entry(coid="wl-th_nvda_pvt_20260612_aaaa-2026-06-19")  # today's anchor
    prior_day_ref = "wl-th_nvda_pvt_20260612_aaaa-2026-06-12-ee0201860-t1"  # rests from 06-12
    monkeypatch.setattr(wo.pib, "live_order_refs", lambda port: {prior_day_ref})
    monkeypatch.setattr(
        wo.pib,
        "submit_brackets",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no cross-day double place")),
    )
    wo.handle_open(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "placed"


def test_handle_open_already_placed_noop(monkeypatch):
    monkeypatch.setattr(
        wo, "heat_ok_for", lambda e: (_ for _ in ()).throw(AssertionError("should not check"))
    )
    entry = _entry(status="placed")
    wo.handle_open(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "placed"


def test_handle_open_broker_rejects(monkeypatch):
    monkeypatch.setattr(wo, "heat_ok_for", lambda e: (True, "ok"))
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    monkeypatch.setattr(wo.pib, "live_order_refs", lambda port: set())
    monkeypatch.setattr(wo.pib, "resolve_conid", lambda port, t: 1)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU1")
    monkeypatch.setattr(
        wo.pib,
        "submit_brackets",
        lambda *a, **k: {
            "ok": False,
            "order_ids": [],
            "entry_order_id": None,
            "entry_order_ids": [],
            "raw": {"error": "x"},
        },
    )
    entry = _entry()
    wo.handle_open(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "error"


# --------------------------------------------------------------------------- #
# Daemon: handle_skip
# --------------------------------------------------------------------------- #
def test_handle_skip_leaves_entry_ready():
    entry = _entry()
    wo.handle_skip(entry, bot_token="B")
    assert entry["status"] == "skipped"


def test_handle_skip_noop_when_placed():
    entry = _entry(status="placed")
    wo.handle_skip(entry, bot_token="B")
    assert entry["status"] == "placed"


# --------------------------------------------------------------------------- #
# Daemon: expire_pending_cards (timeout -> strip buttons)
# --------------------------------------------------------------------------- #
def test_expire_pending_cards_flips_and_strips(monkeypatch):
    edits = []
    monkeypatch.setattr(wo.ti, "edit_card", lambda *a, **k: edits.append(a) or {"ok": True})
    ledger = {
        "orders": {
            "t1": _entry(thesis_id="t1", status="pending", message_id=5, chat_id=-1),
            "t2": _entry(thesis_id="t2", status="placed", message_id=6, chat_id=-1),
            "t3": _entry(thesis_id="t3", status="skipped", message_id=7, chat_id=-1),
        }
    }
    changed = wo.expire_pending_cards(ledger, bot_token="B")
    assert changed is True
    assert ledger["orders"]["t1"]["status"] == "expired"
    assert ledger["orders"]["t2"]["status"] == "placed"  # non-pending untouched
    assert ledger["orders"]["t3"]["status"] == "skipped"
    assert len(edits) == 1  # only the pending card's buttons were stripped


def test_expire_pending_cards_noop_when_none_pending(monkeypatch):
    monkeypatch.setattr(
        wo.ti, "edit_card", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no edit"))
    )
    ledger = {"orders": {"t1": _entry(status="placed", message_id=5, chat_id=-1)}}
    assert wo.expire_pending_cards(ledger, bot_token="B") is False


class _ListenTimeoutArgs:
    def __init__(self, **kw):
        self.date = "2026-06-15"
        self.live = False
        self.window_sec = 0  # deadline already passed -> exercises the timeout branch
        self.once = False
        self.__dict__.update(kw)


def test_cmd_listen_timeout_expires_pending(monkeypatch, patched):
    ledger = {
        "date": "2026-06-15",
        "mode": "paper",
        "orders": {
            "th_x_pvt_20260101_0001": _entry(
                thesis_id="th_x_pvt_20260101_0001", status="pending", message_id=9, chat_id=-100
            )
        },
    }
    wo.save_ledger("2026-06-15", ledger)
    monkeypatch.setattr(wo.ti, "poll_updates", lambda *a, **k: [])
    edits = []
    monkeypatch.setattr(wo.ti, "edit_card", lambda *a, **k: edits.append(a) or {"ok": True})

    rc = wo.cmd_listen(_ListenTimeoutArgs())
    assert rc == 0
    entry = wo.load_ledger("2026-06-15")["orders"]["th_x_pvt_20260101_0001"]
    assert entry["status"] == "expired"
    assert edits  # the card's buttons were stripped on timeout


def test_cmd_send_skips_expired(monkeypatch, patched):
    # An expired card must not be re-sent the same day (idempotent guard).
    monkeypatch.setattr(
        wo.sched, "read_decision", lambda p: {"decision": "allow", "degraded": False}
    )
    monkeypatch.setattr(
        wo.sched,
        "_read_json",
        lambda p: {
            "date": "2026-06-15",
            "candidates": [_cand("NVDA", thesis_id="th_nvda_pvt_20260612_aaaa")],
        },
    )
    monkeypatch.setattr(
        wo.sched, "_list_theses", lambda: [_thesis("th_nvda_pvt_20260612_aaaa", "NVDA")]
    )
    # Seed the ledger with an already-expired entry for the same thesis.
    seeded = {
        "date": "2026-06-15",
        "mode": "paper",
        "orders": {
            "th_nvda_pvt_20260612_aaaa": _entry(
                thesis_id="th_nvda_pvt_20260612_aaaa", status="expired"
            )
        },
    }
    wo.save_ledger("2026-06-15", seeded)
    monkeypatch.setattr(
        wo.ti, "send_order_card", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no resend"))
    )
    assert wo.cmd_send(_Args()) == 0


# --------------------------------------------------------------------------- #
# Daemon: check_fills
# --------------------------------------------------------------------------- #
def test_check_fills_transitions_to_active(monkeypatch):
    monkeypatch.setattr(
        wo.pib,
        "order_fill_status",
        lambda port, oid: {"filled": True, "avg_price": 155.4, "status": "Filled"},
    )
    calls = []
    monkeypatch.setattr(
        wo,
        "transition_to_active",
        lambda tid, price, shares: calls.append((tid, price, shares)) or True,
    )
    ledger = {"orders": {"t1": _entry(status="placed", entry_order_id="100")}}
    changed = wo.check_fills(ledger, port=9000, bot_token="B")
    assert changed is True
    assert ledger["orders"]["t1"]["status"] == "filled"
    assert ledger["orders"]["t1"]["fill_price"] == 155.4
    assert calls == [("th_nvda_pvt_20260612_aaaa", 155.4, 10)]


def test_check_fills_skips_unfilled(monkeypatch):
    monkeypatch.setattr(
        wo.pib,
        "order_fill_status",
        lambda port, oid: {"filled": False, "avg_price": None, "status": "PreSubmitted"},
    )
    ledger = {"orders": {"t1": _entry(status="placed", entry_order_id="100")}}
    assert wo.check_fills(ledger, port=9000, bot_token="B") is False
    assert ledger["orders"]["t1"]["status"] == "placed"


def test_check_fills_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(
        wo.pib,
        "order_fill_status",
        lambda port, oid: {"filled": True, "avg_price": 100.0, "status": "Filled"},
    )
    monkeypatch.setattr(wo, "transition_to_active", lambda *a, **k: False)
    ledger = {"orders": {"t1": _entry(status="placed", entry_order_id="100")}}
    for _ in range(wo.MAX_FILL_TRANSITION_ATTEMPTS):
        wo.check_fills(ledger, port=9000, bot_token="B")
    assert ledger["orders"]["t1"]["status"] == "error"


# --------------------------------------------------------------------------- #
# Daemon: cmd_listen --once
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Daemon: +2R scale-out
# --------------------------------------------------------------------------- #
def _scale_entry(status="pending", **kw):
    e = {
        "kind": "scale",
        "thesis_id": "th_nvda_pvt_20260612_aaaa",
        "ticker": "NVDA",
        "side": "long",
        "shares": 50,
        "entry_price": 100.0,
        "current_price": 116.0,
        "message_id": None,
        "chat_id": None,
        "status": status,
        "sold_qty": None,
        "remaining_qty": None,
        "scale_order_ids": [],
        "error": None,
    }
    e.update(kw)
    return e


def test_handle_scale_out_preview_when_not_live(monkeypatch):
    monkeypatch.setattr(
        wo.pib,
        "place_market_close",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no order")),
    )
    entry = _scale_entry()
    wo.handle_scale_out(entry, port=9000, live=False, bot_token="B")
    assert entry["status"] == "preview"


def test_handle_scale_out_success(monkeypatch):
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    monkeypatch.setattr(wo.pib, "resolve_conid", lambda port, t: 265598)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU1")
    monkeypatch.setattr(wo.pib, "exit_action_for", lambda side: "SELL")
    closed = {}
    monkeypatch.setattr(
        wo.pib,
        "place_market_close",
        lambda port, acct, conid, action, qty: (
            closed.update(action=action, qty=qty) or {"ok": True, "order_ids": ["300"]}
        ),
    )
    cancelled = []
    monkeypatch.setattr(wo.pib, "working_exit_orders", lambda port, conid, ea: ["s1", "t1"])
    monkeypatch.setattr(wo.pib, "cancel_order", lambda port, acct, oid, **k: cancelled.append(oid))
    stopped = {}
    monkeypatch.setattr(
        wo.pib,
        "place_stop",
        lambda port, acct, conid, action, qty, sp, **k: (
            stopped.update(qty=qty, sp=sp) or {"ok": True, "order_ids": ["400"]}
        ),
    )
    trims = []
    monkeypatch.setattr(
        wo, "record_trim", lambda tid, qty, price: trims.append((tid, qty, price)) or True
    )

    entry = _scale_entry(message_id=5, chat_id=-1)
    edits = []
    monkeypatch.setattr(wo.ti, "edit_card", lambda *a, **k: edits.append(a) or {"ok": True})

    wo.handle_scale_out(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "scaled"
    assert entry["sold_qty"] == 25 and entry["remaining_qty"] == 25  # 50% of 50
    assert closed == {"action": "SELL", "qty": 25}
    assert cancelled == ["s1", "t1"]  # old bracket children torn down
    assert stopped == {"qty": 25, "sp": 100.0}  # breakeven stop on the remainder
    assert trims == [("th_nvda_pvt_20260612_aaaa", 25, 116.0)]  # trim at +2R price


def test_handle_scale_out_gateway_down(monkeypatch):
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    entry = _scale_entry()
    wo.handle_scale_out(entry, port=None, live=True, bot_token="B")
    assert entry["status"] == "error"


def test_handle_scale_out_mkt_rejected(monkeypatch):
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    monkeypatch.setattr(wo.pib, "resolve_conid", lambda port, t: 1)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU1")
    monkeypatch.setattr(
        wo.pib, "place_market_close", lambda *a, **k: {"ok": False, "order_ids": []}
    )
    monkeypatch.setattr(
        wo.pib,
        "working_exit_orders",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not reach")),
    )
    entry = _scale_entry()
    wo.handle_scale_out(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "error" and "rejected" in entry["error"]


def test_handle_scale_out_idempotent():
    entry = _scale_entry(status="scaled")
    wo.handle_scale_out(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "scaled"


def test_handle_scale_skip():
    entry = _scale_entry()
    wo.handle_scale_skip(entry, bot_token="B")
    assert entry["status"] == "skipped"


# --------------------------------------------------------------------------- #
# Producer: cmd_scale_card
# --------------------------------------------------------------------------- #
class _ScaleArgs:
    def __init__(self, **kw):
        self.date = "2026-06-15"
        self.thesis_id = "th_nvda_pvt_20260612_aaaa"
        self.ticker = "NVDA"
        self.side = "long"
        self.shares = 50.0
        self.entry = 100.0
        self.price = 116.0
        self.dry_run = False
        self.__dict__.update(kw)


def test_cmd_scale_card_sends_and_writes(monkeypatch, patched):
    sent = {}
    monkeypatch.setattr(
        wo.ti, "send_scale_card", lambda card, token, **k: sent.update(token=token) or 88
    )
    rc = wo.cmd_scale_card(_ScaleArgs())
    assert rc == 0 and sent["token"] == "2r-th_nvda_pvt_20260612_aaaa"
    led = wo.load_ledger("2026-06-15")
    e = led["orders"]["2r-th_nvda_pvt_20260612_aaaa"]
    assert e["kind"] == "scale" and e["status"] == "pending" and e["message_id"] == 88


def test_cmd_scale_card_idempotent(monkeypatch, patched):
    calls = []
    monkeypatch.setattr(wo.ti, "send_scale_card", lambda *a, **k: calls.append(1) or 88)
    wo.cmd_scale_card(_ScaleArgs())
    wo.cmd_scale_card(_ScaleArgs())  # same thesis same day -> no resend
    assert len(calls) == 1


def test_cmd_scale_card_dry_run(monkeypatch, patched):
    monkeypatch.setattr(
        wo.ti, "send_scale_card", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no send"))
    )
    assert wo.cmd_scale_card(_ScaleArgs(dry_run=True)) == 0
    assert not wo.ledger_path("2026-06-15").exists()


# --------------------------------------------------------------------------- #
# Daemon: position-management close (point 3)
# --------------------------------------------------------------------------- #
def _close_entry(status="pending", **kw):
    e = {
        "kind": "close",
        "thesis_id": "th_aapl_pvt_20260612_aaaa",
        "ticker": "AAPL",
        "side": "long",
        "shares": 100,
        "price": 96.0,
        "reason": "тайм-стоп наступил",
        "exit_reason": "time_stop",
        "message_id": None,
        "chat_id": None,
        "status": status,
        "close_order_ids": [],
        "error": None,
    }
    e.update(kw)
    return e


def test_handle_close_preview_when_not_live(monkeypatch):
    monkeypatch.setattr(
        wo.pib,
        "place_market_close",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no order")),
    )
    entry = _close_entry()
    wo.handle_close(entry, port=9000, live=False, bot_token="B")
    assert entry["status"] == "preview"


def test_handle_close_success(monkeypatch):
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    monkeypatch.setattr(wo.pib, "resolve_conid", lambda port, t: 1)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU1")
    monkeypatch.setattr(wo.pib, "exit_action_for", lambda side: "SELL")
    order = []
    cancelled = []
    monkeypatch.setattr(wo.pib, "working_exit_orders", lambda port, conid, ea: ["s1", "t1"])
    monkeypatch.setattr(wo.pib, "cancel_order", lambda port, acct, oid, **k: cancelled.append(oid))
    monkeypatch.setattr(
        wo.pib,
        "place_market_close",
        lambda port, acct, conid, action, qty: (
            order.append((action, qty)) or {"ok": True, "order_ids": ["500"]}
        ),
    )
    closed = []
    monkeypatch.setattr(
        wo, "record_close", lambda tid, price, reason: closed.append((tid, price, reason)) or True
    )
    pms = []
    monkeypatch.setattr(wo, "generate_postmortem", lambda tid: pms.append(tid) or True)
    edits = []
    entry = _close_entry(message_id=5, chat_id=-1)
    monkeypatch.setattr(wo.ti, "edit_card", lambda *a, **k: edits.append(a) or {"ok": True})

    wo.handle_close(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "closed"
    assert cancelled == ["s1", "t1"]  # protective legs cancelled FIRST
    assert order == [("SELL", 100)]  # full position closed at market
    assert closed == [("th_aapl_pvt_20260612_aaaa", 96.0, "time_stop")]
    # A confirmed close auto-generates the per-trade postmortem (variant A).
    assert pms == ["th_aapl_pvt_20260612_aaaa"]
    assert "Постмортем сохранён" in edits[-1][-1]


def test_handle_close_postmortem_failure_still_closes(monkeypatch):
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    monkeypatch.setattr(wo.pib, "resolve_conid", lambda port, t: 1)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU1")
    monkeypatch.setattr(wo.pib, "exit_action_for", lambda side: "SELL")
    monkeypatch.setattr(wo.pib, "working_exit_orders", lambda *a, **k: [])
    monkeypatch.setattr(
        wo.pib, "place_market_close", lambda *a, **k: {"ok": True, "order_ids": ["500"]}
    )
    monkeypatch.setattr(wo, "record_close", lambda *a, **k: True)
    monkeypatch.setattr(wo, "generate_postmortem", lambda tid: False)  # PM step fails
    edits = []
    monkeypatch.setattr(wo.ti, "edit_card", lambda *a, **k: edits.append(a) or {"ok": True})
    entry = _close_entry(message_id=5, chat_id=-1)

    wo.handle_close(entry, port=9000, live=True, bot_token="B")
    # The close still completes; the card flags that the PM needs a manual rerun.
    assert entry["status"] == "closed"
    assert "Постмортем не сгенерён" in edits[-1][-1]


def test_handle_close_gateway_down(monkeypatch):
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    entry = _close_entry()
    wo.handle_close(entry, port=None, live=True, bot_token="B")
    assert entry["status"] == "error"


def test_handle_close_mkt_rejected(monkeypatch):
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    monkeypatch.setattr(wo.pib, "resolve_conid", lambda port, t: 1)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU1")
    monkeypatch.setattr(wo.pib, "working_exit_orders", lambda *a, **k: [])
    monkeypatch.setattr(
        wo.pib, "place_market_close", lambda *a, **k: {"ok": False, "order_ids": []}
    )
    monkeypatch.setattr(
        wo, "record_close", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no record"))
    )
    entry = _close_entry()
    wo.handle_close(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "error" and "rejected" in entry["error"]


def test_handle_close_idempotent():
    entry = _close_entry(status="closed")
    wo.handle_close(entry, port=9000, live=True, bot_token="B")
    assert entry["status"] == "closed"


def test_handle_close_skip():
    entry = _close_entry()
    wo.handle_close_skip(entry, bot_token="B")
    assert entry["status"] == "skipped"


# --------------------------------------------------------------------------- #
# Daemon: detected external close (variant B safety-net)
# --------------------------------------------------------------------------- #
def _detected_entry(status="pending", **kw):
    e = {
        "kind": "close_detected",
        "thesis_id": "th_aapl_pvt_20260612_aaaa",
        "ticker": "AAPL",
        "side": "long",
        "shares": 100,
        "price": 96.0,
        "reason": "позиции нет в IB",
        "exit_reason": "manual",
        "message_id": 5,
        "chat_id": -1,
        "status": status,
        "close_order_ids": [],
        "error": None,
    }
    e.update(kw)
    return e


def test_handle_close_detected_records_without_order(monkeypatch):
    # Confirming a detected close must NEVER place a broker order.
    monkeypatch.setattr(
        wo.pib,
        "place_market_close",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no order on detected close")),
    )
    closed, pms = [], []
    monkeypatch.setattr(
        wo, "record_close", lambda tid, price, reason: closed.append((tid, price, reason)) or True
    )
    monkeypatch.setattr(wo, "generate_postmortem", lambda tid: pms.append(tid) or True)
    edits = []
    monkeypatch.setattr(wo.ti, "edit_card", lambda *a, **k: edits.append(a) or {"ok": True})
    entry = _detected_entry()

    wo.handle_close_detected(entry, bot_token="B")
    assert entry["status"] == "closed"
    assert closed == [("th_aapl_pvt_20260612_aaaa", 96.0, "manual")]
    assert pms == ["th_aapl_pvt_20260612_aaaa"]
    assert "Закрытие записано" in edits[-1][-1] and "Постмортем сохранён" in edits[-1][-1]


def test_handle_close_detected_record_failure(monkeypatch):
    monkeypatch.setattr(wo, "record_close", lambda *a, **k: False)
    monkeypatch.setattr(
        wo, "generate_postmortem", lambda tid: (_ for _ in ()).throw(AssertionError("no PM"))
    )
    edits = []
    monkeypatch.setattr(wo.ti, "edit_card", lambda *a, **k: edits.append(a) or {"ok": True})
    entry = _detected_entry()
    wo.handle_close_detected(entry, bot_token="B")
    assert entry["status"] == "error"
    assert "Не удалось записать закрытие" in edits[-1][-1]


def test_handle_close_detected_idempotent(monkeypatch):
    monkeypatch.setattr(
        wo, "record_close", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no re-record"))
    )
    entry = _detected_entry(status="closed")
    wo.handle_close_detected(entry, bot_token="B")
    assert entry["status"] == "closed"


def test_handle_close_detected_skip_message(monkeypatch):
    edits = []
    monkeypatch.setattr(wo.ti, "edit_card", lambda *a, **k: edits.append(a) or {"ok": True})
    entry = _detected_entry()
    wo.handle_close_skip(entry, bot_token="B")
    assert entry["status"] == "skipped"
    assert "Закрытие не записано" in edits[-1][-1]


class _DetectedArgs:
    def __init__(self, **kw):
        self.date = "2026-06-15"
        self.thesis_id = "th_aapl_pvt_20260612_aaaa"
        self.ticker = "AAPL"
        self.side = "long"
        self.shares = 100.0
        self.price = 96.0
        self.reason = "позиции нет в IB"
        self.exit_reason = "manual"
        self.dry_run = False
        self.__dict__.update(kw)


def test_cmd_close_detected_card_sends_and_writes(monkeypatch, patched):
    sent = {}
    monkeypatch.setattr(
        wo.ti, "send_close_detected_card", lambda card, token, **k: sent.update(token=token) or 91
    )
    rc = wo.cmd_close_detected_card(_DetectedArgs())
    assert rc == 0 and sent["token"] == "closed-th_aapl_pvt_20260612_aaaa"
    e = wo.load_ledger("2026-06-15")["orders"]["closed-th_aapl_pvt_20260612_aaaa"]
    assert e["kind"] == "close_detected" and e["status"] == "pending"


def test_cmd_close_detected_card_idempotent(monkeypatch, patched):
    calls = []
    monkeypatch.setattr(wo.ti, "send_close_detected_card", lambda *a, **k: calls.append(1) or 91)
    wo.cmd_close_detected_card(_DetectedArgs())
    wo.cmd_close_detected_card(_DetectedArgs())
    assert len(calls) == 1


def test_cmd_close_detected_card_suppressed_when_system_closed(monkeypatch, patched):
    # If the thesis was already closed via the system today, no detected card.
    ledger = {
        "date": "2026-06-15",
        "mode": "paper",
        "orders": {
            "close-th_aapl_pvt_20260612_aaaa": _close_entry(status="closed"),
        },
    }
    wo.save_ledger("2026-06-15", ledger)
    monkeypatch.setattr(
        wo.ti,
        "send_close_detected_card",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should be suppressed")),
    )
    rc = wo.cmd_close_detected_card(_DetectedArgs())
    assert rc == 0
    assert "closed-th_aapl_pvt_20260612_aaaa" not in wo.load_ledger("2026-06-15")["orders"]


class _CloseArgs:
    def __init__(self, **kw):
        self.date = "2026-06-15"
        self.thesis_id = "th_aapl_pvt_20260612_aaaa"
        self.ticker = "AAPL"
        self.side = "long"
        self.shares = 100.0
        self.price = 96.0
        self.reason = "тайм-стоп наступил"
        self.exit_reason = "time_stop"
        self.dry_run = False
        self.__dict__.update(kw)


def test_cmd_close_card_sends_and_writes(monkeypatch, patched):
    sent = {}
    monkeypatch.setattr(
        wo.ti, "send_close_card", lambda card, token, **k: sent.update(token=token) or 77
    )
    rc = wo.cmd_close_card(_CloseArgs())
    assert rc == 0 and sent["token"] == "close-th_aapl_pvt_20260612_aaaa"
    e = wo.load_ledger("2026-06-15")["orders"]["close-th_aapl_pvt_20260612_aaaa"]
    assert e["kind"] == "close" and e["status"] == "pending" and e["exit_reason"] == "time_stop"


def test_cmd_close_card_idempotent(monkeypatch, patched):
    calls = []
    monkeypatch.setattr(wo.ti, "send_close_card", lambda *a, **k: calls.append(1) or 77)
    wo.cmd_close_card(_CloseArgs())
    wo.cmd_close_card(_CloseArgs())
    assert len(calls) == 1


class _ListenArgs:
    def __init__(self, **kw):
        self.date = "2026-06-15"
        self.live = True
        self.window_sec = 1
        self.once = True
        self.__dict__.update(kw)


def test_cmd_listen_once_processes_open_tap(monkeypatch, patched):
    # Seed a pending ledger entry for the token the callback will reference.
    ledger = {
        "date": "2026-06-15",
        "mode": "paper",
        "orders": {"th_x_pvt_20260101_0001": _entry(thesis_id="th_x_pvt_20260101_0001")},
    }
    wo.save_ledger("2026-06-15", ledger)

    update = {
        "update_id": 7,
        "callback_query": {
            "id": "cq1",
            "data": "o:th_x_pvt_20260101_0001",
            "message": {"message_id": 1, "chat": {"id": -100}},
        },
    }
    monkeypatch.setattr(wo.ti, "poll_updates", lambda bot, off, timeout=25: [update])
    monkeypatch.setattr(wo.ti, "answer_callback", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(wo.pib, "connect", lambda *a, **k: 9000)
    opened = []
    monkeypatch.setattr(
        wo,
        "handle_open",
        lambda entry, port, *, live, bot_token: (
            opened.append((entry["thesis_id"], port, live)) or entry.update(status="placed")
        ),
    )

    rc = wo.cmd_listen(_ListenArgs())
    assert rc == 0
    assert opened and opened[0][0] == "th_x_pvt_20260101_0001" and opened[0][2] is True
    # offset advanced past the processed update
    assert wo.load_offset() == 8
    # ledger persisted the new status
    assert wo.load_ledger("2026-06-15")["orders"]["th_x_pvt_20260101_0001"]["status"] == "placed"


def test_cmd_listen_once_routes_scale_tap(monkeypatch, patched):
    # A tapped +2R card (kind="scale") routes to handle_scale_out, not handle_open.
    token = "2r-th_x_pvt_20260101_0001"
    ledger = {
        "date": "2026-06-15",
        "mode": "paper",
        "orders": {
            token: _scale_entry(thesis_id="th_x_pvt_20260101_0001", message_id=1, chat_id=-100)
        },
    }
    wo.save_ledger("2026-06-15", ledger)
    update = {
        "update_id": 11,
        "callback_query": {
            "id": "cq",
            "data": f"o:{token}",
            "message": {"message_id": 1, "chat": {"id": -100}},
        },
    }
    monkeypatch.setattr(wo.ti, "poll_updates", lambda *a, **k: [update])
    monkeypatch.setattr(wo.ti, "answer_callback", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(wo.pib, "connect", lambda *a, **k: 9000)
    routed = []
    monkeypatch.setattr(
        wo,
        "handle_scale_out",
        lambda entry, port, *, live, bot_token: (
            routed.append(entry["thesis_id"]) or entry.update(status="scaled")
        ),
    )
    monkeypatch.setattr(
        wo, "handle_open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrong route"))
    )

    rc = wo.cmd_listen(_ListenArgs())
    assert rc == 0 and routed == ["th_x_pvt_20260101_0001"]
    assert wo.load_ledger("2026-06-15")["orders"][token]["status"] == "scaled"


def test_cmd_listen_once_routes_close_tap(monkeypatch, patched):
    # A tapped close card (kind="close") routes to handle_close.
    token = "close-th_x_pvt_20260101_0001"
    ledger = {
        "date": "2026-06-15",
        "mode": "paper",
        "orders": {
            token: _close_entry(thesis_id="th_x_pvt_20260101_0001", message_id=1, chat_id=-100)
        },
    }
    wo.save_ledger("2026-06-15", ledger)
    update = {
        "update_id": 21,
        "callback_query": {
            "id": "cq",
            "data": f"o:{token}",
            "message": {"message_id": 1, "chat": {"id": -100}},
        },
    }
    monkeypatch.setattr(wo.ti, "poll_updates", lambda *a, **k: [update])
    monkeypatch.setattr(wo.ti, "answer_callback", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(wo.pib, "connect", lambda *a, **k: 9000)
    routed = []
    monkeypatch.setattr(
        wo,
        "handle_close",
        lambda entry, port, *, live, bot_token: (
            routed.append(entry["thesis_id"]) or entry.update(status="closed")
        ),
    )
    monkeypatch.setattr(
        wo, "handle_open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrong route"))
    )
    monkeypatch.setattr(
        wo, "handle_scale_out", lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrong route"))
    )

    rc = wo.cmd_listen(_ListenArgs())
    assert rc == 0 and routed == ["th_x_pvt_20260101_0001"]
    assert wo.load_ledger("2026-06-15")["orders"][token]["status"] == "closed"


def test_cmd_listen_once_routes_close_detected_tap(monkeypatch, patched):
    # A tapped detected-close card routes to handle_close_detected and NEVER
    # touches the Gateway (no _connect_port / handle_close).
    token = "closed-th_x_pvt_20260101_0001"
    ledger = {
        "date": "2026-06-15",
        "mode": "paper",
        "orders": {
            token: _detected_entry(thesis_id="th_x_pvt_20260101_0001", message_id=1, chat_id=-100)
        },
    }
    wo.save_ledger("2026-06-15", ledger)
    update = {
        "update_id": 31,
        "callback_query": {
            "id": "cq",
            "data": f"o:{token}",
            "message": {"message_id": 1, "chat": {"id": -100}},
        },
    }
    monkeypatch.setattr(wo.ti, "poll_updates", lambda *a, **k: [update])
    monkeypatch.setattr(wo.ti, "answer_callback", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        wo.pib, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no Gateway"))
    )
    routed = []
    monkeypatch.setattr(
        wo,
        "handle_close_detected",
        lambda entry, *, bot_token: (
            routed.append(entry["thesis_id"]) or entry.update(status="closed")
        ),
    )
    monkeypatch.setattr(
        wo, "handle_close", lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrong route"))
    )

    rc = wo.cmd_listen(_ListenArgs())
    assert rc == 0 and routed == ["th_x_pvt_20260101_0001"]
    assert wo.load_ledger("2026-06-15")["orders"][token]["status"] == "closed"


# --------------------------------------------------------------------------- #
# UI path (Telegram-free): open-now / cancel / sync
# --------------------------------------------------------------------------- #
_TID = "th_nvda_pvt_20260612_aaaa"


class _OpenArgs:
    def __init__(self, **kw):
        self.date = "2026-06-15"
        self.thesis_id = _TID
        self.ticker = "NVDA"
        self.side = "long"
        self.shares = 10.0
        self.pivot = 100.0
        self.stop = 95.0
        self.target = 110.0
        self.worst_entry = 101.0
        self.risk_dollars = 50.0
        self.live = False
        self.dry_run = False
        self.timeout = 20.0
        self.__dict__.update(kw)


class _CancelArgs:
    def __init__(self, **kw):
        self.date = "2026-06-15"
        self.thesis_id = _TID
        self.timeout = 20.0
        self.__dict__.update(kw)


class _SyncArgs:
    def __init__(self, **kw):
        self.date = "2026-06-15"
        self.timeout = 20.0
        self.__dict__.update(kw)


def _seed(tmp_path, **entry):
    ledger = wo.load_ledger("2026-06-15")
    ledger["orders"][_TID] = {"thesis_id": _TID, **entry}
    wo.save_ledger("2026-06-15", ledger)


# -- open-now ---------------------------------------------------------------- #
def test_open_now_preview_when_not_live(monkeypatch, patched):
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (False, "preview mode"))
    assert wo.cmd_open_now(_OpenArgs(live=False)) == 0
    entry = wo.load_ledger("2026-06-15")["orders"][_TID]
    assert entry["status"] == "preview"
    assert entry["message_id"] is None  # Telegram-free entry
    assert entry["coid"] == "wl-th_nvda_pvt_20260612_aaaa-2026-06-15"


def test_open_now_places_bracket_when_live(monkeypatch, patched):
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    monkeypatch.setattr(wo.pib, "connect", lambda timeout=20.0: 5000)
    monkeypatch.setattr(wo.pib, "live_order_refs", lambda port: set())
    monkeypatch.setattr(wo.pib, "resolve_conid", lambda port, ticker: 4815747)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU123")
    monkeypatch.setattr(wo.pib, "build_sub_brackets", lambda *a, **k: [[{"leg": 1}]])
    monkeypatch.setattr(
        wo.pib,
        "submit_brackets",
        lambda port, acct, brackets, *a, **k: {
            "ok": True,
            "order_ids": ["111", "112", "113"],
            "entry_order_id": "111",
            "entry_order_ids": ["111"],
        },
    )
    assert wo.cmd_open_now(_OpenArgs(live=True)) == 0
    entry = wo.load_ledger("2026-06-15")["orders"][_TID]
    assert entry["status"] == "placed"
    assert entry["entry_order_id"] == "111"
    assert entry["order_ids"] == ["111", "112", "113"]


def test_open_now_blocked_by_heat(monkeypatch, patched):
    monkeypatch.setattr(wo, "heat_ok_for", lambda card: (False, "нет слотов"))
    monkeypatch.setattr(
        wo.pib, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no connect"))
    )
    assert wo.cmd_open_now(_OpenArgs(live=True)) == 0
    assert wo.load_ledger("2026-06-15")["orders"][_TID]["status"] == "skipped"


def test_open_now_idempotent_when_already_placed(monkeypatch, patched):
    # No cOID on the seeded entry → can't confirm the broker → conservative no-op.
    _seed(patched, status="placed", order_ids=["111"])
    called = []
    monkeypatch.setattr(wo, "handle_open", lambda *a, **k: called.append(1))
    assert wo.cmd_open_now(_OpenArgs(live=True)) == 0
    assert called == []  # never re-places an already-placed bracket


def test_open_now_noop_when_bracket_still_live(monkeypatch, patched):
    # Ledger placed AND a live order still carries the cOID (its `-t1` sub-bracket)
    # → genuine no-op, no re-placement.
    coid = "wl-th_nvda_pvt_20260612_aaaa-2026-06-15"
    _seed(patched, status="placed", order_ids=["111"], coid=coid)
    monkeypatch.setattr(wo.pib, "connect", lambda timeout=20.0: 5000)
    monkeypatch.setattr(wo.pib, "live_order_refs", lambda port: {coid + "-t1"})
    monkeypatch.setattr(
        wo,
        "handle_open",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-place")),
    )
    assert wo.cmd_open_now(_OpenArgs(live=True)) == 0
    assert wo.load_ledger("2026-06-15")["orders"][_TID]["status"] == "placed"


def test_open_now_replaces_stale_placed_when_broker_empty(monkeypatch, patched):
    # Ledger says placed, but the bracket is gone at the broker (manually cleared)
    # → open-now re-validates, finds nothing live, and re-places.
    coid = "wl-th_nvda_pvt_20260612_aaaa-2026-06-15"
    _seed(patched, status="placed", order_ids=["111"], coid=coid)
    monkeypatch.setattr(wo.pib, "connect", lambda timeout=20.0: 5000)
    monkeypatch.setattr(wo.pib, "live_order_refs", lambda port: set())  # nothing live
    monkeypatch.setattr(wo, "heat_ok_for", lambda e: (True, "ok"))
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (True, "ok"))
    placed = []
    monkeypatch.setattr(
        wo,
        "handle_open",
        lambda entry, port, *, live, bot_token: (
            placed.append(1) or entry.update(status="placed", order_ids=["999"])
        ),
    )
    assert wo.cmd_open_now(_OpenArgs(live=True)) == 0
    assert placed == [1]  # re-placed after finding the broker empty
    led = wo.load_ledger("2026-06-15")["orders"][_TID]
    assert led["status"] == "placed" and led["order_ids"] == ["999"]


def test_open_now_incomplete_geometry_returns_1(patched):
    assert wo.cmd_open_now(_OpenArgs(target=None)) == 1


def test_open_now_auto_sizes_when_no_shares(monkeypatch, patched):
    # A signal-derived thesis: no shares supplied → size from the trading profile.
    monkeypatch.setattr(wo.sched, "_profile_sized_shares", lambda profile, pivot, stop: (7, 35.0))
    monkeypatch.setattr(wo.pib, "order_placement_status", lambda live: (False, "preview mode"))
    captured = {}
    monkeypatch.setattr(
        wo,
        "handle_open",
        lambda entry, port, *, live, bot_token: (
            captured.update(entry) or entry.update(status="preview")
        ),
    )
    assert wo.cmd_open_now(_OpenArgs(shares=None, risk_dollars=None)) == 0
    assert captured["shares"] == 7
    assert captured["risk_dollars"] == 35.0  # sized risk used when caller gives none


def test_open_now_errors_when_unsizable(monkeypatch, patched):
    # No shares and the profile can't size (no account / bad levels) → rc=1.
    monkeypatch.setattr(
        wo.sched, "_profile_sized_shares", lambda profile, pivot, stop: (None, None)
    )
    monkeypatch.setattr(
        wo, "handle_open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not place"))
    )
    assert wo.cmd_open_now(_OpenArgs(shares=None)) == 1


# -- cancel ------------------------------------------------------------------ #
def test_cancel_cancels_working_orders(monkeypatch, patched):
    _seed(patched, status="placed", order_ids=["111", "112", "113"])
    monkeypatch.setattr(wo.pib, "connect", lambda timeout=20.0: 5000)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU123")
    cancelled = []
    monkeypatch.setattr(
        wo.pib, "cancel_order", lambda port, acct, oid, *a, **k: cancelled.append(oid)
    )
    assert wo.cmd_cancel(_CancelArgs()) == 0
    assert set(cancelled) == {"111", "112", "113"}
    assert wo.load_ledger("2026-06-15")["orders"][_TID]["status"] == "cancelled"


def test_cancel_refuses_when_filled(monkeypatch, patched):
    _seed(patched, status="filled", order_ids=["111"])
    monkeypatch.setattr(
        wo.pib, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no connect"))
    )
    assert wo.cmd_cancel(_CancelArgs()) == 1  # use close, not cancel, once filled


def test_cancel_missing_entry_returns_1(patched):
    assert wo.cmd_cancel(_CancelArgs(thesis_id="th_absent_pvt_20260612_zzzz")) == 1


class _CancelOrdersArgs:
    def __init__(self, **kw):
        self.order_ids = "111,112,113"
        self.timeout = 20.0
        self.__dict__.update(kw)


def test_cancel_orders_by_id(monkeypatch):
    monkeypatch.setattr(wo.pib, "connect", lambda timeout=20.0: 5000)
    monkeypatch.setattr(wo.pib, "resolve_account_id", lambda port: "DU123")
    cancelled = []
    monkeypatch.setattr(
        wo.pib, "cancel_order", lambda port, acct, oid, *a, **k: cancelled.append(oid)
    )
    assert wo.cmd_cancel_orders(_CancelOrdersArgs()) == 0
    assert cancelled == ["111", "112", "113"]


def test_cancel_orders_empty_returns_1(monkeypatch):
    monkeypatch.setattr(
        wo.pib, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no connect"))
    )
    assert wo.cmd_cancel_orders(_CancelOrdersArgs(order_ids="")) == 1


# -- sync -------------------------------------------------------------------- #
def test_sync_transitions_filled_entry(monkeypatch, patched):
    _seed(
        patched,
        ticker="NVDA",
        status="placed",
        entry_order_id="111",
        pivot=100.0,
        shares=10,
        message_id=None,
        chat_id=None,
    )
    monkeypatch.setattr(wo.pib, "connect", lambda timeout=20.0: 5000)
    monkeypatch.setattr(
        wo.pib,
        "order_fill_status",
        lambda port, oid, *a, **k: {"status": "Filled", "filled": True, "avg_price": 100.5},
    )
    seen = []
    monkeypatch.setattr(
        wo,
        "transition_to_active",
        lambda tid, price, shares: seen.append((tid, price, shares)) or True,
    )
    assert wo.cmd_sync(_SyncArgs()) == 0
    assert seen == [(_TID, 100.5, 10)]
    entry = wo.load_ledger("2026-06-15")["orders"][_TID]
    assert entry["status"] == "filled" and entry["fill_price"] == 100.5


def test_sync_noop_when_no_placed(monkeypatch, patched):
    monkeypatch.setattr(
        wo.pib, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no connect"))
    )
    assert wo.cmd_sync(_SyncArgs()) == 0


def test_sync_unfilled_stays_placed(monkeypatch, patched):
    _seed(
        patched,
        ticker="NVDA",
        status="placed",
        entry_order_id="111",
        pivot=100.0,
        shares=10,
        message_id=None,
        chat_id=None,
    )
    monkeypatch.setattr(wo.pib, "connect", lambda timeout=20.0: 5000)
    monkeypatch.setattr(
        wo.pib,
        "order_fill_status",
        lambda port, oid, *a, **k: {"status": "Submitted", "filled": False, "avg_price": None},
    )
    assert wo.cmd_sync(_SyncArgs()) == 0
    assert wo.load_ledger("2026-06-15")["orders"][_TID]["status"] == "placed"
