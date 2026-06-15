"""Unit tests for place_ib_bracket.py (no network; pure builders + mocked HTTP)."""

import json

import place_ib_bracket as pib
import pytest


# --------------------------------------------------------------------------- #
# build_bracket_orders — geometry, cOID/parentId wiring, long/short mirror
# --------------------------------------------------------------------------- #
def test_long_bracket_shape_and_wiring():
    orders = pib.build_bracket_orders(
        "long", conid=265598, shares=50, pivot=155.23, stop=150.0, target=167.68, coid="wl-x-d"
    )
    assert len(orders) == 3
    parent, stop_leg, target_leg = orders

    # Parent = BUY STP @ pivot, DAY, carries the cOID and no parentId.
    assert parent["side"] == "BUY"
    assert parent["orderType"] == "STP"
    assert parent["auxPrice"] == 155.23
    assert parent["tif"] == "DAY"
    assert parent["cOID"] == "wl-x-d"
    assert "parentId" not in parent

    # Children = SELL, GTC, reference the parent cOID, no cOID of their own.
    for leg in (stop_leg, target_leg):
        assert leg["side"] == "SELL"
        assert leg["tif"] == "GTC"
        assert leg["parentId"] == "wl-x-d"
        assert "cOID" not in leg
    assert stop_leg["orderType"] == "STP" and stop_leg["auxPrice"] == 150.0
    assert target_leg["orderType"] == "LMT" and target_leg["price"] == 167.68
    assert all(leg["conid"] == 265598 for leg in orders)


def test_short_bracket_is_mirrored():
    orders = pib.build_bracket_orders(
        "short", conid=1, shares=10, pivot=50.0, stop=53.0, target=44.0, coid="c"
    )
    parent, stop_leg, target_leg = orders
    assert parent["side"] == "SELL" and parent["orderType"] == "STP"
    assert stop_leg["side"] == "BUY" and stop_leg["auxPrice"] == 53.0
    assert target_leg["side"] == "BUY" and target_leg["price"] == 44.0


@pytest.mark.parametrize(
    "side,pivot,stop,target",
    [
        ("long", 100, 110, 120),  # stop above pivot — invalid long
        ("long", 100, 90, 95),  # target below pivot — invalid long
        ("short", 50, 40, 45),  # stop below pivot — invalid short
    ],
)
def test_invalid_geometry_raises(side, pivot, stop, target):
    with pytest.raises(ValueError):
        pib.build_bracket_orders(
            side, conid=1, shares=10, pivot=pivot, stop=stop, target=target, coid="c"
        )


def test_bad_shares_and_side_raise():
    with pytest.raises(ValueError):
        pib.build_bracket_orders("long", conid=1, shares=0, pivot=10, stop=9, target=12, coid="c")
    with pytest.raises(ValueError):
        pib.build_bracket_orders(
            "sideways", conid=1, shares=1, pivot=10, stop=9, target=12, coid="c"
        )


def test_coid_for_is_deterministic():
    assert (
        pib.coid_for("th_nvda_pvt_20260612_abc1", "2026-06-15")
        == "wl-th_nvda_pvt_20260612_abc1-2026-06-15"
    )


# --------------------------------------------------------------------------- #
# Two-lock safety guard
# --------------------------------------------------------------------------- #
def test_placement_locked_without_live_flag(monkeypatch):
    monkeypatch.setenv("IB_ALLOW_ORDER_PLACEMENT", "true")
    allowed, reason = pib.order_placement_status(live_flag=False)
    assert allowed is False and "preview" in reason


def test_placement_locked_without_env(monkeypatch):
    monkeypatch.delenv("IB_ALLOW_ORDER_PLACEMENT", raising=False)
    allowed, reason = pib.order_placement_status(live_flag=True)
    assert allowed is False and "IB_ALLOW_ORDER_PLACEMENT" in reason


def test_placement_allowed_with_both(monkeypatch):
    monkeypatch.setenv("IB_ALLOW_ORDER_PLACEMENT", "true")
    allowed, _ = pib.order_placement_status(live_flag=True)
    assert allowed is True


def test_mode_badge_reflects_paper(monkeypatch):
    monkeypatch.delenv("IB_PAPER_TRADING", raising=False)
    assert "PAPER" in pib.mode_badge()
    monkeypatch.setenv("IB_PAPER_TRADING", "false")
    assert "LIVE" in pib.mode_badge()


