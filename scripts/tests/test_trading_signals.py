"""Tests for scripts/lib/trading_signals.py (auto-mode signal engine)."""

import datetime as dt
import importlib.util
import json
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "lib" / "trading_signals.py"
_spec = importlib.util.spec_from_file_location("trading_signals", _MODULE_PATH)
sig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sig)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def make_plan(**overrides):
    """Minimal breakout_trade_plan JSON in the planner's real shape."""
    plan = {
        "schema_version": "1.0",
        "summary": {"actionable_count": 1, "revalidation_count": 1},
        "actionable_orders": [
            {
                "symbol": "NVDA",
                "sector": "Technology",
                "composite_score": 78.5,
                "execution_state": "Pre-breakout",
                "plan_type": "pending_breakout",
                "trade_plan": {
                    "signal_entry": 155.20,
                    "worst_entry": 157.50,
                    "stop_loss_price": 151.30,
                    "target_price": 163.70,
                    "shares": 380,
                    "risk_dollars": 2356.0,
                },
            }
        ],
        "revalidation": [
            {
                "symbol": "AMD",
                "plan_type": "late_breakout_revalidation",
                "pivot": 150.0,
                "current_price": 152.5,
                "max_entry_price": 153.0,
            }
        ],
    }
    plan.update(overrides)
    return plan


def make_short_candidates():
    return [
        {
            "symbol": "NFLX",
            "sector": "Communication Services",
            "composite_score": 82.5,
            "grade": "A",
            "trade_levels": {"entry": 245.0, "stop": 260.0, "stop_pct": 6.1, "target_2r": 215.0},
            "metrics": {"price": 245.3},
        }
    ]


def make_watchlist(candidates):
    return {
        "workflow": "swing-opportunity-daily",
        "date": "2026-06-11",
        "exposure_decision": "allow",
        "candidates": candidates,
    }


def long_candidate(**overrides):
    c = {
        "ticker": "NVDA",
        "side": "long",
        "setup": "VCP Pre-breakout",
        "pivot": 155.20,
        "worst_entry": 157.50,
        "stop": 151.30,
        "target": 163.70,
        "shares": 380,
        "risk_dollars": 2356.0,
        "score": 78.5,
        "validated": True,
    }
    c.update(overrides)
    return c


def short_candidate(**overrides):
    c = {
        "ticker": "NFLX",
        "side": "short",
        "setup": "Stage 4 (grade A)",
        "pivot": 245.0,
        "worst_entry": 240.1,
        "stop": 260.0,
        "target": 215.0,
        "shares": 100,
        "risk_dollars": 1500.0,
        "score": 82.5,
        "validated": None,
    }
    c.update(overrides)
    return c


def make_heat(**overrides):
    heat = {
        "account_size": 150000.0,
        "open_risk_pct": 0.0,
        "remaining_heat_pct": 6.0,
        "remaining_heat_dollars": 9000.0,
        "remaining_position_slots": 6,
        "max_portfolio_heat_pct": 6.0,
        "positions": [],
    }
    heat.update(overrides)
    return heat


def long_position(**overrides):
    p = {
        "thesis_id": "th_aapl_pvt_20260602_2c8b",
        "ticker": "AAPL",
        "entry_price": 100.0,
        "shares": 100,
        "stop_loss": 95.0,
    }
    p.update(overrides)
    return p


def short_position(**overrides):
    p = {
        "thesis_id": "th_nflx_short_20260605_aa11",
        "ticker": "NFLX",
        "entry_price": 245.0,
        "shares": 100,
        "stop_loss": 260.0,  # stop above entry -> short
    }
    p.update(overrides)
    return p


def _types(signals):
    return [(s["ticker"], s["type"]) for s in signals]


# --------------------------------------------------------------------------- #
# size_short
# --------------------------------------------------------------------------- #
class TestSizeShort:
    def test_basic_formula(self):
        # $150k * 1% = $1500 risk; stop-entry = 3 -> 500 shares
        assert sig.size_short(150000, entry=50.0, stop=53.0) == 500

    def test_capped_by_max_position_pct(self):
        # 1% of 150k / 1.0 = 1500 shares -> $300k position > 25% cap ($37.5k -> 187 shares)
        assert sig.size_short(150000, entry=200.0, stop=201.0) == 187

    def test_invalid_stop_returns_zero(self):
        assert sig.size_short(150000, entry=50.0, stop=50.0) == 0
        assert sig.size_short(150000, entry=50.0, stop=49.0) == 0


