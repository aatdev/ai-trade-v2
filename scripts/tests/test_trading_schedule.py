"""Tests for scripts/run_trading_schedule.py (stdlib-only orchestrator)."""

import datetime as dt
import importlib.util
import json
import os
import types
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "run_trading_schedule.py"
_spec = importlib.util.spec_from_file_location("run_trading_schedule", _MODULE_PATH)
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


# --------------------------------------------------------------------------- #
# Calendar gates
# --------------------------------------------------------------------------- #
def test_weekend_is_not_a_trading_day():
    assert ts.is_us_trading_day(dt.date(2026, 6, 6)) is False  # Saturday
    assert ts.is_us_trading_day(dt.date(2026, 6, 7)) is False  # Sunday


def test_holiday_is_not_a_trading_day():
    assert ts.is_us_trading_day(dt.date(2026, 1, 1)) is False  # New Year
    assert ts.is_us_trading_day(dt.date(2026, 7, 3)) is False  # July 4 (observed)
    assert ts.is_us_trading_day(dt.date(2026, 12, 25)) is False  # Christmas


def test_normal_weekday_is_a_trading_day():
    assert ts.is_us_trading_day(dt.date(2026, 6, 2)) is True  # Tuesday
    assert ts.is_us_trading_day(dt.date(2026, 6, 5)) is True  # Friday


def test_first_sunday_detection():
    assert ts.is_first_sunday(dt.date(2026, 6, 7)) is True  # 1st Sunday of June
    assert ts.is_first_sunday(dt.date(2026, 6, 14)) is False  # 2nd Sunday
    assert ts.is_first_sunday(dt.date(2026, 6, 1)) is False  # Monday
    assert ts.is_first_sunday(dt.date(2026, 2, 1)) is True  # Feb 1 2026 is a Sunday


# --------------------------------------------------------------------------- #
# Exposure gate parsing (fail-safe behaviour)
# --------------------------------------------------------------------------- #
def test_missing_gate_file_defaults_to_restrict(tmp_path):
    dec = ts.read_decision(tmp_path / "nope.json")
    assert dec["decision"] == "restrict"
    assert dec["degraded"] is True


def test_valid_allow_decision(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"decision": "allow", "rationale": "broad breadth"}))
    dec = ts.read_decision(p)
    assert dec["decision"] == "allow"
    assert dec["degraded"] is False


def test_decision_is_normalized_lowercase(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"decision": "Cash-Priority"}))
    assert ts.read_decision(p)["decision"] == "cash-priority"


def test_unknown_decision_value_fails_safe(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"decision": "yolo"}))
    dec = ts.read_decision(p)
    assert dec["decision"] == "restrict"
    assert dec["degraded"] is True


def test_unparseable_gate_file_fails_safe(tmp_path):
    p = tmp_path / "g.json"
    p.write_text("{not json")
    dec = ts.read_decision(p)
    assert dec["decision"] == "restrict"
    assert dec["degraded"] is True


# --------------------------------------------------------------------------- #
# Prompt builders embed the gate path and required JSON contract
# --------------------------------------------------------------------------- #
def test_regime_prompt_contains_gate_path_and_decision_enum():
    gate = Path("/tmp/exposure_decision_2026-06-02.json")
    prompt = ts.regime_prompt("2026-06-02", gate, quick=False)
    assert str(gate) in prompt
    assert "allow" in prompt and "restrict" in prompt and "cash-priority" in prompt
    assert "market-regime-daily" in prompt


def test_opportunity_prompt_references_allow_gate_and_watchlist():
    gate = Path("/tmp/g.json")
    wl = Path("/tmp/watchlist_2026-06-02.json")
    prompt = ts.opportunity_prompt("2026-06-02", gate, wl)
    assert str(wl) in prompt
    assert "swing-opportunity-daily" in prompt
    assert "do NOT place any orders" in prompt


def test_regime_prompt_mandates_finishing_and_gate_write():
    gate = Path("/tmp/exposure_decision_2026-06-12.json")
    prompt = ts.regime_prompt("2026-06-12", gate, quick=True)
    # The hardened prompt must forbid stopping on a narration and require the file.
    assert "MANDATORY" in prompt and "FINAL action" in prompt
    assert "no tool call" in prompt.lower() or "tool calls" in prompt.lower()


def test_regime_finish_prompt_targets_gate():
    gate = Path("/tmp/exposure_decision_2026-06-12.json")
    prompt = ts.regime_finish_prompt("2026-06-12", gate)
    assert str(gate) in prompt
    assert "allow" in prompt and "restrict" in prompt and "cash-priority" in prompt


# --------------------------------------------------------------------------- #
# Regime gate retry (headless claude can end before writing the gate)
# --------------------------------------------------------------------------- #
def test_run_regime_gate_retries_when_gate_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "SCHEDULE_DIR", tmp_path / "schedule")
    gate = ts.decision_path("2026-06-12")
    calls = []

    def fake_run_claude(prompt, *, label, dry_run, timeout):
        calls.append(label)
        # Emulate: first pass narrates and exits without the gate; retry writes it.
        if "[gate retry]" in label:
            gate.parent.mkdir(parents=True, exist_ok=True)
            gate.write_text(json.dumps({"decision": "allow", "rationale": "ok"}))
        return True

    monkeypatch.setattr(ts, "run_claude", fake_run_claude)
    args = types.SimpleNamespace(dry_run=False, timeout=60)
    ok, dec = ts.run_regime_gate(
        "2026-06-12", gate, quick=True, label="market-regime-daily (premarket re-check)", args=args
    )
    assert len(calls) == 2 and "[gate retry]" in calls[1]
    assert dec["decision"] == "allow"
    assert not dec.get("degraded")


def test_run_regime_gate_no_retry_when_first_pass_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "SCHEDULE_DIR", tmp_path / "schedule")
    gate = ts.decision_path("2026-06-12")
    calls = []

    def fake_run_claude(prompt, *, label, dry_run, timeout):
        calls.append(label)
        gate.parent.mkdir(parents=True, exist_ok=True)
        gate.write_text(json.dumps({"decision": "restrict", "rationale": "ok"}))
        return True

    monkeypatch.setattr(ts, "run_claude", fake_run_claude)
    args = types.SimpleNamespace(dry_run=False, timeout=60)
    ok, dec = ts.run_regime_gate(
        "2026-06-12", gate, quick=False, label="market-regime-daily (evening EOD)", args=args
    )
    assert len(calls) == 1
    assert dec["decision"] == "restrict"


def test_run_regime_gate_dry_run_single_call(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "SCHEDULE_DIR", tmp_path / "schedule")
    gate = ts.decision_path("2026-06-12")
    calls = []

    def fake_run_claude(prompt, *, label, dry_run, timeout):
        calls.append(label)
        return True

    monkeypatch.setattr(ts, "run_claude", fake_run_claude)
    args = types.SimpleNamespace(dry_run=True, timeout=60)
    ok, dec = ts.run_regime_gate(
        "2026-06-12", gate, quick=True, label="market-regime-daily (premarket re-check)", args=args
    )
    # Dry-run never retries (no gate write expected); fail-safe restrict from read_decision.
    assert len(calls) == 1
    assert dec["decision"] == "restrict"