# --------------------------------------------------------------------------- #
# conid + account resolution (mocked HTTP)
# --------------------------------------------------------------------------- #
def test_resolve_conid_prefers_stock_row(monkeypatch):
    monkeypatch.setattr(
        pib,
        "http_get_json",
        lambda port, path, timeout=20.0: [
            {"conid": 111, "secType": "OPT"},
            {"conid": 222, "secType": "STK"},
        ],
    )
    assert pib.resolve_conid(9000, "NVDA") == 222


def test_resolve_conid_no_match_raises(monkeypatch):
    monkeypatch.setattr(pib, "http_get_json", lambda *a, **k: [])
    with pytest.raises(LookupError):
        pib.resolve_conid(9000, "ZZZZ")


def test_resolve_account_id_first(monkeypatch):
    monkeypatch.setattr(pib, "http_get_json", lambda *a, **k: [{"id": "DU111"}, {"id": "DU222"}])
    assert pib.resolve_account_id(9000) == "DU111"


# --------------------------------------------------------------------------- #
# Confirmation reply loop + submission
# --------------------------------------------------------------------------- #
def test_place_with_confirmations_walks_reply_chain(monkeypatch):
    calls = []

    def fake_post(port, path, body, timeout=20.0):
        calls.append(path)
        if path.endswith("/orders"):
            return [{"id": "reply-1", "message": ["size warning"], "messageIds": ["o1"]}]
        if path == "/iserver/reply/reply-1":
            return [{"id": "reply-2", "message": ["precaution"], "messageIds": ["o2"]}]
        if path == "/iserver/reply/reply-2":
            return [{"order_id": "100", "order_status": "Submitted"}]
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(pib, "http_post_json", fake_post)
    resp = pib.place_with_confirmations(9000, "DU1", {"orders": []})
    assert resp == [{"order_id": "100", "order_status": "Submitted"}]
    assert calls == [
        "/iserver/account/DU1/orders",
        "/iserver/reply/reply-1",
        "/iserver/reply/reply-2",
    ]


def test_place_with_confirmations_stops_on_error_envelope(monkeypatch):
    monkeypatch.setattr(pib, "http_post_json", lambda *a, **k: {"error": "no such contract"})
    resp = pib.place_with_confirmations(9000, "DU1", {"orders": []})
    assert resp == {"error": "no such contract"}


def test_place_with_confirmations_bounded(monkeypatch):
    # Always return a fresh confirmation — must stop after max_replies, not loop forever.
    monkeypatch.setattr(
        pib,
        "http_post_json",
        lambda *a, **k: [{"id": "r", "message": ["w"], "messageIds": []}],
    )
    resp = pib.place_with_confirmations(9000, "DU1", {"orders": []}, max_replies=3)
    assert not pib._is_terminal_order_response(resp)


def test_submit_bracket_normalizes(monkeypatch):
    monkeypatch.setattr(
        pib,
        "place_with_confirmations",
        lambda *a, **k: [{"order_id": "100", "order_status": "Submitted"}, {"order_id": "101"}],
    )
    res = pib.submit_bracket(9000, "DU1", [{"x": 1}])
    assert res["ok"] is True
    assert res["order_ids"] == ["100", "101"]
    assert res["entry_order_id"] == "100"


def test_submit_bracket_failure(monkeypatch):
    monkeypatch.setattr(pib, "place_with_confirmations", lambda *a, **k: {"error": "rejected"})
    res = pib.submit_bracket(9000, "DU1", [{"x": 1}])
    assert res["ok"] is False and res["order_ids"] == []


# --------------------------------------------------------------------------- #
# Fill detection
# --------------------------------------------------------------------------- #
def test_order_fill_status_detects_fill(monkeypatch):
    monkeypatch.setattr(
        pib,
        "http_get_json",
        lambda *a, **k: {"orders": [{"orderId": "100", "status": "Filled", "avgPrice": "155.40"}]},
    )
    st = pib.order_fill_status(9000, "100")
    assert st["filled"] is True and st["avg_price"] == 155.40


