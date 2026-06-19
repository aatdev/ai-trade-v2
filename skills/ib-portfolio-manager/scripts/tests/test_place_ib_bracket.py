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

    # Parent = BUY STP @ pivot, GTC by default, carries the cOID and no parentId.
    assert parent["side"] == "BUY"
    assert parent["orderType"] == "STP"
    assert parent["price"] == 155.23  # CP carries the STP trigger in `price`
    assert parent["tif"] == "GTC"
    assert parent["cOID"] == "wl-x-d"
    assert "parentId" not in parent

    # Children = SELL, GTC, reference the parent cOID, no cOID of their own.
    for leg in (stop_leg, target_leg):
        assert leg["side"] == "SELL"
        assert leg["tif"] == "GTC"
        assert leg["parentId"] == "wl-x-d"
        assert "cOID" not in leg
    assert stop_leg["orderType"] == "STP" and stop_leg["price"] == 150.0
    assert target_leg["orderType"] == "LMT" and target_leg["price"] == 167.68
    assert all(leg["conid"] == 265598 for leg in orders)


def test_short_bracket_is_mirrored():
    orders = pib.build_bracket_orders(
        "short", conid=1, shares=10, pivot=50.0, stop=53.0, target=44.0, coid="c"
    )
    parent, stop_leg, target_leg = orders
    assert parent["side"] == "SELL" and parent["orderType"] == "STP"
    assert stop_leg["side"] == "BUY" and stop_leg["price"] == 53.0
    assert target_leg["side"] == "BUY" and target_leg["price"] == 44.0


def test_entry_tif_defaults_gtc_and_day_is_honored():
    """Entry rests GTC by default (survives the close); DAY still selectable."""
    args = dict(side="long", conid=1, shares=10, pivot=100.0, stop=95.0, target=110.0)

    parent_default = pib.build_bracket_orders(coid="c", **args)[0]
    assert parent_default["tif"] == "GTC"

    parent_day = pib.build_bracket_orders(coid="c", entry_tif="DAY", **args)[0]
    assert parent_day["tif"] == "DAY"

    # build_sub_brackets forwards the same default down to every tranche's entry.
    sub_default = pib.build_sub_brackets(coid="c", **args)
    assert sub_default[0][0]["tif"] == "GTC"
    sub_day = pib.build_sub_brackets(coid="c", entry_tif="DAY", **args)
    assert sub_day[0][0]["tif"] == "DAY"


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


def test_coid_prefix_is_date_agnostic_and_anchors_coid_for():
    tid = "th_nvda_pvt_20260612_abc1"
    prefix = pib.coid_prefix(tid)
    assert prefix == "wl-th_nvda_pvt_20260612_abc1-"
    # coid_for for ANY date starts with the same prefix (so a GTC entry resting
    # from an earlier session is still matched by prefix detection).
    assert pib.coid_for(tid, "2026-06-12").startswith(prefix)
    assert pib.coid_for(tid, "2026-06-19").startswith(prefix)
    # The trailing "-" stops a shorter id from matching a longer sibling.
    assert not pib.coid_for("th_nvda_pvt_20260612_abc12", "2026-06-19").startswith(prefix)


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


def test_live_order_refs_skips_dead_statuses(monkeypatch):
    # Cancelled / Inactive / Rejected rows linger in the orders list but must NOT
    # count as live — else a torn-down bracket can never be re-placed. A Filled
    # entry still counts (a real position exists).
    monkeypatch.setattr(
        pib,
        "http_get_json",
        lambda *a, **k: {
            "orders": [
                {"order_ref": "wl-live-d", "status": "Submitted"},
                {"order_ref": "wl-cxl-d", "status": "Cancelled"},
                {"order_ref": "wl-inact-d", "status": "Inactive"},
                {"order_ref": "wl-rej-d", "status": "Rejected"},
                {"order_ref": "wl-filled-d", "status": "Filled"},
            ]
        },
    )
    assert pib.live_order_refs(9000) == {"wl-live-d", "wl-filled-d"}


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
    assert o["orderType"] == "STP" and o["price"] == 150.0 and o["tif"] == "GTC"


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
    assert out["sub_brackets"] == 1  # single target → one standalone bracket
    assert out["would_place"][0][0]["side"] == "BUY"  # first sub-bracket's parent