# --------------------------------------------------------------------------- #
# Slot dispatch / CLI gating (dry-run, no network)
# --------------------------------------------------------------------------- #
def test_premarket_skips_on_non_trading_day(monkeypatch, capsys):
    rc = ts.main(["--slot", "premarket", "--date", "2026-01-01", "--dry-run", "--no-telegram"])
    assert rc == 0
    assert "not a US trading day" in capsys.readouterr().out


def test_monthly_skips_when_not_first_sunday(capsys):
    rc = ts.main(["--slot", "monthly", "--date", "2026-06-14", "--dry-run", "--no-telegram"])
    assert rc == 0
    assert "not the first Sunday" in capsys.readouterr().out


def test_evening_prep_dry_run_runs_without_network(monkeypatch):
    """Dry-run on a trading day exercises the full evening path without calling
    claude or Telegram."""
    calls = {"claude": 0, "notify": 0}

    def fake_run_claude(prompt, *, label, dry_run, timeout):
        calls["claude"] += 1
        return True

    def fake_notify(text, *, dry_run, no_telegram, file=None):
        calls["notify"] += 1

    monkeypatch.setattr(ts, "run_claude", fake_run_claude)
    monkeypatch.setattr(ts, "notify", fake_notify)
    rc = ts.main(["--slot", "evening-prep", "--date", "2026-06-02", "--dry-run", "--no-telegram"])
    assert rc == 0
    # regime gate is fail-safe restrict (no gate file written by the fake), so
    # only the regime workflow runs and exactly one notification is sent.
    assert calls["claude"] == 1
    assert calls["notify"] == 1