# --------------------------------------------------------------------------- #
# build_watchlist
# --------------------------------------------------------------------------- #
class TestBuildWatchlist:
    def test_actionable_orders_become_long_candidates(self):
        wl = sig.build_watchlist("2026-06-11", "allow", make_plan(), None, None)
        nvda = [c for c in wl["candidates"] if c["ticker"] == "NVDA"][0]
        assert nvda["side"] == "long"
        assert nvda["pivot"] == 155.20
        assert nvda["worst_entry"] == 157.50
        assert nvda["stop"] == 151.30
        assert nvda["target"] == 163.70
        assert nvda["shares"] == 380
        assert nvda["validated"] is None

    def test_revalidation_included_without_shares(self):
        wl = sig.build_watchlist("2026-06-11", "allow", make_plan(), None, None)
        amd = [c for c in wl["candidates"] if c["ticker"] == "AMD"][0]
        assert amd["shares"] is None
        assert amd["pivot"] == 150.0
        assert amd["worst_entry"] == 153.0

    def test_short_candidates_sized_at_one_percent(self):
        wl = sig.build_watchlist(
            "2026-06-11",
            "restrict",
            None,
            make_short_candidates(),
            None,
            account_size=150000,
        )
        nflx = wl["candidates"][0]
        assert nflx["side"] == "short"
        assert nflx["pivot"] == 245.0
        assert nflx["stop"] == 260.0
        assert nflx["target"] == 215.0
        # Default fallback 1% of 150k = 1500 / (260-245) = 100 shares
        assert nflx["shares"] == 100

    def test_short_risk_pct_follows_profile_budget(self):
        # Profile risk 0.33% must size shorts at the same budget as longs, not
        # the module's 1% fallback: 0.33% of 150k = $495 / (260-245) = 33 shares.
        wl = sig.build_watchlist(
            "2026-06-11",
            "restrict",
            None,
            make_short_candidates(),
            None,
            account_size=150000,
            short_risk_pct=0.33,
        )
        nflx = wl["candidates"][0]
        assert nflx["shares"] == 33
        assert nflx["risk_dollars"] == round(33 * (260.0 - 245.0), 2)
        assert nflx["risk_dollars"] <= 150000 * 0.33 / 100

    def test_short_max_position_pct_from_profile_caps_shares(self):
        # A tight stop would otherwise size a huge notional; the position cap
        # (threaded from the profile) must bind.
        wl = sig.build_watchlist(
            "2026-06-11",
            "restrict",
            None,
            [
                {
                    "symbol": "TGT",
                    "grade": "A",
                    "trade_levels": {"entry": 200.0, "stop": 201.0, "target_2r": 194.0},
                }
            ],
            None,
            account_size=150000,
            short_risk_pct=0.33,
            short_max_position_pct=25.0,
        )
        tgt = wl["candidates"][0]
        # 25% of 150k / 200 = 187 shares cap binds before the risk-based count.
        assert tgt["shares"] == 187

    def test_validation_reject_drops_candidate(self):
        validation = {
            "verdicts": [{"ticker": "NVDA", "verdict": "reject", "note": "broken weekly structure"}]
        }
        wl = sig.build_watchlist("2026-06-11", "allow", make_plan(), None, validation)
        tickers = [c["ticker"] for c in wl["candidates"]]
        assert "NVDA" not in tickers
        assert wl["rejected_by_validation"][0]["ticker"] == "NVDA"

    def test_validation_pass_marks_validated(self):
        validation = {"verdicts": [{"ticker": "NVDA", "verdict": "pass", "note": "clean base"}]}
        wl = sig.build_watchlist("2026-06-11", "allow", make_plan(), None, validation)
        nvda = [c for c in wl["candidates"] if c["ticker"] == "NVDA"][0]
        assert nvda["validated"] is True
        assert nvda["validation_note"] == "clean base"

    def test_schema_top_level(self):
        wl = sig.build_watchlist("2026-06-11", "allow", make_plan(), None, None, notes="n")
        assert wl["workflow"] == "swing-opportunity-daily"
        assert wl["date"] == "2026-06-11"
        assert wl["exposure_decision"] == "allow"
        assert wl["notes"] == "n"

    def test_empty_inputs_give_empty_candidates(self):
        wl = sig.build_watchlist("2026-06-11", "restrict", None, None, None)
        assert wl["candidates"] == []