# --------------------------------------------------------------------------- #
# reject_reason — surface IB's actual rejection message
# --------------------------------------------------------------------------- #
def test_reject_reason_from_error_envelope():
    assert pib.reject_reason({"error": "Order rejected: insufficient margin"}) == (
        "Order rejected: insufficient margin"
    )


def test_reject_reason_from_list_messages():
    resp = [{"message": ["Stop price must be above the current price"]}]
    assert pib.reject_reason(resp) == "Stop price must be above the current price"


def test_reject_reason_from_order_status():
    assert pib.reject_reason([{"order_status": "Rejected"}]) == "order_status=Rejected"


def test_reject_reason_none_for_clean_terminal():
    assert pib.reject_reason([{"order_id": "123", "order_status": "Submitted"}]) is None


# --------------------------------------------------------------------------- #
# build_bracket_orders carries NO ocaGroup (a scale-out is N standalone brackets)
# --------------------------------------------------------------------------- #
def test_build_bracket_orders_has_no_oca_group():
    orders = pib.build_bracket_orders("long", 1, 100, 100, 95, 110, "wl-th-d")
    assert len(orders) == 3
    assert "ocaGroup" not in orders[1] and "ocaGroup" not in orders[2]


# --------------------------------------------------------------------------- #
# Multi-target scale-out: 50/25/25 split into N INDEPENDENT native sub-brackets
# --------------------------------------------------------------------------- #
def test_split_targets_3_rounding_and_floor():
    assert pib.split_targets_3(159) == (80, 40, 39)  # round(79.5)=80, round(39.75)=40, rem 39
    assert pib.split_targets_3(4) == (2, 1, 1)
    assert pib.split_targets_3(3) is None  # would leave a 0-share tranche
    assert pib.split_targets_3(2) is None


def test_sub_coid_is_per_tranche():
    assert pib.sub_coid("wl-th-d", 1) == "wl-th-d-t1"
    assert pib.sub_coid("wl-th-d", 3) == "wl-th-d-t3"


def test_attempt_nonce_is_short_hex():
    n = pib.attempt_nonce()
    assert n and all(c in "0123456789abcdef" for c in n)


def test_attempt_coid_keeps_base_as_prefix():
    # The per-attempt base (`{coid}-{nonce}`) must still start with the stable
    # coid_for() anchor so prefix detection / idempotency keep matching.
    base = pib.coid_for("th_x_pvt_20260618_aaaa", "2026-06-18")
    attempt = f"{base}-{pib.attempt_nonce()}"
    assert attempt.startswith(base) and attempt != base
    # ...and a tranche cOID built from it still starts with the anchor.
    assert pib.sub_coid(attempt, 1).startswith(base)


def test_build_sub_brackets_multi_target_structure():
    brackets = pib.build_sub_brackets(
        "long",
        conid=1,
        shares=100,
        pivot=100,
        stop=95,
        target=110,
        coid="wl-th-d",
        target2=120,
        target3=130,
    )
    # Three INDEPENDENT brackets, each a standalone parent + stop + take.
    assert len(brackets) == 3
    assert all(len(b) == 3 for b in brackets)
    parents = [b[0] for b in brackets]
    # Each parent enters at the pivot with a UNIQUE per-tranche cOID, no parentId.
    assert [p["cOID"] for p in parents] == ["wl-th-d-t1", "wl-th-d-t2", "wl-th-d-t3"]
    assert all(p["price"] == 100 and "parentId" not in p for p in parents)
    assert [p["quantity"] for p in parents] == [50, 25, 25]  # 50/25/25 split
    # Per bracket: stop + take share that bracket's cOID as parentId; no ocaGroup.
    for b, qty, tp in zip(brackets, (50, 25, 25), (110, 120, 130)):
        parent, stop_leg, take_leg = b
        assert stop_leg["orderType"] == "STP" and stop_leg["price"] == 95
        assert take_leg["orderType"] == "LMT" and take_leg["price"] == tp
        assert stop_leg["quantity"] == qty and take_leg["quantity"] == qty
        assert stop_leg["parentId"] == parent["cOID"] == take_leg["parentId"]
        assert "ocaGroup" not in stop_leg and "ocaGroup" not in take_leg
    # Full position is protected across the tranche stops.
    assert sum(b[1]["quantity"] for b in brackets) == 100