def test_evening_prep_runs_opportunity_when_gate_allows(monkeypatch, tmp_path):
    """When the regime step writes an `allow` gate, the hybrid screen runs."""
    monkeypatch.setattr(ts, "SCHEDULE_DIR", tmp_path / "schedule")
    monkeypatch.setattr(ts, "SCREENERS_DIR", tmp_path / "screeners")
    monkeypatch.setattr(ts, "PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(ts, "JOURNAL_DIR", tmp_path / "journal")
    monkeypatch.setattr(ts, "LOCK_FILE", tmp_path / "schedule.lock")

    def fake_run_claude(prompt, *, label, dry_run, timeout):
        if "market-regime-daily" in label:
            # emulate the regime workflow writing the gate file
            gate = ts.decision_path("2026-06-02")
            gate.parent.mkdir(parents=True, exist_ok=True)
            gate.write_text(json.dumps({"decision": "allow", "rationale": "ok"}))
        return True

    sent = []
    monkeypatch.setattr(ts, "run_claude", fake_run_claude)
    monkeypatch.setattr(ts, "run_skill_script", lambda *a, **k: None)
    monkeypatch.setattr(ts, "tv_available", lambda **k: True)
    monkeypatch.setattr(ts, "_sync_tv_alerts", lambda wl, args: "")
    monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
    rc = ts.main(["--slot", "evening-prep", "--date", "2026-06-02", "--no-telegram"])
    assert rc == 0
    assert sent and "ALLOW" in sent[0]


# --------------------------------------------------------------------------- #
# Single-run lock
# --------------------------------------------------------------------------- #
def _run_slotted(monkeypatch, lock, *extra, calls=None):
    """Invoke main() for a stubbed evening-prep slot, with LOCK_FILE patched."""
    monkeypatch.setattr(ts, "LOCK_FILE", lock)

    def _slot(date_str, args):
        if calls is not None:
            calls.append(date_str)
        return 0

    monkeypatch.setattr(ts, "SLOTS", {"evening-prep": _slot})
    return ts.main(
        ["--slot", "evening-prep", "--date", "2026-06-02", "--force", "--no-telegram", *extra]
    )


def test_lock_busy_returns_exit_busy_without_running(monkeypatch, tmp_path):
    lock = tmp_path / "schedule.lock"
    lock.write_text(str(os.getpid()))  # a live PID (this process) already holds it
    calls = []
    rc = _run_slotted(monkeypatch, lock, calls=calls)
    assert rc == ts.EXIT_BUSY
    assert calls == []  # slot body never ran
    assert lock.read_text().strip() == str(os.getpid())  # foreign lock left intact


def test_lock_acquired_and_released_on_success(monkeypatch, tmp_path):
    lock = tmp_path / "schedule.lock"
    calls = []
    rc = _run_slotted(monkeypatch, lock, calls=calls)
    assert rc == 0
    assert calls == ["2026-06-02"]  # slot ran
    assert not lock.exists()  # released in finally


def test_dry_run_ignores_lock(monkeypatch, tmp_path):
    lock = tmp_path / "schedule.lock"
    lock.write_text(str(os.getpid()))  # held — a real run would refuse
    calls = []
    rc = _run_slotted(monkeypatch, lock, "--dry-run", calls=calls)
    assert rc == 0
    assert calls == ["2026-06-02"]  # dry-run still ran
    assert lock.read_text().strip() == str(os.getpid())  # lock untouched


def test_stale_lock_from_dead_pid_is_reclaimed(monkeypatch, tmp_path):
    lock = tmp_path / "schedule.lock"
    lock.write_text("999999")  # PID that does not exist
    calls = []
    rc = _run_slotted(monkeypatch, lock, calls=calls)
    assert rc == 0
    assert calls == ["2026-06-02"]
    assert not lock.exists()


# --------------------------------------------------------------------------- #
# Message builders mirror the MyNotes journal templates
# --------------------------------------------------------------------------- #
def test_premarket_msg_has_journal_sections_and_signals():
    dec = {
        "decision": "restrict",
        "net_exposure_ceiling_pct": 46,
        "rationale": "narrow breadth",
        "key_signals": ["Breadth 43/100", "Distribution days at threshold"],
    }
    msg = ts.build_premarket_msg("2026-06-02", dec, None)
    for section in ("📌 ВЕРДИКТ", "🧭 СВОДКА", "💬 ОБОСНОВАНИЕ", "✅ ДЕЙСТВИЕ"):
        assert section in msg
    assert "RESTRICT (потолок ~46%)" in msg
    assert "• Новые свинги: нет" in msg
    assert "• Breadth 43/100" in msg


def test_premarket_msg_allow_invites_orders():
    dec = {"decision": "allow", "rationale": "ok"}
    msg = ts.build_premarket_msg("2026-06-02", dec, None)
    assert "• Новые свинги: да" in msg
    assert "bracket-ордера" in msg


def test_degraded_decision_shows_failsafe_flag():
    dec = {"decision": "restrict", "degraded": True, "rationale": "no gate"}
    assert "⚠️FAIL-SAFE" in ts.build_evening_closed_msg("2026-06-02", dec)


def test_candidate_lines_skips_missing_fields():
    cands = [
        {"ticker": "GOOGL", "setup": "CANSLIM", "pivot": 408.6, "stop": 376, "target": 473},
        {"ticker": "NVDA"},  # no levels -> just the ticker
    ]
    out = ts._candidate_lines(cands)
    assert "• GOOGL — CANSLIM · вход $408.6 / стоп $376 / цель $473" in out
    assert "• NVDA" in out


def test_evening_allow_msg_lists_candidates():
    dec = {"decision": "allow", "net_exposure_ceiling_pct": 60, "rationale": "ok"}
    cands = [{"ticker": "GOOGL", "setup": "VCP", "pivot": 408}]
    msg = ts.build_evening_allow_msg("2026-06-02", dec, "reports/schedule/watchlist.json", cands)
    assert "🧭 КАНДИДАТЫ" in msg
    assert "• GOOGL — VCP" in msg
    assert "Watchlist на завтра: 1 кандидат(ов)" in msg


def test_evening_allow_msg_handles_empty_watchlist():
    dec = {"decision": "allow", "rationale": "ok"}
    msg = ts.build_evening_allow_msg("2026-06-02", dec, "wl.json", [])
    assert "ни один не прошёл гейты" in msg


def test_monthly_msg_has_rules_and_action():
    data = {
        "trades_closed": 7,
        "win_rate_pct": 57,
        "avg_R": 0.8,
        "rule_changes_for_next_month": ["tighter stops"],
    }
    msg = ts.build_monthly_msg("2026-06-07", data)
    assert "🧭 ПРАВИЛА НА СЛЕД. МЕСЯЦ" in msg
    assert "• tighter stops" in msg
    assert "monthly/2026-06.md" in msg


# --------------------------------------------------------------------------- #
# .env loading (cron/launchd start with a bare environment)
# --------------------------------------------------------------------------- #
class TestLoadEnvFile:
    def test_parses_export_and_plain_lines(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(
            "# comment\n"
            "export _TS_TEST_TOKEN=abc:123\n"
            "_TS_TEST_CHAT='-10042'\n"
            "\n"
            "not a key value line\n"
        )
        os.environ.pop("_TS_TEST_TOKEN", None)
        os.environ.pop("_TS_TEST_CHAT", None)
        try:
            ts.load_env_file(env)
            assert os.environ["_TS_TEST_TOKEN"] == "abc:123"
            assert os.environ["_TS_TEST_CHAT"] == "-10042"
        finally:
            os.environ.pop("_TS_TEST_TOKEN", None)
            os.environ.pop("_TS_TEST_CHAT", None)

    def test_existing_environment_wins(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("export _TS_TEST_TOKEN=from_file\n")
        monkeypatch.setenv("_TS_TEST_TOKEN", "from_shell")
        ts.load_env_file(env)
        assert os.environ["_TS_TEST_TOKEN"] == "from_shell"

    def test_missing_file_is_noop(self, tmp_path):
        ts.load_env_file(tmp_path / "absent.env")  # must not raise


def test_main_loads_env_file(monkeypatch):
    called = []
    monkeypatch.setattr(ts, "load_env_file", lambda *a, **k: called.append(True))
    rc = ts.main(["--slot", "premarket", "--date", "2026-01-01", "--dry-run", "--no-telegram"])
    assert rc == 0
    assert called == [True]


# --------------------------------------------------------------------------- #
# claude CLI resolution (cron PATH lacks ~/.local/bin)
# --------------------------------------------------------------------------- #
class TestResolveClaudeBin:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_BIN", "/opt/custom/claude")
        assert ts.resolve_claude_bin() == "/opt/custom/claude"

    def test_found_on_path(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_BIN", raising=False)
        monkeypatch.setattr(ts.shutil, "which", lambda name: "/somewhere/claude")
        assert ts.resolve_claude_bin() == "claude"

    def test_falls_back_to_known_locations(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CLAUDE_BIN", raising=False)
        monkeypatch.setattr(ts.shutil, "which", lambda name: None)
        fake = tmp_path / "claude"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setattr(ts, "CLAUDE_FALLBACK_PATHS", [fake])
        assert ts.resolve_claude_bin() == str(fake)

    def test_nothing_found_returns_bare_name(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_BIN", raising=False)
        monkeypatch.setattr(ts.shutil, "which", lambda name: None)
        monkeypatch.setattr(ts, "CLAUDE_FALLBACK_PATHS", [])
        assert ts.resolve_claude_bin() == "claude"


# --------------------------------------------------------------------------- #
# Auto mode: shared fixtures
# --------------------------------------------------------------------------- #
def _patch_trading_dirs(monkeypatch, tmp_path):
    """Redirect every trading-data dir constant into tmp_path."""
    monkeypatch.setattr(ts, "TRADING_DATA_DIR", tmp_path)
    monkeypatch.setattr(ts, "SCHEDULE_DIR", tmp_path / "schedule")
    monkeypatch.setattr(ts, "MARKET_DIR", tmp_path / "market")
    monkeypatch.setattr(ts, "SCREENERS_DIR", tmp_path / "screeners")
    monkeypatch.setattr(ts, "PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(ts, "JOURNAL_DIR", tmp_path / "journal")
    monkeypatch.setattr(ts, "SIGNALS_STATE_FILE", tmp_path / "logs" / "intraday_signals_state.json")
    # Never touch the production lock: a non-dry-run ts.main() in a test would
    # otherwise race (and block) the real cron autopilot via trading-data/logs/.
    monkeypatch.setattr(ts, "LOCK_FILE", tmp_path / "logs" / "trading_schedule.lock")
    monkeypatch.setattr(ts, "ALERTS_STATE_FILE", tmp_path / "logs" / "watchlist_alerts_state.json")
    for d in ("schedule", "market", "screeners", "plans", "journal", "logs"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _gate(tmp_path, date_str, decision):
    return _write_json(
        tmp_path / "schedule" / f"exposure_decision_{date_str}.json",
        {"decision": decision, "rationale": "test"},
    )


def _watchlist_file(tmp_path, date_str, candidates):
    return _write_json(
        tmp_path / "schedule" / f"watchlist_{date_str}.json",
        {
            "workflow": "swing-opportunity-daily",
            "date": date_str,
            "exposure_decision": "allow",
            "candidates": candidates,
        },
    )


def _heat_file(tmp_path, positions=(), slots=6, heat_dollars=9000.0):
    return _write_json(
        tmp_path / "journal" / "portfolio_heat_2026-06-11_120000.json",
        {
            "account_size": 150000.0,
            "remaining_position_slots": slots,
            "remaining_heat_dollars": heat_dollars,
            "positions": list(positions),
        },
    )


_NVDA_CANDIDATE = {
    "ticker": "NVDA",
    "side": "long",
    "setup": "VCP Pre-breakout",
    "pivot": 155.2,
    "worst_entry": 157.5,
    "stop": 151.3,
    "target": 163.7,
    "shares": 380,
    "risk_dollars": 2356.0,
    "score": 78.5,
}


# --------------------------------------------------------------------------- #
# Intraday slot
# --------------------------------------------------------------------------- #
class TestIntradaySlot:
    def test_skips_outside_window_without_force(self, monkeypatch, tmp_path, capsys):
        _patch_trading_dirs(monkeypatch, tmp_path)
        monkeypatch.setattr(ts, "_now_time", lambda: dt.time(10, 0))
        rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--no-telegram"])
        assert rc == 0
        assert "вне окна" in capsys.readouterr().out

    def test_open_signal_sent_and_deduped(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _gate(tmp_path, "2026-06-11", "allow")
        _watchlist_file(tmp_path, "2026-06-11", [_NVDA_CANDIDATE])
        _heat_file(tmp_path)
        monkeypatch.setattr(
            ts.tsig, "fetch_quotes", lambda tickers, **k: {"NVDA": {"price": 156.0}}
        )
        sent = []
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))

        rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--force", "--no-telegram"])
        assert rc == 0
        assert len(sent) == 1
        assert "ОТКРОЙ ЛОНГ NVDA" in sent[0]
        assert "380" in sent[0]
        # state persisted -> second run is silent
        rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--force", "--no-telegram"])
        assert rc == 0
        assert len(sent) == 1

    def test_stop_hit_signal_for_open_position(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _gate(tmp_path, "2026-06-11", "restrict")
        _heat_file(
            tmp_path,
            positions=[{"ticker": "AAPL", "entry_price": 100.0, "shares": 100, "stop_loss": 95.0}],
        )
        monkeypatch.setattr(ts.tsig, "fetch_quotes", lambda tickers, **k: {"AAPL": {"price": 94.0}})
        sent = []
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
        rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--force", "--no-telegram"])
        assert rc == 0
        assert sent and "AAPL" in sent[0] and "стоп" in sent[0].lower()

    def test_quotes_error_returns_1(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _gate(tmp_path, "2026-06-11", "allow")
        _watchlist_file(tmp_path, "2026-06-11", [_NVDA_CANDIDATE])

        def boom(tickers, **k):
            raise ts.tsig.QuotesError("scanner down")

        monkeypatch.setattr(ts.tsig, "fetch_quotes", boom)
        rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--force", "--no-telegram"])
        assert rc == 1

    def test_no_tickers_is_noop(self, monkeypatch, tmp_path, capsys):
        _patch_trading_dirs(monkeypatch, tmp_path)
        rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--force", "--no-telegram"])
        assert rc == 0
        assert "нет тикеров" in capsys.readouterr().out

    def test_dry_run_never_touches_network(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _watchlist_file(tmp_path, "2026-06-11", [_NVDA_CANDIDATE])

        def boom(tickers, **k):
            raise AssertionError("network call in dry-run")

        monkeypatch.setattr(ts.tsig, "fetch_quotes", boom)
        rc = ts.main(
            ["--slot", "intraday", "--date", "2026-06-11", "--force", "--dry-run", "--no-telegram"]
        )
        assert rc == 0

    def test_non_trading_day_skips(self, monkeypatch, tmp_path, capsys):
        _patch_trading_dirs(monkeypatch, tmp_path)
        rc = ts.main(["--slot", "intraday", "--date", "2026-06-13", "--no-telegram"])
        assert rc == 0
        assert "not a US trading day" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Evening hybrid pipeline
# --------------------------------------------------------------------------- #
def _plan_fixture(tmp_path):
    return _write_json(
        tmp_path / "plans" / "breakout_trade_plan_2026-06-11_221500.json",
        {
            "actionable_orders": [
                {
                    "symbol": "NVDA",
                    "sector": "Technology",
                    "composite_score": 78.5,
                    "execution_state": "Pre-breakout",
                    "plan_type": "pending_breakout",
                    "trade_plan": {
                        "signal_entry": 155.2,
                        "worst_entry": 157.5,
                        "stop_loss_price": 151.3,
                        "target_price": 163.7,
                        "shares": 380,
                        "risk_dollars": 2356.0,
                    },
                }
            ],
            "revalidation": [],
        },
    )


class TestEveningHybrid:
    def _run(
        self,
        monkeypatch,
        tmp_path,
        *,
        decision,
        validation=None,
        market_top=None,
        short_candidates=None,
        tv_up=True,
        quotes_map=None,
        heat_ok=True,
    ):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _write_json(tmp_path / "trading_profile.json", {"account_size": 150000})
        plan_path = _plan_fixture(tmp_path)
        short_path = _write_json(
            tmp_path / "screeners" / "swing_short_screener_2026-06-11_221500.json",
            {"candidates": short_candidates or []},
        )
        if market_top is not None:
            _write_json(tmp_path / "market" / "market_top_2026-06-11_120000.json", market_top)

        script_calls = []
        script_cmds = {}

        def fake_run_skill_script(cmd, *, label, dry_run, timeout, output_glob=None):
            script_calls.append(label)
            script_cmds[label] = [str(c) for c in cmd]
            if "vcp" in label:
                return _write_json(
                    tmp_path / "screeners" / "vcp_screener_2026-06-11_221000.json", {"results": []}
                )
            if "heat" in label:
                return _heat_file(tmp_path) if heat_ok else None
            if "planner" in label:
                return plan_path
            if "short" in label:
                return short_path
            return None

        def fake_run_claude(prompt, *, label, dry_run, timeout):
            if "market-regime-daily" in label:
                _gate(tmp_path, "2026-06-11", decision)
            if "validation" in label and validation is not None:
                _write_json(
                    tmp_path / "schedule" / "watchlist_validation_2026-06-11.json", validation
                )
                return validation is not False
            return True

        sent = []
        synced = []
        monkeypatch.setattr(ts, "run_skill_script", fake_run_skill_script)
        monkeypatch.setattr(ts, "run_claude", fake_run_claude)
        monkeypatch.setattr(ts, "tv_available", lambda **k: tv_up)
        # The short-branch earnings gate fetches quotes; never hit the network.
        monkeypatch.setattr(ts.tsig, "fetch_quotes", lambda tickers, **k: quotes_map or {})
        monkeypatch.setattr(
            ts.talerts,
            "sync_watchlist_alerts",
            lambda wl, state_path, **k: (
                synced.append(wl)
                or {
                    "created": 3,
                    "deleted": 1,
                    "kept": 0,
                    "skipped": 0,
                    "errors": 0,
                    "error_details": [],
                }
            ),
        )
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
        rc = ts.main(["--slot", "evening-prep", "--date", "2026-06-11", "--no-telegram"])
        self.synced = synced
        self.script_cmds = script_cmds
        return rc, script_calls, sent

    def test_allow_runs_deterministic_chain_and_builds_watchlist(self, monkeypatch, tmp_path):
        rc, calls, sent = self._run(monkeypatch, tmp_path, decision="allow")
        assert rc == 0
        assert any("vcp" in c for c in calls)
        assert any("heat" in c for c in calls)
        assert any("planner" in c for c in calls)
        assert any("ingest" in c for c in calls)
        wl = json.loads(
            (tmp_path / "schedule" / "watchlist_2026-06-11.json").read_text(encoding="utf-8")
        )
        assert wl["candidates"][0]["ticker"] == "NVDA"
        assert wl["candidates"][0]["shares"] == 380
        assert sent and "NVDA" in sent[0]

    def test_heat_failure_blocks_long_pipeline_fail_safe(self, monkeypatch, tmp_path):
        """No heat ledger → the planner would assume a zero-risk baseline with
        real positions open. Fail-safe: skip the screen, block new risk."""
        rc, calls, sent = self._run(monkeypatch, tmp_path, decision="allow", heat_ok=False)
        assert rc == 0
        assert not any("vcp" in c for c in calls)
        assert not any("planner" in c for c in calls)
        wl = json.loads(
            (tmp_path / "schedule" / "watchlist_2026-06-11.json").read_text(encoding="utf-8")
        )
        assert wl["candidates"] == []
        assert any("fail-safe" in m for m in sent)

    def test_validation_reject_drops_candidate(self, monkeypatch, tmp_path):
        validation = {
            "date": "2026-06-11",
            "verdicts": [{"ticker": "NVDA", "verdict": "reject", "note": "broken base"}],
        }
        rc, _, sent = self._run(monkeypatch, tmp_path, decision="allow", validation=validation)
        assert rc == 0
        wl = json.loads(
            (tmp_path / "schedule" / "watchlist_2026-06-11.json").read_text(encoding="utf-8")
        )
        assert wl["candidates"] == []
        assert wl["rejected_by_validation"][0]["ticker"] == "NVDA"

    def test_restrict_with_market_pressure_runs_short_screen(self, monkeypatch, tmp_path):
        market_top = {
            "composite": {"composite_score": 51.5},
            "components": {"distribution_days": {"effective_count": 6.0}},
            "follow_through_day": {"ftd_detected": False},
        }
        shorts = [
            {
                "symbol": "NFLX",
                "grade": "A",
                "composite_score": 82.5,
                "trade_levels": {"entry": 245.0, "stop": 260.0, "target_2r": 215.0},
            }
        ]
        rc, calls, sent = self._run(
            monkeypatch,
            tmp_path,
            decision="restrict",
            market_top=market_top,
            short_candidates=shorts,
        )
        assert rc == 0
        assert any("short" in c for c in calls)
        assert any("heat" in c for c in calls)  # fresh ledger for tomorrow's monitor
        assert any("ingest" in c for c in calls)  # shorts register theses too
        short_cmd = next(v for k, v in self.script_cmds.items() if "swing-short-screener" in k)
        assert "--full-sp500" in short_cmd  # full universe, not the first 100 names
        ingest_cmd = next(v for k, v in self.script_cmds.items() if "ingest" in k)
        assert "swing-short-screener" in ingest_cmd
        wl = json.loads(
            (tmp_path / "schedule" / "watchlist_2026-06-11.json").read_text(encoding="utf-8")
        )
        assert wl["candidates"][0]["side"] == "short"
        assert wl["candidates"][0]["shares"] == 100  # 1% of 150k / 15
        assert sent and "ШОРТ" in sent[0]

    def test_short_candidates_before_earnings_are_dropped(self, monkeypatch, tmp_path):
        """Plan rule 6.4: a short reporting within the earnings gate must not
        reach the watchlist; the Telegram digest names the exclusion."""
        market_top = {
            "composite": {"composite_score": 51.5},
            "components": {"distribution_days": {"effective_count": 6.0}},
            "follow_through_day": {"ftd_detected": False},
        }
        shorts = [
            {
                "symbol": "NFLX",
                "grade": "A",
                "composite_score": 82.5,
                "trade_levels": {"entry": 245.0, "stop": 260.0, "target_2r": 215.0},
            },
            {
                "symbol": "ADBE",
                "grade": "A",
                "composite_score": 80.0,
                "trade_levels": {"entry": 210.0, "stop": 218.5, "target_2r": 193.0},
            },
        ]
        near = (dt.date.today() + dt.timedelta(days=2)).isoformat()  # ≤2 weekdays
        far = (dt.date.today() + dt.timedelta(days=40)).isoformat()  # ≥28 weekdays
        quotes_map = {
            "ADBE": {"price": 210.0, "earnings_date": near},
            "NFLX": {"price": 245.0, "earnings_date": far},
        }
        rc, _, sent = self._run(
            monkeypatch,
            tmp_path,
            decision="restrict",
            market_top=market_top,
            short_candidates=shorts,
            quotes_map=quotes_map,
        )
        assert rc == 0
        wl = json.loads(
            (tmp_path / "schedule" / "watchlist_2026-06-11.json").read_text(encoding="utf-8")
        )
        assert [c["ticker"] for c in wl["candidates"]] == ["NFLX"]
        assert any("Исключены перед отчётом" in m and "ADBE" in m for m in sent)

    def test_restrict_without_market_top_skips_shorts(self, monkeypatch, tmp_path):
        rc, calls, sent = self._run(monkeypatch, tmp_path, decision="restrict")
        assert rc == 0
        assert not any("short" in c for c in calls)
        assert sent and "ЗАКРЫТ" in sent[0]

    def test_fresh_ftd_blocks_shorts(self, monkeypatch, tmp_path):
        market_top = {
            "composite": {"composite_score": 51.5},
            "components": {"distribution_days": {"effective_count": 6.0}},
            "follow_through_day": {"ftd_detected": True},
        }
        rc, calls, _ = self._run(monkeypatch, tmp_path, decision="restrict", market_top=market_top)
        assert rc == 0
        assert not any("short" in c for c in calls)


# --------------------------------------------------------------------------- #
# Watchlist freshness (stale lists must not arm OPEN signals)
# --------------------------------------------------------------------------- #
class TestWatchlistFreshness:
    def test_today_and_prev_trading_day_are_fresh(self):
        today = dt.date(2026, 6, 11)  # Thursday
        assert ts._watchlist_is_fresh({"date": "2026-06-11"}, today) is True
        assert ts._watchlist_is_fresh({"date": "2026-06-10"}, today) is True
        assert ts._watchlist_is_fresh({"date": "2026-06-09"}, today) is False

    def test_monday_accepts_friday_list(self):
        monday = dt.date(2026, 6, 8)
        assert ts._watchlist_is_fresh({"date": "2026-06-05"}, monday) is True
        assert ts._watchlist_is_fresh({"date": "2026-06-04"}, monday) is False

    def test_bad_or_missing_date_is_stale(self):
        today = dt.date(2026, 6, 11)
        assert ts._watchlist_is_fresh({}, today) is False
        assert ts._watchlist_is_fresh({"date": "junk"}, today) is False
        assert ts._watchlist_is_fresh(None, today) is False

    def test_intraday_stale_watchlist_does_not_arm_open_signals(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _gate(tmp_path, "2026-06-11", "allow")
        _write_json(
            tmp_path / "schedule" / "watchlist_2026-06-08.json",
            {"date": "2026-06-08", "candidates": [_NVDA_CANDIDATE]},
        )
        _heat_file(
            tmp_path,
            positions=[{"ticker": "AAPL", "entry_price": 100.0, "shares": 100, "stop_loss": 95.0}],
        )
        monkeypatch.setattr(
            ts.tsig,
            "fetch_quotes",
            lambda tickers, **k: {"NVDA": {"price": 156.0}, "AAPL": {"price": 94.0}},
        )
        sent = []
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
        rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--force", "--no-telegram"])
        assert rc == 0
        assert sent and "AAPL" in sent[0]  # open positions still managed
        assert "ОТКРОЙ ЛОНГ NVDA" not in sent[0]  # stale levels never fire entries

    def test_premarket_warns_on_stale_watchlist(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _write_json(
            tmp_path / "schedule" / "watchlist_2026-06-08.json",
            {"date": "2026-06-08", "candidates": []},
        )
        monkeypatch.setattr(ts, "run_skill_script", lambda *a, **k: None)
        monkeypatch.setattr(
            ts, "run_regime_gate",
            lambda *a, **k: (True, {"decision": "allow", "rationale": "x"}),
        )
        monkeypatch.setattr(ts, "tv_available", lambda **k: True)
        sent = []
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
        rc = ts.main(["--slot", "premarket", "--date", "2026-06-11", "--no-telegram"])
        assert rc == 0
        assert sent and "Watchlist устарел" in sent[0]


# --------------------------------------------------------------------------- #
# signals.md parsing (must mirror the REAL ticker-analysis block format)
# --------------------------------------------------------------------------- #
_REAL_SIGNALS_MD = """# Trading Signals Journal

---

## 2026-06-10 — AOS — 🔴 SELL (breakdown)

- **Trigger для Short:** close 1D < $57.00
- **Entry (Short):** $55.50–$57.00
- **Stop:** $59.00
- **T1 / T2 / T3:** $53.00 / $50.00 / $47.00

---

## 2026-06-12 — AOS — 🟢 BUY (reversal)

- **Trigger для Long:** close 1D > $60.00
- **Entry (Long):** $58.00–$60.50
- **Stop:** $56.00
- **T1 / T2 / T3:** $64.00 / $70.00 / $78.00
- **Альтернатива (Short):** close < $55 → stop $58, T1 $50

---

## 2026-06-12 — ALLE — 🟡 HOLD (отскок к сопротивлению)

- **Trigger для Long:** close 1D > $135.50
- **Stop:** $129.40
- **T1 / T2 / T3:** $138.50 / $144.50 / $148.80
---
"""


class TestParseSignalsMd:
    def _write(self, monkeypatch, tmp_path, text=_REAL_SIGNALS_MD):
        _patch_trading_dirs(monkeypatch, tmp_path)
        f = tmp_path / "analysis" / "signals.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    def test_parses_real_format_latest_block_wins(self, monkeypatch, tmp_path):
        self._write(monkeypatch, tmp_path)
        sig = ts._parse_signals_md("AOS")
        assert sig is not None
        assert sig["date"] == "2026-06-12"
        assert sig["direction"] == "long"
        assert sig["trigger"] == 60.0
        assert sig["stop"] == 56.0  # not the alternative-scenario $58
        assert (sig["t1"], sig["t2"], sig["t3"]) == (64.0, 70.0, 78.0)
        assert (sig["entry_low"], sig["entry_high"]) == (58.0, 60.5)

    def test_hold_block_never_arms_levels(self, monkeypatch, tmp_path):
        self._write(monkeypatch, tmp_path)
        assert ts._parse_signals_md("ALLE") is None

    def test_direction_from_trigger_line_without_emoji(self, monkeypatch, tmp_path):
        md = (
            "# J\n\n---\n\n## 2026-06-12 — NVDA — анализ\n\n"
            "- **Trigger для Short:** close < $150.00\n"
            "- **Stop:** $158.00\n"
            "- **T1 / T2 / T3:** $140.00 / $135.00 / $130.00\n"
        )
        self._write(monkeypatch, tmp_path, md)
        sig = ts._parse_signals_md("NVDA")
        assert sig["direction"] == "short"
        assert sig["trigger"] == 150.0

    def test_unknown_ticker_returns_none(self, monkeypatch, tmp_path):
        self._write(monkeypatch, tmp_path)
        assert ts._parse_signals_md("TSLA") is None

    def test_missing_core_level_returns_none(self, monkeypatch, tmp_path):
        md = "# J\n\n---\n\n## 2026-06-12 — NVDA — 🟢 BUY\n\n- **Trigger для Long:** $150\n"
        self._write(monkeypatch, tmp_path, md)
        assert ts._parse_signals_md("NVDA") is None  # no Stop / T1


# --------------------------------------------------------------------------- #
# Short-branch market-pressure gate (_short_conditions)
# --------------------------------------------------------------------------- #
def _weekdays_ago(n: int) -> str:
    """ISO date n weekdays back from today."""
    d = dt.date.today()
    while n > 0:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d.isoformat()


class TestShortConditions:
    def _setup(self, monkeypatch, tmp_path, *, market_top=None, ftd=None, ibd=None):
        _patch_trading_dirs(monkeypatch, tmp_path)
        if market_top is not None:
            _write_json(tmp_path / "market" / "market_top_2026-06-11_120000.json", market_top)
        if ftd is not None:
            _write_json(tmp_path / "market" / "ftd_detector_2026-06-11_130000.json", ftd)
        if ibd is not None:
            _write_json(
                tmp_path / "market" / "ibd_distribution_day_monitor_2026-06-11_125900.json", ibd
            )

    @staticmethod
    def _pressure_top(score=30.0, dd=6.0, ftd=False):
        return {
            "composite": {"composite_score": score},
            "components": {"distribution_days": {"effective_count": dd}},
            "follow_through_day": {"ftd_detected": ftd},
        }

    @staticmethod
    def _ftd_report(state, ftd_date, *, invalidated=False):
        return {
            "market_state": {"combined_state": state},
            "sp500": {"state": state, "ftd": {"ftd_detected": state == "FTD_CONFIRMED",
                                              "ftd_date": ftd_date}},
            "nasdaq": {"state": "RALLY_ATTEMPT", "ftd": {"ftd_detected": False}},
            "ftd_invalidation": {"invalidated": invalidated},
        }

    def test_market_top_dd_fallback_enables_shorts(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, market_top=self._pressure_top())
        active, reason = ts._short_conditions()
        assert active is True
        assert "market_top" in reason

    def test_ibd_dd_count_preferred_over_market_top(self, monkeypatch, tmp_path):
        # market_top overstates DD (no rally invalidation); IBD says only 1.
        ibd = {"market_distribution_state": {"index_results": [
            {"symbol": "QQQ", "d25_count": 1}, {"symbol": "SPY", "d25_count": 0}]}}
        self._setup(monkeypatch, tmp_path, market_top=self._pressure_top(), ibd=ibd)
        active, reason = ts._short_conditions()
        assert active is False
        assert "давления нет" in reason and "[ibd]" in reason

    def test_old_confirmed_ftd_does_not_block(self, monkeypatch, tmp_path):
        ftd = self._ftd_report("FTD_CONFIRMED", _weekdays_ago(40))
        self._setup(monkeypatch, tmp_path, market_top=self._pressure_top(), ftd=ftd)
        active, _ = ts._short_conditions()
        assert active is True

    def test_invalidated_ftd_does_not_block(self, monkeypatch, tmp_path):
        ftd = self._ftd_report("FTD_CONFIRMED", _weekdays_ago(2), invalidated=True)
        self._setup(monkeypatch, tmp_path, market_top=self._pressure_top(), ftd=ftd)
        active, _ = ts._short_conditions()
        assert active is True

    def test_fresh_confirmed_ftd_blocks(self, monkeypatch, tmp_path):
        ftd = self._ftd_report("FTD_CONFIRMED", _weekdays_ago(2))
        self._setup(monkeypatch, tmp_path, market_top=self._pressure_top(), ftd=ftd)
        active, reason = ts._short_conditions()
        assert active is False
        assert "FTD" in reason

    def test_detector_overrides_market_top_false_negative(self, monkeypatch, tmp_path):
        # market_top hardwires ftd_detected=False below score 40; the detector
        # knows about a fresh FTD and must win regardless of file mtimes.
        ftd = self._ftd_report("FTD_CONFIRMED", _weekdays_ago(1))
        self._setup(
            monkeypatch, tmp_path, market_top=self._pressure_top(score=30.0, ftd=False), ftd=ftd
        )
        active, _ = ts._short_conditions()
        assert active is False

    def test_detector_no_ftd_overrides_market_top_stale_true(self, monkeypatch, tmp_path):
        # market_top still carries a stale break-on-detect True; the detector
        # state machine says the rally attempt has no confirmed FTD.
        ftd = self._ftd_report("RALLY_ATTEMPT", None)
        self._setup(monkeypatch, tmp_path, market_top=self._pressure_top(ftd=True), ftd=ftd)
        active, _ = ts._short_conditions()
        assert active is True


# --------------------------------------------------------------------------- #
# Auto-analyze reconcile (scheduler side; policy unified with ui reconcile.ts)
# --------------------------------------------------------------------------- #
_AOS_SIGNAL = {
    "ticker": "AOS",
    "date": "2026-06-12",
    "direction": "long",
    "trigger": 60.0,
    "stop": 56.0,
    "t1": 64.0,
    "t2": 70.0,
    "t3": 78.0,
    "entry_low": 58.0,
    "entry_high": 60.5,
}


class TestAutoAnalyzeReconcile:
    def _reconcile(self, monkeypatch, tmp_path, *, candidates, signal):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _write_json(
            tmp_path / "trading_profile.json",
            {"account_size": 150000, "risk_pct": 1, "max_position_pct": 25},
        )
        wl = {"date": "2026-06-11", "candidates": candidates}
        wl_path = _write_json(tmp_path / "schedule" / "watchlist_2026-06-11.json", wl)
        monkeypatch.setattr(ts, "_run_ticker_analysis", lambda ticker, args: True)
        monkeypatch.setattr(ts, "_parse_signals_md", lambda ticker: signal)
        args = types.SimpleNamespace(dry_run=False, timeout=60)
        return ts._auto_analyze_reconcile(wl, wl_path, "2026-06-11", args)

    def test_levels_update_resizes_from_profile_with_caps(self, monkeypatch, tmp_path):
        cand = {
            "ticker": "AOS", "side": "long", "pivot": 59.0, "stop": 56.5,
            "target": 63.0, "shares": 639, "risk_dollars": 517.59, "score": 80.3,
        }
        out = self._reconcile(monkeypatch, tmp_path, candidates=[cand], signal=_AOS_SIGNAL)
        c = out["candidates"][0]
        assert c["shares"] == 375  # 150000×1% / |60−56| — budget, not 517.59/4≈129
        assert c["risk_dollars"] == 1500.0
        assert c["worst_entry"] == 60.5  # Entry-range high, not == trigger
        assert c["pivot"] == 60.0 and c["stop"] == 56.0
        assert c["screener_origin"]["pivot"] == 59.0

    def test_worst_entry_falls_back_to_chase_band(self, monkeypatch, tmp_path):
        cand = {"ticker": "AOS", "side": "long", "pivot": 59.0, "stop": 56.5, "shares": 100}
        signal = {**_AOS_SIGNAL, "entry_low": None, "entry_high": None}
        out = self._reconcile(monkeypatch, tmp_path, candidates=[cand], signal=signal)
        assert out["candidates"][0]["worst_entry"] == 61.2  # 60 × 1.02

    def test_direction_flip_excludes_and_invalidates_thesis(self, monkeypatch, tmp_path):
        invalidated = []
        monkeypatch.setattr(
            ts, "_invalidate_thesis", lambda tid, *, reason: invalidated.append(tid)
        )
        cand = {
            "ticker": "AOS", "side": "short", "pivot": 58.66, "stop": 59.47,
            "shares": 639, "thesis_id": "th_aos_pvt_20260611_ab12",
        }
        out = self._reconcile(monkeypatch, tmp_path, candidates=[cand], signal=_AOS_SIGNAL)
        assert out["candidates"] == []
        rej = out["rejected_by_validation"][0]
        assert rej["source"] == "analysis-excluded"
        assert rej["side"] == "short"  # original side kept for the audit trail
        assert invalidated == ["th_aos_pvt_20260611_ab12"]

    def test_profile_sized_shares_caps_tight_stop(self):
        profile = {"account_size": 150000, "risk_pct": 1, "max_position_pct": 25}
        shares, risk = ts._profile_sized_shares(profile, 60.0, 59.9)
        assert shares == 625  # cap 150000×25%/60, not 1500/0.1=15000
        assert risk == 62.5


# --------------------------------------------------------------------------- #
# Thesis invalidation (direction-flip reconcile)
# --------------------------------------------------------------------------- #
def test_invalidate_thesis_uses_store_terminate(monkeypatch):
    """The state machine forbids `transition <id> INVALIDATED` (and the CLI
    launcher has no bare `transition` subcommand at all) — invalidation must go
    through `store terminate --terminal-status INVALIDATED`."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_run)
    ts._invalidate_thesis("th_x1", reason="analysis direction-flip: signal=short")
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd.index("store") + 1 == cmd.index("terminate")
    assert "th_x1" in cmd
    assert "--terminal-status" in cmd and "INVALIDATED" in cmd
    assert "--exit-reason" in cmd
    assert "transition" not in cmd


def test_open_long_signal_embeds_working_journal_commands(monkeypatch, tmp_path):
    """The Telegram OPEN-long message embeds copy-paste journal commands: the
    launcher needs the `store` prefix before `transition`, and BSD date has no
    `%:z` (it would emit a literal `:z` and fail RFC3339 validation)."""
    _patch_trading_dirs(monkeypatch, tmp_path)
    _gate(tmp_path, "2026-06-11", "allow")
    _watchlist_file(tmp_path, "2026-06-11", [{**_NVDA_CANDIDATE, "thesis_id": "th_abc"}])
    _heat_file(tmp_path)
    monkeypatch.setattr(ts.tsig, "fetch_quotes", lambda tickers, **k: {"NVDA": {"price": 156.0}})
    sent = []
    monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))

    rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--force", "--no-telegram"])
    assert rc == 0
    assert sent and "store transition th_abc ENTRY_READY" in sent[0]
    assert "%:z" not in sent[0]


def test_open_short_signal_embeds_journal_commands(monkeypatch, tmp_path):
    """Shorts get the same copy-paste journal commands as longs once the short
    branch registers theses."""
    _patch_trading_dirs(monkeypatch, tmp_path)
    _gate(tmp_path, "2026-06-11", "restrict")
    short_cand = {
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
        "thesis_id": "th_nflx_pvt_20260611_cd34",
    }
    _watchlist_file(tmp_path, "2026-06-11", [short_cand])
    _heat_file(tmp_path)
    monkeypatch.setattr(ts.tsig, "fetch_quotes", lambda tickers, **k: {"NFLX": {"price": 244.0}})
    sent = []
    monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))

    rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--force", "--no-telegram"])
    assert rc == 0
    assert sent and "ОТКРОЙ ШОРТ NFLX" in sent[0]
    assert "store transition th_nflx_pvt_20260611_cd34 ENTRY_READY" in sent[0]
    assert "store open-position th_nflx_pvt_20260611_cd34" in sent[0]


# --------------------------------------------------------------------------- #
# Weekly slot
# --------------------------------------------------------------------------- #
class TestWeeklySlot:
    def test_skips_on_non_saturday(self, monkeypatch, tmp_path, capsys):
        _patch_trading_dirs(monkeypatch, tmp_path)
        rc = ts.main(["--slot", "weekly", "--date", "2026-06-10", "--dry-run", "--no-telegram"])
        assert rc == 0
        assert "not a Saturday" in capsys.readouterr().out

    def test_runs_deterministic_scripts_and_claude(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        calls = []
        monkeypatch.setattr(
            ts,
            "run_skill_script",
            lambda cmd, *, label, dry_run, timeout, output_glob=None: calls.append(label),
        )

        def fake_run_claude(prompt, *, label, dry_run, timeout):
            _write_json(
                tmp_path / "schedule" / "weekly_review_2026-06-13.json",
                {"top_risk_score": 51.5, "macro_regime": "Contraction"},
            )
            return True

        monkeypatch.setattr(ts, "run_claude", fake_run_claude)
        sent = []
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
        rc = ts.main(["--slot", "weekly", "--date", "2026-06-13", "--no-telegram"])
        assert rc == 0
        assert any("ibd" in c for c in calls)
        assert any("macro" in c for c in calls)
        assert any("ftd" in c for c in calls)
        assert sent and "WEEKLY" in sent[0]


# --------------------------------------------------------------------------- #
# Auto-mode prompts and messages
# --------------------------------------------------------------------------- #
def test_validation_prompt_lists_candidates_and_path():
    cands = [{"ticker": "NVDA", "side": "long", "pivot": 155.2, "stop": 151.3}]
    prompt = ts.validation_prompt("2026-06-11", cands, Path("/tmp/v.json"))
    assert "NVDA" in prompt
    assert "/tmp/v.json" in prompt
    assert '"pass" | "reject"' in prompt


def test_weekly_prompt_contains_summary_path():
    prompt = ts.weekly_prompt("2026-06-13", Path("/tmp/weekly.json"))
    assert "/tmp/weekly.json" in prompt
    assert "market-top" in prompt.lower() or "market_top" in prompt.lower()


def test_intraday_msg_formats_signal_types():
    signals = [
        {
            "key": "NVDA:OPEN_LONG",
            "type": "OPEN_LONG",
            "ticker": "NVDA",
            "side": "long",
            "price": 156.0,
            "candidate": _NVDA_CANDIDATE,
        },
        {
            "key": "AAPL:STOP_HIT",
            "type": "STOP_HIT",
            "ticker": "AAPL",
            "side": "long",
            "price": 94.0,
            "position": {"ticker": "AAPL", "entry_price": 100.0, "stop_loss": 95.0, "shares": 100},
        },
        {
            "key": "MSFT:TWO_R",
            "type": "TWO_R",
            "ticker": "MSFT",
            "side": "long",
            "price": 110.0,
            "position": {"ticker": "MSFT", "entry_price": 100.0, "stop_loss": 95.0, "shares": 50},
        },
    ]
    msg = ts.build_intraday_msg("2026-06-11", signals)
    assert "ОТКРОЙ ЛОНГ NVDA" in msg
    assert "стоп" in msg
    assert "постмортем" in msg  # STOP_HIT follow-up
    assert "продай 50%" in msg  # TWO_R rule


# --------------------------------------------------------------------------- #
# TV alerts integration + no-cache + TV availability
# --------------------------------------------------------------------------- #
class TestTvIntegration:
    def test_evening_allow_syncs_alerts_and_reports(self, monkeypatch, tmp_path):
        helper = TestEveningHybrid()
        rc, _, sent = helper._run(monkeypatch, tmp_path, decision="allow")
        assert rc == 0
        assert helper.synced, "sync_watchlist_alerts must be called"
        assert any("Алерты TV" in m for m in sent)

    def test_evening_allow_tv_down_notifies_immediately_rc1(self, monkeypatch, tmp_path):
        helper = TestEveningHybrid()
        rc, calls, sent = helper._run(monkeypatch, tmp_path, decision="allow", tv_up=False)
        assert rc == 1
        assert sent and "TradingView" in sent[0]
        assert not any("vcp" in c for c in calls)  # screen skipped without TV

    def test_evening_short_branch_tv_down_notifies_rc1(self, monkeypatch, tmp_path):
        market_top = {
            "composite": {"composite_score": 51.5},
            "components": {"distribution_days": {"effective_count": 6.0}},
            "follow_through_day": {"ftd_detected": False},
        }
        helper = TestEveningHybrid()
        rc, calls, sent = helper._run(
            monkeypatch, tmp_path, decision="restrict", market_top=market_top, tv_up=False
        )
        assert rc == 1
        assert sent and "TradingView" in sent[0]
        assert not any("short" in c for c in calls)

    def test_intraday_missed_purges_alerts(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        monkeypatch.setattr(ts, "ALERTS_STATE_FILE", tmp_path / "logs" / "alerts_state.json")
        _gate(tmp_path, "2026-06-11", "allow")
        _watchlist_file(tmp_path, "2026-06-11", [_NVDA_CANDIDATE])
        _heat_file(tmp_path)
        monkeypatch.setattr(
            ts.tsig, "fetch_quotes", lambda tickers, **k: {"NVDA": {"price": 170.0}}
        )  # far above worst_entry -> MISSED
        monkeypatch.setattr(ts, "tv_available", lambda **k: True)
        purged = []
        monkeypatch.setattr(
            ts.talerts,
            "purge_watchlist_alerts",
            lambda tickers, state_path, **k: (
                purged.append(list(tickers))
                or {
                    "created": 0,
                    "deleted": 3,
                    "kept": 0,
                    "skipped": 0,
                    "errors": 0,
                    "error_details": [],
                }
            ),
        )
        sent = []
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
        rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--force", "--no-telegram"])
        assert rc == 0
        assert purged == [["NVDA"]]
        assert sent and "Сняты алерты" in sent[0]

    def test_intraday_missed_tv_down_warns_in_message(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _gate(tmp_path, "2026-06-11", "allow")
        _watchlist_file(tmp_path, "2026-06-11", [_NVDA_CANDIDATE])
        monkeypatch.setattr(
            ts.tsig, "fetch_quotes", lambda tickers, **k: {"NVDA": {"price": 170.0}}
        )
        monkeypatch.setattr(ts, "tv_available", lambda **k: False)
        sent = []
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
        rc = ts.main(["--slot", "intraday", "--date", "2026-06-11", "--force", "--no-telegram"])
        assert rc == 0
        assert sent and "вручную" in sent[0]

    def test_weekly_tv_down_notifies_and_skips_det_scripts(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        calls = []
        monkeypatch.setattr(
            ts,
            "run_skill_script",
            lambda cmd, *, label, dry_run, timeout, output_glob=None: calls.append(label),
        )
        monkeypatch.setattr(ts, "tv_available", lambda **k: False)
        monkeypatch.setattr(ts, "run_claude", lambda *a, **k: True)
        sent = []
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
        rc = ts.main(["--slot", "weekly", "--date", "2026-06-13", "--no-telegram"])
        assert rc == 0
        assert calls == []  # deterministic scripts skipped without TV
        assert any("TradingView" in m for m in sent)

    def test_premarket_tv_down_prepends_warning(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        monkeypatch.setattr(ts, "run_skill_script", lambda *a, **k: None)
        monkeypatch.setattr(ts, "run_claude", lambda *a, **k: True)
        monkeypatch.setattr(ts, "tv_available", lambda **k: False)
        sent = []
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
        rc = ts.main(["--slot", "premarket", "--date", "2026-06-11", "--no-telegram"])
        assert rc == 0
        assert sent and "TradingView" in sent[0]


def test_run_skill_script_disables_tv_cache(monkeypatch):
    captured = {}

    class _Res:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return _Res()

    monkeypatch.setattr(ts.subprocess, "run", fake_run)
    ts.run_skill_script(["echo"], label="x", dry_run=False, timeout=10)
    assert captured["env"]["TV_NO_CACHE"] == "1"