# --------------------------------------------------------------------------- #
# evaluate_signals — opening
# --------------------------------------------------------------------------- #
class TestOpenSignals:
    def test_open_long_fires_between_pivot_and_worst_entry(self):
        wl = make_watchlist([long_candidate()])
        signals = sig.evaluate_signals(wl, make_heat(), {"NVDA": {"price": 156.0}}, "allow", set())
        assert _types(signals) == [("NVDA", "OPEN_LONG")]
        assert signals[0]["price"] == 156.0
        assert signals[0]["candidate"]["shares"] == 380

    def test_no_open_long_below_pivot(self):
        wl = make_watchlist([long_candidate()])
        signals = sig.evaluate_signals(wl, make_heat(), {"NVDA": {"price": 154.0}}, "allow", set())
        assert signals == []

    def test_armed_ticker_suppresses_open_signal(self):
        # A bracket already placed via the watchlist-order daemon must not also
        # surface an OPEN_LONG (the trader would otherwise re-place it manually).
        wl = make_watchlist([long_candidate()])
        signals = sig.evaluate_signals(
            wl, make_heat(), {"NVDA": {"price": 156.0}}, "allow", set(), armed_tickers={"NVDA"}
        )
        assert signals == []

    def test_missed_above_worst_entry(self):
        wl = make_watchlist([long_candidate()])
        signals = sig.evaluate_signals(wl, make_heat(), {"NVDA": {"price": 160.0}}, "allow", set())
        assert _types(signals) == [("NVDA", "MISSED")]

    def test_no_open_long_when_gate_not_allow(self):
        wl = make_watchlist([long_candidate()])
        signals = sig.evaluate_signals(
            wl, make_heat(), {"NVDA": {"price": 156.0}}, "restrict", set()
        )
        assert signals == []

    def test_open_short_fires_when_gate_restrict(self):
        wl = make_watchlist([short_candidate()])
        signals = sig.evaluate_signals(
            wl, make_heat(), {"NFLX": {"price": 244.0}}, "restrict", set()
        )
        assert _types(signals) == [("NFLX", "OPEN_SHORT")]

    def test_short_suppressed_when_gate_allow(self):
        wl = make_watchlist([short_candidate()])
        signals = sig.evaluate_signals(wl, make_heat(), {"NFLX": {"price": 244.0}}, "allow", set())
        assert signals == []

    def test_short_missed_below_chase_band(self):
        wl = make_watchlist([short_candidate()])
        signals = sig.evaluate_signals(
            wl, make_heat(), {"NFLX": {"price": 230.0}}, "cash-priority", set()
        )
        assert _types(signals) == [("NFLX", "MISSED")]

    def test_open_short_suppressed_when_shorts_disabled(self):
        # Short trading opt-in: an in-band short candidate is NOT armed when
        # allow_shorts=False, even under a genuine restrict gate.
        wl = make_watchlist([short_candidate()])
        signals = sig.evaluate_signals(
            wl, make_heat(), {"NFLX": {"price": 244.0}}, "restrict", set(), allow_shorts=False
        )
        assert signals == []

    def test_disabling_shorts_leaves_longs_untouched(self):
        # The flag gates only new short risk — a valid long still arms.
        wl = make_watchlist([long_candidate()])
        signals = sig.evaluate_signals(
            wl, make_heat(), {"NVDA": {"price": 156.0}}, "allow", set(), allow_shorts=False
        )
        assert _types(signals) == [("NVDA", "OPEN_LONG")]

    def test_open_short_still_manages_position_when_shorts_disabled(self):
        # allow_shorts=False must NOT stop managing an already-open short.
        heat = make_heat(positions=[short_position()])
        signals = sig.evaluate_signals(
            None, heat, {"NFLX": {"price": 261.0}}, "restrict", set(), allow_shorts=False
        )
        assert _types(signals) == [("NFLX", "STOP_HIT")]

    def test_no_capacity_slots_emits_skipped(self):
        wl = make_watchlist([long_candidate()])
        heat = make_heat(remaining_position_slots=0)
        signals = sig.evaluate_signals(wl, heat, {"NVDA": {"price": 156.0}}, "allow", set())
        assert _types(signals) == [("NVDA", "SKIPPED_CAPACITY")]

    def test_unsized_candidate_reserves_default_risk_budget(self):
        # A revalidation advisory (shares/risk None) must not pass the heat
        # gate for free: it reserves a full 1% budget (150k × 1% = 1500 > 1000).
        cand = long_candidate(shares=None, risk_dollars=None)
        heat = make_heat(remaining_heat_dollars=1000.0)
        wl = make_watchlist([cand])
        signals = sig.evaluate_signals(wl, heat, {"NVDA": {"price": 156.0}}, "allow", set())
        assert _types(signals) == [("NVDA", "SKIPPED_CAPACITY")]

    def test_unsized_candidate_opens_when_budget_allows(self):
        cand = long_candidate(shares=None, risk_dollars=None)
        heat = make_heat(remaining_heat_dollars=9000.0)
        wl = make_watchlist([cand])
        signals = sig.evaluate_signals(wl, heat, {"NVDA": {"price": 156.0}}, "allow", set())
        assert _types(signals) == [("NVDA", "OPEN_LONG")]

    def test_no_heat_budget_emits_skipped(self):
        wl = make_watchlist([long_candidate()])
        heat = make_heat(remaining_heat_dollars=1000.0)  # candidate risks 2356
        signals = sig.evaluate_signals(wl, heat, {"NVDA": {"price": 156.0}}, "allow", set())
        assert _types(signals) == [("NVDA", "SKIPPED_CAPACITY")]

    def test_slots_consumed_in_score_order(self):
        a = long_candidate(ticker="AAA", score=90.0)
        b = long_candidate(ticker="BBB", score=50.0)
        wl = make_watchlist([b, a])
        heat = make_heat(remaining_position_slots=1)
        quotes = {"AAA": {"price": 156.0}, "BBB": {"price": 156.0}}
        signals = sig.evaluate_signals(wl, heat, quotes, "allow", set())
        assert ("AAA", "OPEN_LONG") in _types(signals)
        assert ("BBB", "SKIPPED_CAPACITY") in _types(signals)

    def test_no_open_for_already_open_position(self):
        wl = make_watchlist([long_candidate(ticker="AAPL")])
        heat = make_heat(positions=[long_position()])
        signals = sig.evaluate_signals(wl, heat, {"AAPL": {"price": 156.0}}, "allow", set())
        assert ("AAPL", "OPEN_LONG") not in _types(signals)

    def test_suppress_opens_blocks_open_long(self):
        # A degraded gate must not arm new longs even when the gate reads "allow".
        wl = make_watchlist([long_candidate()])
        signals = sig.evaluate_signals(
            wl, make_heat(), {"NVDA": {"price": 156.0}}, "allow", set(), suppress_opens=True
        )
        assert signals == []

    def test_suppress_opens_blocks_open_short(self):
        wl = make_watchlist([short_candidate()])
        signals = sig.evaluate_signals(
            wl, make_heat(), {"NFLX": {"price": 244.0}}, "restrict", set(), suppress_opens=True
        )
        assert signals == []

    def test_suppress_opens_still_manages_open_positions(self):
        # STOP_HIT on an open position must still fire under a degraded gate.
        wl = make_watchlist([long_candidate(ticker="AAPL")])
        heat = make_heat(positions=[long_position()])
        signals = sig.evaluate_signals(
            wl, heat, {"AAPL": {"price": 94.5}}, "restrict", set(), suppress_opens=True
        )
        assert _types(signals) == [("AAPL", "STOP_HIT")]

    def test_missing_quote_is_silent(self):
        wl = make_watchlist([long_candidate()])
        assert sig.evaluate_signals(wl, make_heat(), {}, "allow", set()) == []