def test_build_sub_brackets_single_when_no_scale_targets():
    brackets = pib.build_sub_brackets("long", 1, 100, 100, 95, 110, "wl-th-d")
    assert len(brackets) == 1 and len(brackets[0]) == 3
    assert brackets[0][0]["cOID"] == "wl-th-d-t1"


def test_build_sub_brackets_falls_back_to_single_when_too_few_shares():
    brackets = pib.build_sub_brackets(
        "long", 1, 3, 100, 95, 110, "wl-th-d", target2=120, target3=130
    )
    assert len(brackets) == 1  # 3 shares can't split → single full-size bracket
    assert brackets[0][0]["quantity"] == 3


def test_build_sub_brackets_rejects_unordered_scale_targets():
    with pytest.raises(ValueError):
        pib.build_sub_brackets(
            "long", 1, 100, 100, 95, 110, "wl-th-d", target2=105, target3=130
        )  # T2 (105) < T1 (110) for a long


def test_build_sub_brackets_short_multi_target_mirrors():
    brackets = pib.build_sub_brackets(
        "short", 1, 100, 100, 105, 90, "wl-th-d", target2=85, target3=80
    )
    assert len(brackets) == 3
    takes = [b[2] for b in brackets]
    assert all(
        b[1]["side"] == "BUY" and b[2]["side"] == "BUY" for b in brackets
    )  # short exits = BUY
    assert [t["price"] for t in takes] == [90, 85, 80]  # T1>T2>T3 for a short


# --------------------------------------------------------------------------- #
# submit_brackets — N independent POSTs aggregated into one envelope
# --------------------------------------------------------------------------- #
def test_submit_brackets_aggregates_all_ids(monkeypatch):
    posts = []

    def fake_submit(port, account_id, orders, timeout=20.0):
        posts.append(orders)
        n = len(posts)
        return {
            "ok": True,
            "order_ids": [f"{n}00", f"{n}01", f"{n}02"],
            "entry_order_id": f"{n}00",
            "reason": None,
            "raw": [{"order_id": f"{n}00"}],
        }

    monkeypatch.setattr(pib, "submit_bracket", fake_submit)
    res = pib.submit_brackets(9000, "DU1", [[{"a": 1}], [{"b": 2}], [{"c": 3}]])
    assert res["ok"] is True
    assert len(posts) == 3  # one POST per sub-bracket
    assert res["order_ids"] == ["100", "101", "102", "200", "201", "202", "300", "301", "302"]
    assert res["entry_order_id"] == "100"  # first tranche's parent = fill anchor
    assert res["entry_order_ids"] == ["100", "200", "300"]


def test_submit_brackets_partial_failure_reports_placed_and_reason(monkeypatch):
    seq = iter(
        [
            {"ok": True, "order_ids": ["100"], "entry_order_id": "100", "reason": None, "raw": {}},
            {
                "ok": False,
                "order_ids": [],
                "entry_order_id": None,
                "reason": "price exceeds the Percentage constraint of 3%",
                "raw": {"error": "x"},
            },
        ]
    )
    monkeypatch.setattr(pib, "submit_bracket", lambda *a, **k: next(seq))
    res = pib.submit_brackets(9000, "DU1", [[{"a": 1}], [{"b": 2}]])
    assert res["ok"] is False
    assert res["order_ids"] == ["100"]  # the placed tranche is still reported
    assert "Percentage constraint" in res["reason"]
