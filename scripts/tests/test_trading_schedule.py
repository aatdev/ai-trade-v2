"""Tests for scripts/run_trading_schedule.py (stdlib-only orchestrator)."""

import datetime as dt
import importlib.util
import json
import os
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

        def fake_run_skill_script(cmd, *, label, dry_run, timeout, output_glob=None):
            script_calls.append(label)
            if "vcp" in label:
                return _write_json(
                    tmp_path / "screeners" / "vcp_screener_2026-06-11_221000.json", {"results": []}
                )
            if "heat" in label:
                return _heat_file(tmp_path)
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
        wl = json.loads(
            (tmp_path / "schedule" / "watchlist_2026-06-11.json").read_text(encoding="utf-8")
        )
        assert wl["candidates"][0]["side"] == "short"
        assert wl["candidates"][0]["shares"] == 100  # 1% of 150k / 15
        assert sent and "ШОРТ" in sent[0]

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