# --------------------------------------------------------------------------- #
# evaluate_signals — managing open positions
# --------------------------------------------------------------------------- #
class TestManageSignals:
    def test_stop_hit_long(self):
        heat = make_heat(positions=[long_position()])
        signals = sig.evaluate_signals(None, heat, {"AAPL": {"price": 94.5}}, "allow", set())
        assert _types(signals) == [("AAPL", "STOP_HIT")]

    def test_near_stop_long(self):
        heat = make_heat(positions=[long_position()])
        # stop 95, +1% band -> 95.95
        signals = sig.evaluate_signals(None, heat, {"AAPL": {"price": 95.5}}, "allow", set())
        assert _types(signals) == [("AAPL", "NEAR_STOP")]

    def test_stop_hit_suppresses_near_stop(self):
        heat = make_heat(positions=[long_position()])
        signals = sig.evaluate_signals(None, heat, {"AAPL": {"price": 95.0}}, "allow", set())
        assert _types(signals) == [("AAPL", "STOP_HIT")]

    def test_two_r_long(self):
        heat = make_heat(positions=[long_position()])
        # risk = 5 -> 2R at 110
        signals = sig.evaluate_signals(None, heat, {"AAPL": {"price": 110.0}}, "allow", set())
        assert _types(signals) == [("AAPL", "TWO_R")]

    def test_quiet_zone_no_signal(self):
        heat = make_heat(positions=[long_position()])
        signals = sig.evaluate_signals(None, heat, {"AAPL": {"price": 100.0}}, "allow", set())
        assert signals == []

    def test_stop_hit_short(self):
        heat = make_heat(positions=[short_position()])
        signals = sig.evaluate_signals(None, heat, {"NFLX": {"price": 261.0}}, "restrict", set())
        assert _types(signals) == [("NFLX", "STOP_HIT")]

    def test_two_r_short(self):
        heat = make_heat(positions=[short_position()])
        # risk = 15 -> 2R at 215
        signals = sig.evaluate_signals(None, heat, {"NFLX": {"price": 214.0}}, "restrict", set())
        assert _types(signals) == [("NFLX", "TWO_R")]

    def test_manage_signals_fire_regardless_of_gate(self):
        heat = make_heat(positions=[long_position()])
        signals = sig.evaluate_signals(
            None, heat, {"AAPL": {"price": 94.0}}, "cash-priority", set()
        )
        assert _types(signals) == [("AAPL", "STOP_HIT")]

    def test_side_detected_from_stop_above_entry(self):
        heat = make_heat(positions=[short_position()])
        signals = sig.evaluate_signals(None, heat, {"NFLX": {"price": 261.0}}, "allow", set())
        assert signals[0]["side"] == "short"

    def test_trailed_long_with_explicit_side_is_quiet_above_stop(self):
        # Long trailed to breakeven+ (stop 105 > entry 100). Geometry alone
        # would misread it as a short and fire a false STOP_HIT at 110; the
        # explicit side from the heat ledger must win → no signal.
        pos = long_position(side="long", stop_loss=105.0)
        heat = make_heat(positions=[pos])
        signals = sig.evaluate_signals(None, heat, {"AAPL": {"price": 110.0}}, "allow", set())
        assert signals == []

    def test_trailed_long_explicit_side_fires_stop_hit_below_trailed_stop(self):
        pos = long_position(side="long", stop_loss=105.0)
        heat = make_heat(positions=[pos])
        signals = sig.evaluate_signals(None, heat, {"AAPL": {"price": 104.5}}, "allow", set())
        assert _types(signals) == [("AAPL", "STOP_HIT")]
        assert signals[0]["side"] == "long"

    def test_explicit_short_side_overrides_geometry(self):
        # Short whose stop was trailed BELOW entry (in profit): geometry says
        # "long"; the explicit side must keep short semantics — price rallying
        # back through the trailed stop is a STOP_HIT.
        pos = short_position(side="short", stop_loss=240.0)  # entry 245
        heat = make_heat(positions=[pos])
        signals = sig.evaluate_signals(None, heat, {"NFLX": {"price": 241.0}}, "restrict", set())
        assert _types(signals) == [("NFLX", "STOP_HIT")]
        assert signals[0]["side"] == "short"


