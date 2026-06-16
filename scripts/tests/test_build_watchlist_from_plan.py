"""Tests for scripts/build_watchlist_from_plan.py (UI Screener-tab Save backend)."""

import importlib.util
import json
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "build_watchlist_from_plan.py"
_spec = importlib.util.spec_from_file_location("build_watchlist_from_plan", _MODULE_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

DATE = "2026-06-16"


def make_plan():
    """Two actionable orders in the planner's real shape (→ two long candidates)."""
    return {
        "schema_version": "1.0",
        "summary": {"actionable_count": 2},
        "actionable_orders": [
            {
                "symbol": "NVDA",
                "sector": "Technology",
                "composite_score": 88.0,
                "execution_state": "Pre-breakout",
                "plan_type": "pending_breakout",
                "trade_plan": {
                    "signal_entry": 155.20,
                    "worst_entry": 158.30,
                    "stop_loss_price": 151.30,
                    "target_price": 163.00,
                    "shares": 380,
                    "risk_dollars": 2356.0,
                },
            },
            {
                "symbol": "AVGO",
                "sector": "Technology",
                "composite_score": 81.5,
                "execution_state": "Pre-breakout",
                "plan_type": "pending_breakout",
                "trade_plan": {
                    "signal_entry": 250.00,
                    "worst_entry": 255.00,
                    "stop_loss_price": 240.00,
                    "target_price": 270.00,
                    "shares": 120,
                    "risk_dollars": 1200.0,
                },
            },
        ],
    }


def _setup(tmp_path, *, plan=True, gate="restrict"):
    """Create a staging area + canonical schedule dir with optional gate file."""
    data_dir = tmp_path / "trading-data"
    (data_dir / "schedule").mkdir(parents=True)
    staging = data_dir / "ui-staging"
    staging.mkdir()

    vcp = staging / "vcp_screener_2026-06-16_120000.json"
    vcp.write_text(json.dumps({"schema_version": "1.0", "results": []}), encoding="utf-8")

    plan_path = None
    if plan:
        plan_path = staging / "breakout_trade_plan_2026-06-16_120000.json"
        plan_path.write_text(json.dumps(make_plan()), encoding="utf-8")

    if gate is not None:
        (data_dir / "schedule" / f"exposure_decision_{DATE}.json").write_text(
            json.dumps({"decision": gate}), encoding="utf-8"
        )
    return data_dir, vcp, plan_path


def _run(data_dir, vcp, plan_path, *extra):
    argv = ["--staged-vcp", str(vcp), "--date", DATE, "--data-dir", str(data_dir), *extra]
    if plan_path:
        argv += ["--staged-plan", str(plan_path)]
    return mod.main(argv)


def _read_wl(data_dir):
    return json.loads((data_dir / "schedule" / f"watchlist_{DATE}.json").read_text())


def test_two_orders_exact_levels_and_real_gate(tmp_path):
    data_dir, vcp, plan_path = _setup(tmp_path, gate="restrict")
    assert _run(data_dir, vcp, plan_path, "--promote") == 0

    wl = _read_wl(data_dir)
    # Real gate decision is honored (not forced to "allow").
    assert wl["exposure_decision"] == "restrict"
    cands = wl["candidates"]
    assert [c["ticker"] for c in cands] == ["NVDA", "AVGO"]

    nvda = cands[0]
    assert nvda["pivot"] == 155.20
    assert nvda["worst_entry"] == 158.30
    assert nvda["stop"] == 151.30
    assert nvda["target"] == 163.00
    assert nvda["shares"] == 380
    assert nvda["risk_dollars"] == 2356.0
    assert nvda["score"] == 88.0
    assert nvda["side"] == "long"


def test_promote_copies_into_canonical_dirs(tmp_path):
    data_dir, vcp, plan_path = _setup(tmp_path)
    assert _run(data_dir, vcp, plan_path, "--promote") == 0

    screeners = list((data_dir / "screeners").glob("vcp_screener_*.json"))
    plans = list((data_dir / "plans").glob("breakout_trade_plan_*.json"))
    assert len(screeners) == 1
    assert len(plans) == 1
    # source_plan points at the promoted plan (repo-relative or absolute path).
    assert _read_wl(data_dir)["source_plan"].endswith(plans[0].name)


def test_no_promote_leaves_canonical_dirs_empty(tmp_path):
    data_dir, vcp, plan_path = _setup(tmp_path)
    assert _run(data_dir, vcp, plan_path) == 0  # no --promote

    assert not (data_dir / "screeners").exists()
    assert not (data_dir / "plans").exists()
    assert _read_wl(data_dir)["source_plan"] is None


def test_gate_defaults_allow_when_missing(tmp_path):
    data_dir, vcp, plan_path = _setup(tmp_path, gate=None)
    assert _run(data_dir, vcp, plan_path) == 0
    assert _read_wl(data_dir)["exposure_decision"] == "allow"


def test_no_plan_writes_empty_watchlist(tmp_path):
    data_dir, vcp, _ = _setup(tmp_path, plan=False)
    assert _run(data_dir, vcp, None) == 0
    assert _read_wl(data_dir)["candidates"] == []


def test_missing_staged_vcp_returns_1_no_write(tmp_path):
    data_dir, _, plan_path = _setup(tmp_path)
    missing = data_dir / "ui-staging" / "does_not_exist.json"
    assert _run(data_dir, missing, plan_path, "--promote") == 1
    assert not (data_dir / "schedule" / f"watchlist_{DATE}.json").exists()
    assert not (data_dir / "screeners").exists()
