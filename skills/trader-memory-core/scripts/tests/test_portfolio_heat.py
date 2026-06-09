"""Tests for portfolio_heat.py — live open-risk (heat) ledger over the thesis store."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import portfolio_heat
import pytest
import thesis_store

# -- Helpers -------------------------------------------------------------------


def _make_active(
    state_dir: Path,
    ticker: str,
    *,
    entry: float,
    stop: float | None,
    shares: float,
    sector: str | None = None,
) -> str:
    """Register a thesis and walk it to ACTIVE with entry/shares (+ optional stop)."""
    origin = {"skill": "test-skill", "output_file": "t.json"}
    if sector:
        origin["raw_provenance"] = {"sector": sector}
    data = {
        "ticker": ticker,
        "thesis_type": "pivot_breakout",
        "thesis_statement": f"{ticker} heat-ledger test thesis (entry {entry})",
        "origin": origin,
    }
    tid = thesis_store.register(state_dir, data)
    thesis_store.transition(state_dir, tid, "ENTRY_READY", reason="test")
    thesis_store.open_position(
        state_dir, tid, actual_price=entry, actual_date="2026-06-01T00:00:00+00:00", shares=shares
    )
    if stop is not None:
        thesis_store.update(state_dir, tid, {"exit": {"stop_loss": stop}})
    return tid


# -- collect_positions ----------------------------------------------------------


class TestCollectPositions:
    def test_active_position_collected(self, tmp_path):
        _make_active(tmp_path, "AAPL", entry=50.0, stop=45.0, shares=100, sector="Technology")
        positions, warnings = portfolio_heat.collect_positions(tmp_path)
        assert warnings == []
        assert len(positions) == 1
        pos = positions[0]
        assert pos["ticker"] == "AAPL"
        assert pos["status"] == "ACTIVE"
        assert pos["shares"] == 100
        assert pos["entry_price"] == 50.0
        assert pos["stop_loss"] == 45.0
        assert pos["sector"] == "Technology"
        assert pos["risk_dollars"] == 500.0
        assert pos["position_value"] == 5000.0
        assert pos["risk_basis"] == "entry_minus_stop"

    def test_idea_thesis_ignored(self, tmp_path):
        thesis_store.register(
            tmp_path,
            {
                "ticker": "MSFT",
                "thesis_type": "pivot_breakout",
                "thesis_statement": "MSFT idea only — must not appear in heat",
                "origin": {"skill": "test-skill", "output_file": "t.json"},
            },
        )
        positions, warnings = portfolio_heat.collect_positions(tmp_path)
        assert positions == []
        assert warnings == []

    def test_stop_above_entry_contributes_zero_risk(self, tmp_path):
        _make_active(tmp_path, "NVDA", entry=50.0, stop=55.0, shares=100)
        positions, _ = portfolio_heat.collect_positions(tmp_path)
        assert positions[0]["risk_dollars"] == 0.0

    def test_missing_stop_warns(self, tmp_path):
        _make_active(tmp_path, "TSLA", entry=200.0, stop=None, shares=10)
        positions, warnings = portfolio_heat.collect_positions(tmp_path)
        assert positions[0]["risk_dollars"] is None
        assert positions[0]["risk_basis"] == "unknown"
        assert any(w["code"] == "STOP_MISSING" for w in warnings)

    def test_missing_stop_falls_back_to_sizer_risk(self, tmp_path):
        tid = _make_active(tmp_path, "AMD", entry=100.0, stop=None, shares=20)
        # position is replaced wholesale by update(); keep shares consistent
        thesis_store.update(
            tmp_path,
            tid,
            {"position": {"shares": 20, "shares_remaining": 20, "risk_dollars": 250.0}},
        )
        positions, warnings = portfolio_heat.collect_positions(tmp_path)
        assert positions[0]["risk_dollars"] == 250.0
        assert positions[0]["risk_basis"] == "sizer_risk_dollars"
        assert any(w["code"] == "STOP_MISSING_USED_SIZER_RISK" for w in warnings)

    def test_partially_closed_uses_shares_remaining(self, tmp_path):
        tid = _make_active(tmp_path, "GOOG", entry=100.0, stop=90.0, shares=30)
        trim_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        thesis_store.trim(tmp_path, tid, 10, 120.0, trim_at)
        positions, _ = portfolio_heat.collect_positions(tmp_path)
        assert len(positions) == 1
        pos = positions[0]
        assert pos["status"] == "PARTIALLY_CLOSED"
        assert pos["shares"] == 20
        assert pos["risk_dollars"] == 200.0  # (100-90) * 20 remaining

    def test_empty_state_dir(self, tmp_path):
        positions, warnings = portfolio_heat.collect_positions(tmp_path)
        assert positions == []
        assert warnings == []


# -- build_report ----------------------------------------------------------------


class TestBuildReport:
    def _two_position_report(self, tmp_path, **kwargs):
        _make_active(tmp_path, "AAPL", entry=50.0, stop=45.0, shares=100, sector="Technology")
        _make_active(tmp_path, "XOM", entry=200.0, stop=190.0, shares=10)
        positions, warnings = portfolio_heat.collect_positions(tmp_path)
        defaults = {"account_size": 100_000.0, "max_heat_pct": 6.0, "max_positions": 6}
        defaults.update(kwargs)
        return portfolio_heat.build_report(positions, warnings, **defaults)

    def test_heat_aggregation(self, tmp_path):
        report = self._two_position_report(tmp_path)
        assert report["open_risk_dollars"] == 600.0
        assert report["open_risk_pct"] == 0.6
        assert report["positions_count"] == 2
        assert report["heat_complete"] is True
        assert report["remaining_heat_pct"] == 5.4
        assert report["remaining_heat_dollars"] == 5400.0
        assert report["remaining_position_slots"] == 4

    def test_sector_exposure_pct_of_account(self, tmp_path):
        report = self._two_position_report(tmp_path)
        assert report["sector_exposure"] == {"Technology": 5.0, "Unknown": 2.0}

    def test_planner_compatible_top_level_keys(self, tmp_path):
        """Output must satisfy breakout-trade-planner --current-exposure-json contract."""
        report = self._two_position_report(tmp_path)
        assert isinstance(report["open_risk_pct"], float)
        assert isinstance(report["sector_exposure"], dict)

    def test_unknown_risk_marks_incomplete(self, tmp_path):
        _make_active(tmp_path, "TSLA", entry=200.0, stop=None, shares=10)
        positions, warnings = portfolio_heat.collect_positions(tmp_path)
        report = portfolio_heat.build_report(
            positions, warnings, account_size=100_000.0, max_heat_pct=6.0, max_positions=6
        )
        assert report["heat_complete"] is False
        assert report["open_risk_dollars"] == 0.0
        assert report["positions_count"] == 1

    def test_no_max_positions(self, tmp_path):
        report = self._two_position_report(tmp_path, max_positions=None)
        assert report["max_positions"] is None
        assert report["remaining_position_slots"] is None

    def test_empty_report(self):
        report = portfolio_heat.build_report(
            [], [], account_size=100_000.0, max_heat_pct=6.0, max_positions=6
        )
        assert report["open_risk_pct"] == 0.0
        assert report["sector_exposure"] == {}
        assert report["heat_complete"] is True


# -- CLI -------------------------------------------------------------------------


class TestMain:
    def test_main_writes_planner_compatible_json(self, tmp_path):
        state = tmp_path / "theses"
        state.mkdir()
        _make_active(state, "AAPL", entry=50.0, stop=45.0, shares=100, sector="Technology")
        out = tmp_path / "out"
        rc = portfolio_heat.main(
            [
                "--state-dir",
                str(state),
                "--account-size",
                "100000",
                "--output-dir",
                str(out),
            ]
        )
        assert rc == 0
        files = sorted(out.glob("portfolio_heat_*.json"))
        assert files
        report = json.loads(files[-1].read_text())
        assert report["open_risk_pct"] == 0.5
        assert report["sector_exposure"] == {"Technology": 5.0}
        md_files = sorted(out.glob("portfolio_heat_*.md"))
        assert md_files

    def test_main_json_only(self, tmp_path):
        state = tmp_path / "theses"
        state.mkdir()
        out = tmp_path / "out"
        rc = portfolio_heat.main(
            [
                "--state-dir",
                str(state),
                "--account-size",
                "100000",
                "--json-only",
                "--output-dir",
                str(out),
            ]
        )
        assert rc == 0
        assert sorted(out.glob("portfolio_heat_*.json"))
        assert not sorted(out.glob("portfolio_heat_*.md"))

    def test_main_account_size_from_profile(self, tmp_path):
        state = tmp_path / "theses"
        state.mkdir()
        profile = tmp_path / "profile.json"
        profile.write_text(
            json.dumps({"account_size": 150000, "max_portfolio_heat_pct": 6.0, "max_positions": 6})
        )
        out = tmp_path / "out"
        rc = portfolio_heat.main(
            ["--state-dir", str(state), "--profile", str(profile), "--output-dir", str(out)]
        )
        assert rc == 0
        report = json.loads(sorted(out.glob("portfolio_heat_*.json"))[-1].read_text())
        assert report["account_size"] == 150000

    def test_main_missing_account_size_errors(self, tmp_path):
        state = tmp_path / "theses"
        state.mkdir()
        with pytest.raises(SystemExit):
            portfolio_heat.main(["--state-dir", str(state), "--output-dir", str(tmp_path / "o")])

    def test_main_missing_state_dir_errors(self, tmp_path):
        rc = portfolio_heat.main(
            [
                "--state-dir",
                str(tmp_path / "nope"),
                "--account-size",
                "100000",
                "--output-dir",
                str(tmp_path / "o"),
            ]
        )
        assert rc == 1