# --------------------------------------------------------------------------- #
# evaluate_signals — earnings rules (plan rule 6.4)
# --------------------------------------------------------------------------- #
_THU = dt.date(2026, 6, 11)  # Thursday


class TestEarningsRules:
    def test_weekdays_until(self):
        assert sig.weekdays_until("2026-06-12", _THU) == 1  # Friday
        assert sig.weekdays_until("2026-06-15", _THU) == 2  # Monday, weekend skipped
        assert sig.weekdays_until("2026-06-11", _THU) == 0  # same day
        assert sig.weekdays_until("2026-06-10", _THU) == 0  # past

    def test_open_short_blocked_before_earnings(self):
        wl = make_watchlist([short_candidate()])
        quotes = {"NFLX": {"price": 244.0, "earnings_date": "2026-06-18"}}  # 5 weekdays
        signals = sig.evaluate_signals(wl, None, quotes, "restrict", set(), today=_THU)
        assert _types(signals) == [("NFLX", "SKIPPED_EARNINGS")]
        assert signals[0]["days_to_earnings"] == 5

    def test_open_short_allowed_when_earnings_far(self):
        wl = make_watchlist([short_candidate()])
        quotes = {"NFLX": {"price": 244.0, "earnings_date": "2026-07-30"}}
        signals = sig.evaluate_signals(wl, None, quotes, "restrict", set(), today=_THU)
        assert _types(signals) == [("NFLX", "OPEN_SHORT")]

    def test_open_short_allowed_when_earnings_unknown(self):
        wl = make_watchlist([short_candidate()])
        quotes = {"NFLX": {"price": 244.0, "earnings_date": None}}
        signals = sig.evaluate_signals(wl, None, quotes, "restrict", set(), today=_THU)
        assert _types(signals) == [("NFLX", "OPEN_SHORT")]

    def test_open_long_not_blocked_by_earnings(self):
        # The long side is already gated at plan time by the breakout planner.
        wl = make_watchlist([long_candidate()])
        quotes = {"NVDA": {"price": 156.0, "earnings_date": "2026-06-18"}}
        signals = sig.evaluate_signals(wl, None, quotes, "allow", set(), today=_THU)
        assert _types(signals) == [("NVDA", "OPEN_LONG")]

    def test_earnings_soon_warns_open_position(self):
        heat = make_heat(positions=[long_position()])
        quotes = {"AAPL": {"price": 100.0, "earnings_date": "2026-06-15"}}  # 2 weekdays
        signals = sig.evaluate_signals(None, heat, quotes, "allow", set(), today=_THU)
        assert ("AAPL", "EARNINGS_SOON") in _types(signals)
        warn = next(s for s in signals if s["type"] == "EARNINGS_SOON")
        assert warn["side"] == "long"
        assert warn["days_to_earnings"] == 2

    def test_no_earnings_warning_when_far(self):
        heat = make_heat(positions=[long_position()])
        quotes = {"AAPL": {"price": 100.0, "earnings_date": "2026-07-30"}}
        signals = sig.evaluate_signals(None, heat, quotes, "allow", set(), today=_THU)
        assert signals == []

    def test_earnings_soon_deduped(self):
        heat = make_heat(positions=[long_position()])
        quotes = {"AAPL": {"price": 100.0, "earnings_date": "2026-06-15"}}
        sent = {"AAPL:EARNINGS_SOON"}
        signals = sig.evaluate_signals(None, heat, quotes, "allow", sent, today=_THU)
        assert signals == []


