"""Tests for plan_breakout_trades module."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date, datetime, timedelta

import pytest
from plan_breakout_trades import (
    generate_plans,
    load_input,
    load_profile,
    main,
    process_candidate,
    validate_result,
)


def _make_args(**overrides) -> argparse.Namespace:
    defaults = {
        "input": "test.json",
        "account_size": 100_000,
        "risk_pct": 0.5,
        "max_position_pct": 10.0,
        "max_sector_pct": 30.0,
        "max_portfolio_heat_pct": 6.0,
        "target_r_multiple": 2.0,
        "stop_buffer_pct": 1.0,
        "max_chase_pct": 2.0,
        "pivot_buffer_pct": 0.1,
        "current_exposure_json": None,
        "output_dir": "/tmp/test_plans",
        "earnings_gate_days": 0,
        "time_stop_trading_days": 0,
        "profile": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _add_weekdays(start: date, n: int) -> date:
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _make_vcp_result(
    symbol: str = "TEST",
    score: float = 85.0,
    state: str = "Pre-breakout",
    valid_vcp: bool = True,
    pivot: float = 100.0,
    last_low: float = 95.0,
    sector: str = "Technology",
    price: float = 98.0,
    breakout_volume: bool = False,
    distance_from_pivot: float = -2.0,
) -> dict:
    return {
        "symbol": symbol,
        "company_name": f"{symbol} Inc.",
        "sector": sector,
        "price": price,
        "market_cap": 50_000_000_000,
        "composite_score": score,
        "rating": "Strong VCP",
        "execution_state": state,
        "valid_vcp": valid_vcp,
        "entry_ready": False,
        "vcp_pattern": {
            "pivot_price": pivot,
            "contractions": [
                {"label": "T1", "high_price": 105.0, "low_price": 92.0, "depth_pct": 12.4},
                {"label": "T2", "high_price": pivot, "low_price": last_low, "depth_pct": 5.0},
            ],
            "atr_value": 2.5,
        },
        "volume_pattern": {
            "breakout_volume_detected": breakout_volume,
            "avg_volume_50d": 1_000_000,
            "dry_up_ratio": 0.5,
        },
        "pivot_proximity": {
            "stop_loss_price": last_low * 0.99,
            "risk_pct": 5.0,
            "distance_from_pivot_pct": distance_from_pivot,
        },
        "trend_template": {"score": 100},
        "relative_strength": {"rs_percentile": 80},
    }


def _make_input_data(results: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "metadata": {"generated_at": "2026-04-12 17:35:47"},
        "results": results,
        "summary": {"total": len(results)},
        "sector_distribution": {},
    }


class TestLoadInput:
    def test_missing_schema_version_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"results": []}, f)
            f.flush()
            with pytest.raises(ValueError, match="schema_version"):
                load_input(f.name)
            os.unlink(f.name)

    def test_wrong_schema_version_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"schema_version": "99.0", "results": []}, f)
            f.flush()
            with pytest.raises(ValueError, match="Unsupported"):
                load_input(f.name)
            os.unlink(f.name)

    def test_valid_input_loads(self):
        data = _make_input_data([_make_vcp_result()])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            loaded = load_input(f.name)
            assert len(loaded["results"]) == 1
            os.unlink(f.name)


class TestValidateResult:
    def test_valid_result(self):
        result = _make_vcp_result()
        is_valid, warnings = validate_result(result)
        assert is_valid
        assert warnings == []

    def test_missing_symbol(self):
        result = _make_vcp_result()
        del result["symbol"]
        is_valid, warnings = validate_result(result)
        assert not is_valid
        assert any("symbol" in w for w in warnings)

    def test_missing_contractions(self):
        result = _make_vcp_result()
        result["vcp_pattern"]["contractions"] = []
        is_valid, warnings = validate_result(result)
        assert not is_valid


class TestMinerviniGate:
    def test_prebreakout_strong_vcp_actionable(self):
        result = _make_vcp_result(score=85.0, state="Pre-breakout", valid_vcp=True)
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "actionable"
        assert classified["data"]["plan_type"] == "pending_breakout"
        assert classified["data"]["decision_code"] == "ACTIONABLE_PREBREAKOUT"

    def test_prebreakout_risk_worst_over_8_rejected(self):
        # pivot=100, last_low=88 -> stop=87.12, worst=102 -> risk=14.58%
        result = _make_vcp_result(score=85.0, last_low=88.0)
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "rejected"
        assert "risk_pct_worst" in classified["data"]["reason"]

    def test_prebreakout_invalid_vcp_rejected(self):
        result = _make_vcp_result(valid_vcp=False)
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "rejected"

    def test_prebreakout_developing_watchlist(self):
        result = _make_vcp_result(score=65.0, valid_vcp=True)
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "watchlist"

    def test_developing_invalid_vcp_rejected(self):
        result = _make_vcp_result(score=65.0, valid_vcp=False)
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "rejected"

    def test_breakout_with_volume_revalidation(self):
        result = _make_vcp_result(
            score=85.0,
            state="Breakout",
            valid_vcp=True,
            breakout_volume=True,
            distance_from_pivot=1.5,
            price=101.0,
        )
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "revalidation"
        assert classified["data"]["plan_type"] == "late_breakout_revalidation"

    def test_breakout_no_volume_rejected(self):
        result = _make_vcp_result(
            score=85.0,
            state="Breakout",
            valid_vcp=True,
            breakout_volume=False,
            distance_from_pivot=1.5,
            price=101.0,
        )
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "rejected"

    def test_breakout_price_above_worst_rejected(self):
        result = _make_vcp_result(
            score=85.0,
            state="Breakout",
            valid_vcp=True,
            breakout_volume=True,
            distance_from_pivot=1.5,
            price=110.0,
        )
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "rejected"

    def test_extended_state_rejected(self):
        result = _make_vcp_result(state="Extended")
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "rejected"

    def test_overextended_state_rejected(self):
        result = _make_vcp_result(state="Overextended")
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "rejected"

    def test_breakout_developing_does_not_watchlist(self):
        """Breakout candidates must not go to watchlist — they already crossed pivot."""
        result = _make_vcp_result(
            score=65.0,
            state="Breakout",
            valid_vcp=True,
            breakout_volume=True,
            distance_from_pivot=1.5,
            price=101.0,
        )
        args = _make_args()
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "rejected"
        assert classified["classification"] != "watchlist"


class TestPortfolioConstraints:
    def test_heat_ceiling_defers(self):
        result = _make_vcp_result(score=85.0)
        args = _make_args(max_portfolio_heat_pct=0.01)  # Tiny ceiling
        classified = process_candidate(
            result, args, 0.0, {}, {"sector_exposure": {}, "open_risk_pct": 0}
        )
        assert classified["classification"] == "deferred"

    def test_sector_constraint_constrains(self):
        result = _make_vcp_result(score=85.0, sector="Technology")
        args = _make_args(max_sector_pct=5.0)
        exposure = {"sector_exposure": {"Technology": 4.9}, "open_risk_pct": 0}
        classified = process_candidate(result, args, 0.0, {}, exposure)
        assert classified["classification"] == "constrained"


class TestGeneratePlans:
    def test_empty_results(self):
        data = _make_input_data([])
        args = _make_args()
        plans = generate_plans(data, args)
        assert plans["schema_version"] == "1.0"
        assert plans["summary"]["actionable_count"] == 0

    def test_score_order_processing(self):
        r1 = _make_vcp_result(symbol="HIGH", score=90.0)
        r2 = _make_vcp_result(symbol="LOW", score=75.0)
        data = _make_input_data([r2, r1])  # Lower score first in input
        args = _make_args()
        plans = generate_plans(data, args)
        if len(plans["actionable_orders"]) >= 2:
            assert plans["actionable_orders"][0]["symbol"] == "HIGH"

    def test_validation_failure_creates_warning(self):
        bad = {"symbol": "BAD"}  # Missing required fields
        data = _make_input_data([bad])
        args = _make_args()
        plans = generate_plans(data, args)
        assert len(plans["warnings"]) > 0
        assert plans["summary"]["rejected_count"] == 1

    def test_actionable_has_order_templates(self):
        result = _make_vcp_result(score=85.0)
        data = _make_input_data([result])
        args = _make_args()
        plans = generate_plans(data, args)
        assert plans["summary"]["actionable_count"] == 1
        order = plans["actionable_orders"][0]
        assert "pre_place" in order["order_templates"]
        assert "post_confirm" in order["order_templates"]
        assert order["order_templates"]["pre_place"]["type"] == "stop_limit"
        assert order["order_templates"]["post_confirm"]["type"] == "limit"

    def test_input_metadata_populated(self):
        data = _make_input_data([_make_vcp_result()])
        args = _make_args()
        plans = generate_plans(data, args)
        meta = plans["input_metadata"]
        assert meta["candidates_in_file"] == 1
        assert meta["input_scope"] == "top_n_only"


class TestEarningsGate:
    """--earnings-gate-days: block plans with earnings within N trading days."""

    def _near_far(self):
        today = datetime.now().date()
        near = _add_weekdays(today, 5).isoformat()  # 5 trading days -> blocked at gate 10
        far = _add_weekdays(today, 25).isoformat()  # 25 trading days -> passes gate 10
        return near, far

    def test_gate_disabled_no_annotation(self):
        data = _make_input_data([_make_vcp_result()])
        plans = generate_plans(data, _make_args())
        assert plans["blocked_earnings"] == []
        assert plans["summary"]["blocked_earnings_count"] == 0
        assert "earnings_gate" not in plans["actionable_orders"][0]

    def test_blocked_plan_moves_to_blocked_list(self):
        near, _ = self._near_far()
        data = _make_input_data([_make_vcp_result(symbol="SOON")])
        args = _make_args(earnings_gate_days=10)
        plans = generate_plans(data, args, earnings_map={"SOON": near})
        assert plans["summary"]["actionable_count"] == 0
        assert plans["summary"]["blocked_earnings_count"] == 1
        blocked = plans["blocked_earnings"][0]
        assert blocked["symbol"] == "SOON"
        assert blocked["earnings_gate"] == "blocked"
        assert blocked["earnings_date"] == near
        assert blocked["days_to_earnings"] == 5
        assert "blocked_reason" in blocked
        # Blocked plans must not consume portfolio heat
        assert plans["summary"]["total_risk_dollars"] == 0

    def test_pass_plan_is_annotated(self):
        _, far = self._near_far()
        data = _make_input_data([_make_vcp_result(symbol="LATER")])
        args = _make_args(earnings_gate_days=10)
        plans = generate_plans(data, args, earnings_map={"LATER": far})
        assert plans["summary"]["actionable_count"] == 1
        order = plans["actionable_orders"][0]
        assert order["earnings_gate"] == "pass"
        assert order["earnings_date"] == far
        assert order["days_to_earnings"] == 25

    def test_no_calendar_entry_passes(self):
        data = _make_input_data([_make_vcp_result(symbol="QUIET")])
        args = _make_args(earnings_gate_days=10)
        plans = generate_plans(data, args, earnings_map={})
        order = plans["actionable_orders"][0]
        assert order["earnings_gate"] == "pass"
        assert order["earnings_date"] is None

    def test_fetch_failure_marks_unknown_and_warns(self):
        data = _make_input_data([_make_vcp_result(symbol="MYST")])
        args = _make_args(earnings_gate_days=10)
        plans = generate_plans(data, args, earnings_map={}, earnings_fetch_failed=True)
        assert plans["summary"]["actionable_count"] == 1
        assert plans["actionable_orders"][0]["earnings_gate"] == "unknown"
        assert any(w["code"] == "EARNINGS_GATE_DEGRADED" for w in plans["warnings"])

    def test_blocked_frees_heat_for_next_candidate(self):
        near, _ = self._near_far()
        soon = _make_vcp_result(symbol="SOON", score=90.0)
        later = _make_vcp_result(symbol="LATER", score=85.0)
        data = _make_input_data([soon, later])
        args = _make_args(earnings_gate_days=10)
        plans = generate_plans(data, args, earnings_map={"SOON": near})
        assert [o["symbol"] for o in plans["actionable_orders"]] == ["LATER"]
        assert [b["symbol"] for b in plans["blocked_earnings"]] == ["SOON"]

    def test_watchlist_annotated_but_not_blocked(self):
        near, _ = self._near_far()
        data = _make_input_data([_make_vcp_result(symbol="WATCH", score=65.0)])
        args = _make_args(earnings_gate_days=10)
        plans = generate_plans(data, args, earnings_map={"WATCH": near})
        assert plans["blocked_earnings"] == []
        watch = plans["watchlist"][0]
        assert watch["earnings_gate"] == "blocked"
        assert watch["earnings_date"] == near

    def test_revalidation_is_blocked_too(self):
        near, _ = self._near_far()
        result = _make_vcp_result(
            symbol="BRK",
            score=85.0,
            state="Breakout",
            breakout_volume=True,
            distance_from_pivot=1.5,
            price=101.0,
        )
        data = _make_input_data([result])
        args = _make_args(earnings_gate_days=10)
        plans = generate_plans(data, args, earnings_map={"BRK": near})
        assert plans["summary"]["revalidation_count"] == 0
        assert [b["symbol"] for b in plans["blocked_earnings"]] == ["BRK"]
        assert plans["blocked_earnings"][0]["plan_type"] == "late_breakout_revalidation"


class TestTimeStop:
    """--time-stop-trading-days: annotate actionable plans with a time-stop rule."""

    def test_time_stop_fields_present(self):
        data = _make_input_data([_make_vcp_result()])
        args = _make_args(time_stop_trading_days=15)
        plans = generate_plans(data, args)
        tp = plans["actionable_orders"][0]["trade_plan"]
        assert tp["time_stop_trading_days"] == 15
        assert "+1R" in tp["time_stop_rule"]
        assert "15 trading days" in tp["time_stop_rule"]
        assert plans["parameters"]["time_stop_trading_days"] == 15

    def test_time_stop_disabled_by_default(self):
        data = _make_input_data([_make_vcp_result()])
        plans = generate_plans(data, _make_args())
        tp = plans["actionable_orders"][0]["trade_plan"]
        assert "time_stop_trading_days" not in tp
        assert "time_stop_rule" not in tp


class TestLoadProfile:
    """--profile: JSON parameter profile overriding argparse defaults."""

    PLANNER_KEYS = {
        "account_size",
        "risk_pct",
        "max_position_pct",
        "max_sector_pct",
        "max_portfolio_heat_pct",
        "target_r_multiple",
        "stop_buffer_pct",
        "max_chase_pct",
        "pivot_buffer_pct",
        "earnings_gate_days",
        "time_stop_trading_days",
    }

    def _write(self, tmp_path, payload) -> str:
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(payload))
        return str(path)

    def test_applies_known_keys(self, tmp_path):
        path = self._write(tmp_path, {"account_size": 150000, "risk_pct": 1.5})
        profile = load_profile(path, self.PLANNER_KEYS)
        assert profile == {"account_size": 150000, "risk_pct": 1.5}

    def test_keys_for_other_scripts_silently_skipped(self, tmp_path):
        # atr_multiplier / max_positions belong to position-sizer / heat ledger
        path = self._write(tmp_path, {"account_size": 150000, "atr_multiplier": 2.0})
        profile = load_profile(path, self.PLANNER_KEYS)
        assert profile == {"account_size": 150000}

    def test_unknown_keys_warn_but_do_not_fail(self, tmp_path, capsys):
        path = self._write(tmp_path, {"account_size": 150000, "risk_pc": 9.9})
        profile = load_profile(path, self.PLANNER_KEYS)
        assert profile == {"account_size": 150000}
        assert "risk_pc" in capsys.readouterr().err

    def test_non_numeric_value_raises(self, tmp_path):
        path = self._write(tmp_path, {"account_size": "lots"})
        with pytest.raises(ValueError, match="account_size"):
            load_profile(path, self.PLANNER_KEYS)

    def test_non_object_json_raises(self, tmp_path):
        path = self._write(tmp_path, [1, 2, 3])
        with pytest.raises(ValueError, match="object"):
            load_profile(path, self.PLANNER_KEYS)


class TestMainWithProfile:
    """End-to-end: main() reads --profile, CLI flags win over profile values."""

    def _write_input(self, tmp_path) -> str:
        data = _make_input_data([_make_vcp_result()])
        path = tmp_path / "vcp.json"
        path.write_text(json.dumps(data))
        return str(path)

    def _latest_plan(self, out_dir) -> dict:
        files = sorted(out_dir.glob("breakout_trade_plan_*.json"))
        assert files, "no plan JSON produced"
        return json.loads(files[-1].read_text())

    def test_profile_supplies_account_size(self, tmp_path):
        input_path = self._write_input(tmp_path)
        profile = tmp_path / "profile.json"
        profile.write_text(json.dumps({"account_size": 150000, "risk_pct": 1.5}))
        out = tmp_path / "out"
        main(
            [
                "--input",
                input_path,
                "--profile",
                str(profile),
                "--output-dir",
                str(out),
            ]
        )
        plans = self._latest_plan(out)
        assert plans["parameters"]["account_size"] == 150000
        assert plans["parameters"]["base_risk_pct"] == 1.5

    def test_cli_flag_overrides_profile(self, tmp_path):
        input_path = self._write_input(tmp_path)
        profile = tmp_path / "profile.json"
        profile.write_text(json.dumps({"account_size": 150000, "risk_pct": 1.5}))
        out = tmp_path / "out"
        main(
            [
                "--input",
                input_path,
                "--profile",
                str(profile),
                "--risk-pct",
                "2.0",
                "--output-dir",
                str(out),
            ]
        )
        plans = self._latest_plan(out)
        assert plans["parameters"]["base_risk_pct"] == 2.0
        assert plans["parameters"]["account_size"] == 150000

    def test_missing_account_size_errors(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRADING_PROFILE", raising=False)
        monkeypatch.setenv("TRADING_DATE_DIR", str(tmp_path))  # no trading_profile.json here
        input_path = self._write_input(tmp_path)
        with pytest.raises(SystemExit):
            main(["--input", input_path, "--output-dir", str(tmp_path / "out")])

    def test_bad_profile_path_errors(self, tmp_path):
        input_path = self._write_input(tmp_path)
        with pytest.raises(SystemExit):
            main(
                [
                    "--input",
                    input_path,
                    "--profile",
                    str(tmp_path / "missing.json"),
                    "--account-size",
                    "100000",
                    "--output-dir",
                    str(tmp_path / "out"),
                ]
            )