def test_order_fill_status_pending(monkeypatch):
    monkeypatch.setattr(
        pib,
        "http_get_json",
        lambda *a, **k: [{"orderId": "100", "status": "PreSubmitted"}],
    )
    st = pib.order_fill_status(9000, "100")
    assert st["filled"] is False


def test_order_fill_status_missing_order(monkeypatch):
    monkeypatch.setattr(pib, "http_get_json", lambda *a, **k: {"orders": []})
    st = pib.order_fill_status(9000, "999")
    assert st["status"] is None and st["filled"] is False


def test_live_order_refs_collects_coid(monkeypatch):
    monkeypatch.setattr(
        pib,
        "http_get_json",
        lambda *a, **k: {"orders": [{"order_ref": "wl-a-d"}, {"cOID": "wl-b-d"}, {}]},
    )
    assert pib.live_order_refs(9000) == {"wl-a-d", "wl-b-d"}


# --------------------------------------------------------------------------- #
# Scale-out / close helpers (+2R)
# --------------------------------------------------------------------------- #
def test_exit_action_for():
    assert pib.exit_action_for("long") == "SELL"
    assert pib.exit_action_for("short") == "BUY"
    assert pib.exit_action_for(None) == "SELL"


def test_place_market_close_builds_mkt(monkeypatch):
    seen = {}

    def fake(port, account_id, body, timeout=20.0):
        seen["body"] = body
        return [{"order_id": "200", "order_status": "Submitted"}]

    monkeypatch.setattr(pib, "place_with_confirmations", fake)
    res = pib.place_market_close(9000, "DU1", 265598, "SELL", 25)
    assert res["ok"] is True and res["order_ids"] == ["200"]
    o = seen["body"]["orders"][0]
    assert o["orderType"] == "MKT" and o["side"] == "SELL" and o["quantity"] == 25


def test_place_stop_builds_stp(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        pib,
        "place_with_confirmations",
        lambda port, acct, body, timeout=20.0: seen.update(body=body) or [{"order_id": "201"}],
    )
    res = pib.place_stop(9000, "DU1", 1, "SELL", 25, 150.0)
    assert res["ok"] is True
    o = seen["body"]["orders"][0]
    assert o["orderType"] == "STP" and o["auxPrice"] == 150.0 and o["tif"] == "GTC"


def test_cancel_order_calls_delete(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        pib,
        "http_delete_json",
        lambda port, path, timeout=20.0: seen.update(path=path) or {"msg": "ok"},
    )
    pib.cancel_order(9000, "DU1", "201")
    assert seen["path"] == "/iserver/account/DU1/order/201"


def test_working_exit_orders_filters_by_conid_side_status(monkeypatch):
    monkeypatch.setattr(
        pib,
        "http_get_json",
        lambda *a, **k: {
            "orders": [
                {"orderId": "1", "conid": 100, "side": "SELL", "status": "Submitted"},  # keep
                {"orderId": "2", "conid": 100, "side": "SELL", "status": "Filled"},  # done -> skip
                {"orderId": "3", "conid": 100, "side": "BUY", "status": "Submitted"},  # wrong side
                {
                    "orderId": "4",
                    "conid": 999,
                    "side": "SELL",
                    "status": "PreSubmitted",
                },  # wrong conid
                {"orderId": "5", "conid": 100, "side": "SELL", "status": "PreSubmitted"},  # keep
            ]
        },
    )
    assert pib.working_exit_orders(9000, 100, "SELL") == ["1", "5"]


# --------------------------------------------------------------------------- #
# CLI preview path posts nothing
# --------------------------------------------------------------------------- #
def test_cli_preview_does_not_post(monkeypatch, capsys):
    monkeypatch.delenv("IB_ALLOW_ORDER_PLACEMENT", raising=False)

    def boom(*a, **k):
        raise AssertionError("preview must not POST")

    monkeypatch.setattr(pib, "http_post_json", boom)
    monkeypatch.setattr(
        pib, "connect", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("no gw"))
    )
    rc = pib.main(
        [
            "--ticker",
            "NVDA",
            "--side",
            "long",
            "--shares",
            "50",
            "--pivot",
            "155.23",
            "--stop",
            "150",
            "--target",
            "167.68",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "preview"
    assert out["would_place"][0]["side"] == "BUY"