# --------------------------------------------------------------------------- #
# premarket_gap_gate
# --------------------------------------------------------------------------- #
def _verdicts(flagged):
    return [(f["ticker"], f["verdict"]) for f in flagged]


class TestPremarketGapGate:
    # long_candidate: pivot 155.20, worst_entry 157.50, stop 151.30
    def test_long_extended_above_chase_band(self):
        wl = make_watchlist([long_candidate()])
        quotes = {"NVDA": {"price": 155.0, "premarket_price": 160.0}}
        flagged = sig.premarket_gap_gate(wl, quotes, "allow", today=_THU)
        assert _verdicts(flagged) == [("NVDA", sig.GAP_EXTENDED)]
        assert flagged[0]["gap_pct"] > 0

    def test_long_invalidated_below_stop(self):
        wl = make_watchlist([long_candidate()])
        quotes = {"NVDA": {"price": 150.0, "premarket_price": 150.0}}  # < stop 151.30
        assert _verdicts(sig.premarket_gap_gate(wl, quotes, "allow", today=_THU)) == [
            ("NVDA", sig.GAP_INVALIDATED)
        ]

    def test_long_armed_within_band_is_omitted(self):
        wl = make_watchlist([long_candidate()])
        quotes = {"NVDA": {"price": 156.0, "premarket_price": 156.0}}  # in [pivot, worst]
        assert sig.premarket_gap_gate(wl, quotes, "allow", today=_THU) == []

    def test_mild_gap_down_above_stop_stays_armed(self):
        # Below pivot but not through the stop -> breakout still pending, not flagged.
        wl = make_watchlist([long_candidate()])
        quotes = {"NVDA": {"price": 153.0, "premarket_price": 153.0}}
        assert sig.premarket_gap_gate(wl, quotes, "allow", today=_THU) == []

    def test_no_premarket_print_is_omitted(self):
        wl = make_watchlist([long_candidate()])
        quotes = {"NVDA": {"price": 160.0, "premarket_price": None}}
        assert sig.premarket_gap_gate(wl, quotes, "allow", today=_THU) == []

    def test_missing_premarket_field_is_omitted(self):
        wl = make_watchlist([long_candidate()])
        quotes = {"NVDA": {"price": 160.0}}  # premarket not requested
        assert sig.premarket_gap_gate(wl, quotes, "allow", today=_THU) == []

    def test_earnings_today_blocks_armed_long(self):
        wl = make_watchlist([long_candidate()])
        quotes = {"NVDA": {"price": 156.0, "premarket_price": 156.0, "earnings_date": "2026-06-12"}}
        flagged = sig.premarket_gap_gate(wl, quotes, "allow", today=_THU)  # Fri = 1 weekday
        assert _verdicts(flagged) == [("NVDA", sig.GAP_EARNINGS)]
        assert flagged[0]["days_to_earnings"] == 1

    def test_earnings_far_does_not_block_armed_long(self):
        wl = make_watchlist([long_candidate()])
        quotes = {"NVDA": {"price": 156.0, "premarket_price": 156.0, "earnings_date": "2026-07-30"}}
        assert sig.premarket_gap_gate(wl, quotes, "allow", today=_THU) == []

    def test_long_not_flagged_when_gate_not_allow(self):
        wl = make_watchlist([long_candidate()])
        quotes = {"NVDA": {"price": 160.0, "premarket_price": 160.0}}
        assert sig.premarket_gap_gate(wl, quotes, "restrict", today=_THU) == []

    # short_candidate: pivot 245.0, worst_entry 240.1, stop 260.0
    def test_short_extended_below_chase_band(self):
        wl = make_watchlist([short_candidate()])
        quotes = {"NFLX": {"price": 235.0, "premarket_price": 235.0}}  # < worst 240.1
        assert _verdicts(sig.premarket_gap_gate(wl, quotes, "restrict", today=_THU)) == [
            ("NFLX", sig.GAP_EXTENDED)
        ]

    def test_short_invalidated_above_stop(self):
        wl = make_watchlist([short_candidate()])
        quotes = {"NFLX": {"price": 262.0, "premarket_price": 262.0}}  # >= stop 260
        assert _verdicts(sig.premarket_gap_gate(wl, quotes, "cash-priority", today=_THU)) == [
            ("NFLX", sig.GAP_INVALIDATED)
        ]

    def test_short_armed_within_band_is_omitted(self):
        wl = make_watchlist([short_candidate()])
        quotes = {"NFLX": {"price": 242.0, "premarket_price": 242.0}}  # in [worst, pivot]
        assert sig.premarket_gap_gate(wl, quotes, "restrict", today=_THU) == []

    def test_short_not_flagged_when_gate_allow(self):
        wl = make_watchlist([short_candidate()])
        quotes = {"NFLX": {"price": 235.0, "premarket_price": 235.0}}
        assert sig.premarket_gap_gate(wl, quotes, "allow", today=_THU) == []

    def test_short_not_flagged_when_shorts_disabled(self):
        # A short the monitor would not arm (allow_shorts=False) is never
        # gap-flagged — mirrors evaluate_signals' side gate.
        wl = make_watchlist([short_candidate()])
        quotes = {"NFLX": {"price": 235.0, "premarket_price": 235.0}}
        assert sig.premarket_gap_gate(wl, quotes, "restrict", today=_THU, allow_shorts=False) == []

    def test_empty_watchlist_returns_empty(self):
        assert sig.premarket_gap_gate(None, {}, "allow", today=_THU) == []
        assert sig.premarket_gap_gate(make_watchlist([]), {}, "allow", today=_THU) == []


