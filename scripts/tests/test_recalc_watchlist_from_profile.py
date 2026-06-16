"""Tests for scripts/recalc_watchlist_from_profile.py (UI Профиль-tab Пересчитать).

The real run shells out to the breakout-trade-planner (live TradingView data),
so these tests drive --dry-run and assert the orchestration: which canonical VCP
snapshot / heat / profile get resolved, and the exact planner + builder argv —
mirroring how the server-side arg-builders are unit-tested.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[1] / "recalc_watchlist_from_profile.py"
_spec = importlib.util.spec_from_file_location("recalc_watchlist_from_profile", _MODULE_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

DATE = "2026-06-16"
PROFILE = {
    "account_size": 150000,
    "risk_pct": 1.5,
    "max_position_pct": 25.0,
    "atr_multiplier": 2.0,
    "target_r_multiple": 2.0,
}


def _setup(tmp_path, *, vcps=("vcp_screener_2026-06-16_120000.json",), heats=(), profile=True):
    data_dir = tmp_path / "trading-data"
    (data_dir / "screeners").mkdir(parents=True)
    (data_dir / "journal").mkdir(parents=True)
    for name in vcps:
        (data_dir / "screeners" / name).write_text(
            json.dumps({"schema_version": "1.0", "results": []}), encoding="utf-8"
        )
    for name in heats:
        (data_dir / "journal" / name).write_text(json.dumps({}), encoding="utf-8")
    if profile:
        (data_dir / "trading_profile.json").write_text(json.dumps(PROFILE), encoding="utf-8")
    return data_dir


def _run_dry(data_dir, *extra):
    return mod.main(["--data-dir", str(data_dir), "--date", DATE, "--dry-run", *extra])


def test_dry_run_resolves_latest_vcp_and_builds_commands(tmp_path, capsys):
    data_dir = _setup(
        tmp_path,
        vcps=("vcp_screener_2026-06-15_120000.json", "vcp_screener_2026-06-16_093000.json"),
        heats=("portfolio_heat_2026-06-16_120000.json",),
    )
    assert _run_dry(data_dir) == 0
    out = capsys.readouterr().out

    # Newest VCP snapshot is chosen (lexical sort on the timestamped name).
    assert "vcp_screener_2026-06-16_093000.json" in out
    assert "vcp_screener_2026-06-15_120000.json" not in out.split("planner:")[1]

    planner_line = next(ln for ln in out.splitlines() if "planner:" in ln)
    assert "plan_breakout_trades.py" in planner_line
    assert "--input" in planner_line and "vcp_screener_2026-06-16_093000.json" in planner_line
    assert "--profile" in planner_line and "trading_profile.json" in planner_line
    # Heat snapshot is wired as current-exposure when present.
    assert "--current-exposure-json" in planner_line
    assert "portfolio_heat_2026-06-16_120000.json" in planner_line

    build_line = next(ln for ln in out.splitlines() if "build:" in ln)
    assert "build_watchlist_from_plan.py" in build_line
    assert "--promote" in build_line
    assert "--ingest-theses" in build_line
    assert "--date" in build_line and DATE in build_line
    assert "--data-dir" in build_line


def test_dry_run_without_heat_omits_current_exposure(tmp_path, capsys):
    data_dir = _setup(tmp_path, heats=())
    assert _run_dry(data_dir) == 0
    planner_line = next(ln for ln in capsys.readouterr().out.splitlines() if "planner:" in ln)
    assert "--current-exposure-json" not in planner_line


def test_no_canonical_vcp_returns_2(tmp_path, capsys):
    data_dir = _setup(tmp_path, vcps=())
    assert _run_dry(data_dir) == 2
    assert "no canonical VCP screener" in capsys.readouterr().out


def test_missing_profile_returns_1(tmp_path, capsys):
    data_dir = _setup(tmp_path, profile=False)
    assert _run_dry(data_dir) == 1
    assert "trading profile not found" in capsys.readouterr().out


def test_explicit_profile_override(tmp_path, capsys):
    data_dir = _setup(tmp_path, profile=False)
    custom = tmp_path / "custom_profile.json"
    custom.write_text(json.dumps(PROFILE), encoding="utf-8")
    assert _run_dry(data_dir, "--profile", str(custom)) == 0
    planner_line = next(ln for ln in capsys.readouterr().out.splitlines() if "planner:" in ln)
    assert "custom_profile.json" in planner_line


def test_bad_date_errors(tmp_path):
    data_dir = _setup(tmp_path)
    with pytest.raises(SystemExit) as exc:
        mod.main(["--data-dir", str(data_dir), "--date", "06/16/2026", "--dry-run"])
    assert exc.value.code != 0
