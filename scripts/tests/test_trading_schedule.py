"""Tests for scripts/run_trading_schedule.py (stdlib-only orchestrator)."""

import datetime as dt
import importlib.util
import json
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


def test_evening_prep_runs_opportunity_when_gate_allows(monkeypatch):
    """When the regime step writes an `allow` gate, the opportunity screen runs."""

    def fake_run_claude(prompt, *, label, dry_run, timeout):
        if "market-regime-daily" in label:
            # emulate the regime workflow writing the gate file
            gate = ts.decision_path("2026-06-02")
            gate.write_text(json.dumps({"decision": "allow", "rationale": "ok"}))
        return True

    sent = []
    monkeypatch.setattr(ts, "run_claude", fake_run_claude)
    monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
    try:
        rc = ts.main(["--slot", "evening-prep", "--date", "2026-06-02", "--no-telegram"])
    finally:
        ts.decision_path("2026-06-02").unlink(missing_ok=True)
    assert rc == 0
    assert sent and "ALLOW" in sent[0]


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