# --------------------------------------------------------------------------- #
# evaluate_signals — dedup
# --------------------------------------------------------------------------- #
class TestDedup:
    def test_sent_keys_are_skipped(self):
        heat = make_heat(positions=[long_position()])
        sent = {"AAPL:STOP_HIT"}
        signals = sig.evaluate_signals(None, heat, {"AAPL": {"price": 94.0}}, "allow", sent)
        assert signals == []

    def test_signal_key_format(self):
        heat = make_heat(positions=[long_position()])
        signals = sig.evaluate_signals(None, heat, {"AAPL": {"price": 94.0}}, "allow", set())
        assert signals[0]["key"] == "AAPL:STOP_HIT"


# --------------------------------------------------------------------------- #
# Signals state (dedup persistence)
# --------------------------------------------------------------------------- #
class TestSignalsState:
    def test_load_missing_returns_default(self, tmp_path):
        state = sig.load_signals_state(tmp_path / "nope.json", "2026-06-11")
        assert state == {"date": "2026-06-11", "sent": {}}

    def test_roundtrip(self, tmp_path):
        path = tmp_path / "state.json"
        state = {"date": "2026-06-11", "sent": {"NVDA:OPEN_LONG": "2026-06-11T16:00:00"}}
        sig.save_signals_state(path, state)
        assert sig.load_signals_state(path, "2026-06-11") == state

    def test_rollover_on_new_date(self, tmp_path):
        path = tmp_path / "state.json"
        sig.save_signals_state(
            path, {"date": "2026-06-10", "sent": {"NVDA:OPEN_LONG": "2026-06-10T16:00:00"}}
        )
        state = sig.load_signals_state(path, "2026-06-11")
        assert state == {"date": "2026-06-11", "sent": {}}

    def test_corrupt_file_returns_default(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not json", encoding="utf-8")
        state = sig.load_signals_state(path, "2026-06-11")
        assert state == {"date": "2026-06-11", "sent": {}}

    def test_mark_sent(self):
        state = {"date": "2026-06-11", "sent": {}}
        sig.mark_sent(state, ["NVDA:OPEN_LONG"], "2026-06-11T16:05:00")
        assert state["sent"]["NVDA:OPEN_LONG"] == "2026-06-11T16:05:00"


# --------------------------------------------------------------------------- #
# fetch_quotes (scanner HTTP mocked)
# --------------------------------------------------------------------------- #
class TestFetchQuotes:
    def test_parses_scanner_rows(self, monkeypatch):
        captured = {}

        def fake_post(url, payload, timeout=30):
            captured["url"] = url
            captured["payload"] = payload
            return {
                "totalCount": 2,
                "data": [
                    {"s": "NASDAQ:NVDA", "d": ["NVDA", 156.0, 1000000]},
                    {"s": "NASDAQ:AAPL", "d": ["AAPL", 230.5, 2000000]},
                ],
            }

        monkeypatch.setattr(sig, "_http_post_json", fake_post)
        quotes = sig.fetch_quotes(["NVDA", "AAPL"])
        assert quotes["NVDA"]["price"] == 156.0
        assert quotes["AAPL"]["price"] == 230.5
        assert "scanner.tradingview.com" in captured["url"]
        names_filter = [f for f in captured["payload"]["filter"] if f["left"] == "name"][0]
        assert set(names_filter["right"]) == {"NVDA", "AAPL"}

    def test_first_row_wins_for_duplicate_names(self, monkeypatch):
        def fake_post(url, payload, timeout=30):
            return {
                "data": [
                    {"s": "NYSE:F", "d": ["F", 12.0, 100]},
                    {"s": "AMEX:F", "d": ["F", 99.0, 100]},
                ]
            }

        monkeypatch.setattr(sig, "_http_post_json", fake_post)
        assert sig.fetch_quotes(["F"])["F"]["price"] == 12.0

    def test_null_close_is_skipped(self, monkeypatch):
        def fake_post(url, payload, timeout=30):
            return {"data": [{"s": "NASDAQ:XXX", "d": ["XXX", None, 100]}]}

        monkeypatch.setattr(sig, "_http_post_json", fake_post)
        assert sig.fetch_quotes(["XXX"]) == {}

    def test_empty_ticker_list_short_circuits(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("must not be called")

        monkeypatch.setattr(sig, "_http_post_json", boom)
        assert sig.fetch_quotes([]) == {}

    def test_network_error_raises_quotes_error(self, monkeypatch):
        calls = []

        def fake_post(url, payload, timeout=30):
            calls.append(1)
            raise sig.TransientQuotesError("boom")

        monkeypatch.setattr(sig, "_http_post_json", fake_post)
        monkeypatch.setattr(sig.time, "sleep", lambda s: None)
        try:
            sig.fetch_quotes(["NVDA"], max_retries=3)
        except sig.QuotesError:
            pass
        else:
            raise AssertionError("expected QuotesError")
        assert len(calls) == 3

    def test_premarket_columns_requested_and_parsed(self, monkeypatch):
        captured = {}

        def fake_post(url, payload, timeout=30):
            captured["payload"] = payload
            return {"data": [{"s": "NASDAQ:NVDA", "d": ["NVDA", 156.0, 1000000, None, 159.0, 1.9]}]}

        monkeypatch.setattr(sig, "_http_post_json", fake_post)
        q = sig.fetch_quotes(["NVDA"], premarket=True)
        assert captured["payload"]["columns"][-2:] == ["premarket_close", "premarket_change"]
        assert q["NVDA"]["premarket_price"] == 159.0
        assert q["NVDA"]["premarket_change_pct"] == 1.9

    def test_premarket_null_print_is_none(self, monkeypatch):
        def fake_post(url, payload, timeout=30):
            return {"data": [{"s": "NASDAQ:XYZ", "d": ["XYZ", 50.0, 100, None, None, None]}]}

        monkeypatch.setattr(sig, "_http_post_json", fake_post)
        assert sig.fetch_quotes(["XYZ"], premarket=True)["XYZ"]["premarket_price"] is None

    def test_non_premarket_fetch_omits_premarket_keys(self, monkeypatch):
        captured = {}

        def fake_post(url, payload, timeout=30):
            captured["payload"] = payload
            return {"data": [{"s": "NASDAQ:NVDA", "d": ["NVDA", 156.0, 1000000]}]}

        monkeypatch.setattr(sig, "_http_post_json", fake_post)
        q = sig.fetch_quotes(["NVDA"])
        assert "premarket_close" not in captured["payload"]["columns"]
        assert "premarket_price" not in q["NVDA"]


# --------------------------------------------------------------------------- #
# Watchlist JSON is serializable
# --------------------------------------------------------------------------- #
def test_watchlist_json_serializable():
    wl = sig.build_watchlist(
        "2026-06-11", "allow", make_plan(), make_short_candidates(), None, account_size=150000
    )
    json.dumps(wl)
