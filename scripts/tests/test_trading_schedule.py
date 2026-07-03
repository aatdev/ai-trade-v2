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

# Window fixtures are CET wall-clock; pin the conversion zone for determinism.
from zoneinfo import ZoneInfo  # noqa: E402

ts.LOCAL_TZ = ZoneInfo("Europe/Zurich")


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
# Headless claude command construction
# --------------------------------------------------------------------------- #
def test_run_claude_uses_print_flag(monkeypatch):
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", raising=False)
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_FLAGS", raising=False)
    captured = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # Non-empty stdout: rc=0 alone is no longer treated as success.
        return types.SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    assert ts.run_claude("do the thing", label="t", dry_run=False, timeout=10) is True
    cmd = captured["cmd"]
    assert "-p" in cmd
    # -p must come before the prompt so the prompt is its positional argument
    assert cmd.index("-p") < cmd.index("do the thing")


def test_run_claude_kill_ppid_mode_does_not_use_print_flag(monkeypatch):
    # kill-ppid is the fallback for environments where -p is unreliable.
    monkeypatch.setenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", "kill-ppid")
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_FLAGS", raising=False)
    captured = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    ts.run_claude("do the thing", label="t", dry_run=False, timeout=10)
    assert "-p" not in captured["cmd"]


# --------------------------------------------------------------------------- #
# Wrapper wall-time cap (claude-p --timeout defaults to 300s)
# --------------------------------------------------------------------------- #
class TestWrapperTimeoutFlags:
    """``claude-p``/``claude-pee`` cap their own wall-time at 300s by default;
    the scheduler must forward the per-step budget or long workflows die with
    ``StopTimeout`` (rc=2) -> fail-safe RESTRICT gate."""

    def test_forwards_for_claude_p(self):
        assert ts._wrapper_timeout_flags("claude-p", 1800, []) == [
            "--timeout",
            str(1800 - ts.WRAPPER_TIMEOUT_GRACE_S),
        ]

    def test_forwards_for_claude_pee_full_path(self):
        assert ts._wrapper_timeout_flags("/usr/local/bin/claude-pee", 900, []) == [
            "--timeout",
            str(900 - ts.WRAPPER_TIMEOUT_GRACE_S),
        ]

    def test_noop_for_plain_claude(self):
        # A plain claude (via $CLAUDE_BIN) has no --timeout flag.
        assert ts._wrapper_timeout_flags("/opt/custom/claude", 1800, []) == []
        assert ts._wrapper_timeout_flags("claude", 1800, []) == []

    def test_noop_when_operator_pinned(self):
        # An explicit --timeout via TRADING_SCHEDULE_CLAUDE_FLAGS wins.
        assert ts._wrapper_timeout_flags("claude-p", 1800, ["--timeout", "600"]) == []

    def test_cap_never_below_one(self):
        # A tiny budget must never yield a negative/zero cap.
        assert ts._wrapper_timeout_flags("claude-p", 5, []) == ["--timeout", "1"]


def test_run_claude_forwards_timeout_to_wrapper(monkeypatch):
    """End-to-end: the wrapper cap lands in the executed command, before the
    positional prompt."""
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", raising=False)
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_FLAGS", raising=False)
    monkeypatch.setattr(ts, "resolve_claude_bin", lambda: "claude-p")
    captured = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    assert ts.run_claude("do the thing", label="t", dry_run=False, timeout=1800) is True
    cmd = captured["cmd"]
    assert "--timeout" in cmd
    assert cmd[cmd.index("--timeout") + 1] == str(1800 - ts.WRAPPER_TIMEOUT_GRACE_S)
    assert cmd.index("--timeout") < cmd.index("do the thing")


def test_run_claude_kill_ppid_also_forwards_timeout(monkeypatch):
    """The kill-ppid path builds its own command; it must forward the cap too."""
    monkeypatch.setenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", "kill-ppid")
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_FLAGS", raising=False)
    monkeypatch.setattr(ts, "resolve_claude_bin", lambda: "claude-p")
    captured = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    ts.run_claude("do the thing", label="t", dry_run=False, timeout=1800)
    assert "--timeout" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--timeout") + 1] == str(
        1800 - ts.WRAPPER_TIMEOUT_GRACE_S
    )


def test_run_claude_does_not_forward_timeout_to_plain_claude(monkeypatch):
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", raising=False)
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_FLAGS", raising=False)
    monkeypatch.setattr(ts, "resolve_claude_bin", lambda: "/opt/custom/claude")
    captured = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    assert ts.run_claude("do the thing", label="t", dry_run=False, timeout=1800) is True
    assert "--timeout" not in captured["cmd"]


def test_run_claude_respects_operator_pinned_timeout(monkeypatch):
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", raising=False)
    monkeypatch.setenv("TRADING_SCHEDULE_CLAUDE_FLAGS", "--timeout 600")
    monkeypatch.setattr(ts, "resolve_claude_bin", lambda: "claude-p")
    captured = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    assert ts.run_claude("do the thing", label="t", dry_run=False, timeout=1800) is True
    cmd = captured["cmd"]
    assert cmd.count("--timeout") == 1
    assert cmd[cmd.index("--timeout") + 1] == "600"


# --------------------------------------------------------------------------- #
# Empty MCP allowlist (ambient .mcp.json / interactive-brokers cold-boot hang)
# --------------------------------------------------------------------------- #
class TestMcpDisableFlags:
    """Workflow claude steps call no IB tool, so they must run with an EMPTY MCP
    allowlist -- otherwise the ambient interactive-brokers server's IB Gateway
    cold-boot / 2FA handshake can hang the FIRST claude step of a slot for the
    whole budget -> ``StopTimeout`` (rc=2) -> fail-safe RESTRICT gate."""

    def test_disables_by_default(self):
        assert ts._mcp_disable_flags([]) == ["--strict-mcp-config"]
        # Composes with the wrapper-timeout flag (order-independent detection).
        assert ts._mcp_disable_flags(["--timeout", "1770"]) == ["--strict-mcp-config"]

    def test_noop_when_caller_supplied_mcp_config(self):
        # ticker-analysis opts into the TradingView MCP; do not force it off.
        assert (
            ts._mcp_disable_flags(["--mcp-config", "/tmp/tv.json", "--strict-mcp-config"]) == []
        )
        assert ts._mcp_disable_flags(["--mcp-config", "/tmp/tv.json"]) == []

    def test_noop_when_operator_already_strict(self):
        assert ts._mcp_disable_flags(["--strict-mcp-config"]) == []


def test_run_claude_disables_project_mcp_by_default(monkeypatch):
    """A plain workflow step (regime / chart-validation / weekly / monthly) runs
    with ``--strict-mcp-config`` and NO ``--mcp-config`` (=> zero MCP servers),
    before the positional prompt."""
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", raising=False)
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_FLAGS", raising=False)
    monkeypatch.setattr(ts, "resolve_claude_bin", lambda: "claude-p")
    captured = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    assert ts.run_claude("do the thing", label="t", dry_run=False, timeout=1800) is True
    cmd = captured["cmd"]
    assert "--strict-mcp-config" in cmd
    assert "--mcp-config" not in cmd
    assert cmd.index("--strict-mcp-config") < cmd.index("do the thing")


def test_run_claude_keeps_caller_mcp_config(monkeypatch):
    """When the caller declares a server set (ticker-analysis' TradingView opt-in
    via TRADING_SCHEDULE_CLAUDE_FLAGS), the default disable is suppressed and the
    explicit ``--mcp-config`` is preserved (exactly one ``--strict-mcp-config``)."""
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", raising=False)
    monkeypatch.setenv(
        "TRADING_SCHEDULE_CLAUDE_FLAGS", "--mcp-config /tmp/tv.json --strict-mcp-config"
    )
    monkeypatch.setattr(ts, "resolve_claude_bin", lambda: "claude-p")
    captured = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    assert ts.run_claude("do the thing", label="t", dry_run=False, timeout=1800) is True
    cmd = captured["cmd"]
    assert cmd.count("--strict-mcp-config") == 1
    assert "/tmp/tv.json" in cmd


def test_run_claude_kill_ppid_also_disables_project_mcp(monkeypatch):
    """The kill-ppid path builds its own command; it must disable ambient MCP too."""
    monkeypatch.setenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", "kill-ppid")
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_FLAGS", raising=False)
    monkeypatch.setattr(ts, "resolve_claude_bin", lambda: "claude-p")
    captured = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    ts.run_claude("do the thing", label="t", dry_run=False, timeout=1800)
    assert "--strict-mcp-config" in captured["cmd"]


def test_run_claude_strips_nested_session_env(monkeypatch):
    """The child claude must NOT inherit the parent Claude Code session markers
    (CLAUDECODE / CLAUDE_CODE_*), which make a nested claude-pee no-op; auth via
    CLAUDE_CONFIG_DIR must survive."""
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", raising=False)
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_FLAGS", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/cfg")
    captured = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return types.SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    assert ts.run_claude("do the thing", label="t", dry_run=False, timeout=10) is True
    env = captured["env"]
    assert env is not None, "child env must be passed explicitly"
    assert "CLAUDECODE" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert env.get("CLAUDE_CONFIG_DIR") == "/tmp/cfg"


def test_run_claude_empty_stdout_is_failure(monkeypatch):
    """rc=0 with empty stdout (a nested/failed claude-pee) is NOT success."""
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", raising=False)
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_FLAGS", raising=False)

    def fake_subprocess_run(cmd, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    assert ts.run_claude("do the thing", label="t", dry_run=False, timeout=10) is False


def test_run_claude_requires_expected_output(monkeypatch, tmp_path):
    """With expected_output set, rc=0 succeeds only once the file exists non-empty."""
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", raising=False)
    monkeypatch.delenv("TRADING_SCHEDULE_CLAUDE_FLAGS", raising=False)
    out = tmp_path / "weekly_review.json"

    def fake_subprocess_run(cmd, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(ts.subprocess, "run", fake_subprocess_run)
    # File not written -> failure even though rc=0 and stdout is non-empty.
    assert ts.run_claude("x", label="t", dry_run=False, timeout=10, expected_output=out) is False
    # Once the expected file exists with content -> success.
    out.write_text(json.dumps({"ok": True}))
    assert ts.run_claude("x", label="t", dry_run=False, timeout=10, expected_output=out) is True


# --------------------------------------------------------------------------- #
# Regime gate retry (headless claude can end before writing the gate)
# --------------------------------------------------------------------------- #
def test_run_regime_gate_retries_when_gate_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "SCHEDULE_DIR", tmp_path / "schedule")
    gate = ts.decision_path("2026-06-12")
    calls = []

    def fake_run_claude(prompt, *, label, dry_run, timeout, expected_output=None):
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

    def fake_run_claude(prompt, *, label, dry_run, timeout, expected_output=None):
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

    def fake_run_claude(prompt, *, label, dry_run, timeout, expected_output=None):
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
# Regime-flip thesis hygiene (_terminate_offside_theses)
# --------------------------------------------------------------------------- #
def _patch_theses(monkeypatch, theses):
    killed = []
    monkeypatch.setattr(ts, "_list_theses", lambda: theses)
    monkeypatch.setattr(ts, "_invalidate_thesis", lambda tid, *, reason: killed.append(tid))
    return killed


def test_terminate_offside_long_regime_kills_only_nonopen_shorts(monkeypatch):
    theses = [
        {"thesis_id": "s_idea", "side": "short", "status": "IDEA"},
        {"thesis_id": "s_ready", "side": "short", "status": "ENTRY_READY"},
        {"thesis_id": "s_active", "side": "short", "status": "ACTIVE"},  # open position
        {"thesis_id": "s_term", "side": "short", "status": "INVALIDATED"},  # terminal
        {"thesis_id": "l_idea", "side": "long", "status": "IDEA"},  # right side
    ]
    killed = _patch_theses(monkeypatch, theses)
    out = ts._terminate_offside_theses({"decision": "allow"}, types.SimpleNamespace(dry_run=False))
    assert sorted(out) == ["s_idea", "s_ready"]
    assert sorted(killed) == ["s_idea", "s_ready"]


def test_terminate_offside_short_regime_kills_only_nonopen_longs(monkeypatch):
    theses = [
        {"thesis_id": "l_idea", "side": "long", "status": "IDEA"},
        {"thesis_id": "l_ready", "side": "long", "status": "ENTRY_READY"},
        {"thesis_id": "l_active", "side": "long", "status": "PARTIALLY_CLOSED"},  # open position
        {"thesis_id": "nul_idea", "status": "IDEA"},  # null side -> long
        {"thesis_id": "s_idea", "side": "short", "status": "IDEA"},  # right side
    ]
    killed = _patch_theses(monkeypatch, theses)
    out = ts._terminate_offside_theses(
        {"decision": "restrict"}, types.SimpleNamespace(dry_run=False)
    )
    assert sorted(out) == ["l_idea", "l_ready", "nul_idea"]
    assert sorted(killed) == ["l_idea", "l_ready", "nul_idea"]


def test_terminate_offside_degraded_gate_is_noop(monkeypatch):
    killed = _patch_theses(monkeypatch, [{"thesis_id": "l_idea", "side": "long", "status": "IDEA"}])
    out = ts._terminate_offside_theses(
        {"decision": "restrict", "degraded": True}, types.SimpleNamespace(dry_run=False)
    )
    assert out == [] and killed == []


def test_terminate_offside_dry_run_lists_without_terminating(monkeypatch):
    killed = _patch_theses(
        monkeypatch, [{"thesis_id": "s_idea", "side": "short", "status": "IDEA"}]
    )
    out = ts._terminate_offside_theses({"decision": "allow"}, types.SimpleNamespace(dry_run=True))
    assert out == ["s_idea"]  # reported as "would invalidate"
    assert killed == []  # but not actually terminated


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

    def fake_run_claude(prompt, *, label, dry_run, timeout, expected_output=None):
        calls["claude"] += 1
        return True

    def fake_notify(text, *, dry_run, no_telegram, file=None):
        calls["notify"] += 1

    monkeypatch.setattr(ts, "run_claude", fake_run_claude)
    monkeypatch.setattr(ts, "notify", fake_notify)
    monkeypatch.setattr(ts, "_list_theses", lambda: [])  # no real trader-memory CLI
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

    def fake_run_claude(prompt, *, label, dry_run, timeout, expected_output=None):
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
    monkeypatch.setattr(ts, "_list_theses", lambda: [])  # no real trader-memory CLI
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
        universe_tickers=None,
    ):
        _patch_trading_dirs(monkeypatch, tmp_path)
        # Control the expanded liquid universe (scripts/lib/data/vcp_universe.txt)
        # hermetically — both the long VCP screen and the short screen read it.
        # Default [] keeps the bundled S&P 500 fallback path deterministic
        # regardless of whether the real data file is present in the checkout.
        monkeypatch.setattr(ts, "_read_vcp_universe", lambda: list(universe_tickers or []))
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

        def fake_run_claude(prompt, *, label, dry_run, timeout, expected_output=None):
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
            universe_tickers=["AAPL", "MSFT", "NVDA"],  # expanded liquid universe
        )
        assert rc == 0
        assert any("short" in c for c in calls)
        assert any("heat" in c for c in calls)  # fresh ledger for tomorrow's monitor
        assert any("ingest" in c for c in calls)  # shorts register theses too
        short_cmd = next(v for k, v in self.script_cmds.items() if "swing-short-screener" in k)
        # Expanded universe present → screen it (mirrors the long VCP branch),
        # NOT the S&P 500: --universe overrides and bypasses --max-candidates.
        assert "--universe" in short_cmd
        assert "AAPL" in short_cmd
        assert "--full-sp500" not in short_cmd
        ingest_cmd = next(v for k, v in self.script_cmds.items() if "ingest" in k)
        assert "swing-short-screener" in ingest_cmd
        wl = json.loads(
            (tmp_path / "schedule" / "watchlist_2026-06-11.json").read_text(encoding="utf-8")
        )
        assert wl["candidates"][0]["side"] == "short"
        assert wl["candidates"][0]["shares"] == 100  # 1% of 150k / 15
        assert sent and "ШОРТ" in sent[0]

    def test_short_branch_without_universe_file_falls_back_to_full_sp500(
        self, monkeypatch, tmp_path
    ):
        """No expanded universe file → screen the full S&P 500. Without
        --full-sp500 the screener caps at the first ~100 constituents."""
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
        rc, _, _ = self._run(
            monkeypatch,
            tmp_path,
            decision="restrict",
            market_top=market_top,
            short_candidates=shorts,
            universe_tickers=[],  # no expanded universe → fallback
        )
        assert rc == 0
        short_cmd = next(v for k, v in self.script_cmds.items() if "swing-short-screener" in k)
        assert "--full-sp500" in short_cmd
        assert "--universe" not in short_cmd

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
# Position care (time stops + EMA/SMA trail rules from the plan, step 3)
# --------------------------------------------------------------------------- #
class TestPositionCareWarnings:
    def _setup(self, monkeypatch, tmp_path, positions, indicators=None):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _write_json(
            tmp_path / "trading_profile.json",
            {"account_size": 150000, "risk_pct": 1, "time_stop_trading_days": 15},
        )
        _heat_file(tmp_path, positions=positions)
        monkeypatch.setattr(ts.tsig, "fetch_indicators", lambda tickers, **k: indicators or {})
        return types.SimpleNamespace(dry_run=False, timeout=60)

    def test_time_stop_reached_and_approaching(self, monkeypatch, tmp_path):
        positions = [
            {
                "ticker": "OLD",
                "side": "long",
                "entry_price": 100.0,
                "shares": 10,
                "stop_loss": 95.0,
                "entry_date": _weekdays_ago(16),
            },
            {
                "ticker": "MID",
                "side": "long",
                "entry_price": 100.0,
                "shares": 10,
                "stop_loss": 95.0,
                "entry_date": _weekdays_ago(13),
            },
            {
                "ticker": "NEW",
                "side": "long",
                "entry_price": 100.0,
                "shares": 10,
                "stop_loss": 95.0,
                "entry_date": _weekdays_ago(3),
            },
        ]
        args = self._setup(monkeypatch, tmp_path, positions)
        lines = "\n".join(ts._position_care_warnings(args))
        assert "OLD" in lines and "НАСТУПИЛ" in lines
        assert "MID" in lines and "через 2 т.д." in lines
        assert "NEW" not in lines

    def test_short_uses_10_day_time_stop(self, monkeypatch, tmp_path):
        positions = [
            {
                "ticker": "SHRT",
                "side": "short",
                "entry_price": 100.0,
                "shares": 10,
                "stop_loss": 105.0,
                "entry_date": _weekdays_ago(11),
            },
        ]
        args = self._setup(monkeypatch, tmp_path, positions)
        lines = "\n".join(ts._position_care_warnings(args))
        assert "SHRT" in lines and "тайм-стоп 10 т.д. НАСТУПИЛ" in lines

    def test_ema_break_and_sma50_trail(self, monkeypatch, tmp_path):
        positions = [
            {
                "ticker": "AAPL",
                "side": "long",
                "entry_price": 100.0,
                "shares": 10,
                "stop_loss": 95.0,
                "entry_date": _weekdays_ago(21),
            },
        ]
        indicators = {"AAPL": {"close": 96.0, "ema20": 98.0, "sma50": 97.0}}
        args = self._setup(monkeypatch, tmp_path, positions, indicators)
        lines = "\n".join(ts._position_care_warnings(args))
        assert "ниже EMA20" in lines
        assert "SMA50" in lines and "трейл-выход" in lines

    def test_short_ema_break_mirrored(self, monkeypatch, tmp_path):
        positions = [
            {
                "ticker": "NFLX",
                "side": "short",
                "entry_price": 100.0,
                "shares": 10,
                "stop_loss": 105.0,
                "entry_date": _weekdays_ago(2),
            },
        ]
        indicators = {"NFLX": {"close": 103.0, "ema20": 101.0, "sma50": 110.0}}
        args = self._setup(monkeypatch, tmp_path, positions, indicators)
        lines = "\n".join(ts._position_care_warnings(args))
        assert "выше EMA20" in lines and "слабость шорта" in lines

    def test_quiet_positions_produce_no_warnings(self, monkeypatch, tmp_path):
        positions = [
            {
                "ticker": "OK",
                "side": "long",
                "entry_price": 100.0,
                "shares": 10,
                "stop_loss": 95.0,
                "entry_date": _weekdays_ago(3),
            },
        ]
        indicators = {"OK": {"close": 105.0, "ema20": 102.0, "sma50": 100.0}}
        args = self._setup(monkeypatch, tmp_path, positions, indicators)
        assert ts._position_care_warnings(args) == []

    def test_care_signals_mark_exit_reason(self, monkeypatch, tmp_path):
        positions = [
            {
                "ticker": "OLD",
                "side": "long",
                "entry_price": 100.0,
                "shares": 10,
                "stop_loss": 95.0,
                "entry_date": _weekdays_ago(16),
                "thesis_id": "th_old_pvt_20260101_0001",
            },
            {
                "ticker": "MID",
                "side": "long",
                "entry_price": 100.0,
                "shares": 10,
                "stop_loss": 95.0,
                "entry_date": _weekdays_ago(13),
                "thesis_id": "th_mid_pvt_20260101_0002",
            },
        ]
        args = self._setup(monkeypatch, tmp_path, positions)
        events = ts._position_care_signals(args)
        by_ticker = {e["ticker"]: e for e in events}
        assert by_ticker["OLD"]["exit_reason"] == "time_stop"  # reached -> actionable
        assert by_ticker["MID"]["exit_reason"] is None  # approaching -> advisory only


class TestSendCloseCards:
    def test_send_close_cards_shells_out_for_exits(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _write_json(tmp_path / "trading_profile.json", {"time_stop_trading_days": 15})
        _heat_file(
            tmp_path,
            positions=[
                {
                    "ticker": "AAPL",
                    "side": "long",
                    "entry_price": 100.0,
                    "shares": 50,
                    "stop_loss": 95.0,
                    "entry_date": _weekdays_ago(21),
                    "thesis_id": "th_aapl_pvt_20260101_0003",
                },
            ],
        )
        # close below both EMA20 and SMA50 + >4 weeks -> actionable exits
        monkeypatch.setattr(
            ts.tsig,
            "fetch_indicators",
            lambda tickers, **k: {"AAPL": {"close": 96.0, "ema20": 98.0, "sma50": 97.0}},
        )
        calls = []
        monkeypatch.setattr(ts, "run_skill_script", lambda cmd, **k: calls.append(cmd))
        args = types.SimpleNamespace(dry_run=False, no_telegram=False, timeout=60)
        ts._send_close_cards("2026-06-15", args)
        assert len(calls) == 1  # one close card for the position
        cmd = [str(c) for c in calls[0]]
        assert "close-card" in cmd and "th_aapl_pvt_20260101_0003" in cmd
        assert "--exit-reason" in cmd and "time_stop" in cmd  # time_stop wins

    def test_send_close_cards_skips_when_no_telegram(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        called = []
        monkeypatch.setattr(ts, "run_skill_script", lambda cmd, **k: called.append(cmd))
        args = types.SimpleNamespace(dry_run=False, no_telegram=True, timeout=60)
        ts._send_close_cards("2026-06-15", args)
        assert called == []


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
            ts,
            "run_regime_gate",
            lambda *a, **k: (True, {"decision": "allow", "rationale": "x"}),
        )
        monkeypatch.setattr(ts, "tv_available", lambda **k: True)
        sent = []
        monkeypatch.setattr(ts, "notify", lambda text, **k: sent.append(text))
        rc = ts.main(["--slot", "premarket", "--date", "2026-06-11", "--no-telegram"])
        assert rc == 0
        assert sent and "Watchlist устарел" in sent[0]


# --------------------------------------------------------------------------- #
# latest_watchlist() file selection (must ignore validation siblings)
# --------------------------------------------------------------------------- #
class TestLatestWatchlist:
    def test_ignores_validation_sibling(self, monkeypatch, tmp_path):
        """watchlist_validation_<date>.json must never be returned: it sorts
        AFTER watchlist_<date>.json lexicographically ('v' > digit) and carries
        a different schema (verdicts, no candidates), so a naive glob would pick
        it and make a fresh watchlist look stale/missing."""
        _patch_trading_dirs(monkeypatch, tmp_path)
        sched = tmp_path / "schedule"
        _write_json(
            sched / "watchlist_2026-06-15.json",
            {"date": "2026-06-15", "candidates": [_NVDA_CANDIDATE]},
        )
        _write_json(
            sched / "watchlist_validation_2026-06-11.json",
            {"date": "2026-06-11", "verdicts": []},
        )
        latest = ts.latest_watchlist()
        assert latest is not None
        assert latest.name == "watchlist_2026-06-15.json"

    def test_none_when_only_validation_files(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _write_json(
            tmp_path / "schedule" / "watchlist_validation_2026-06-11.json",
            {"date": "2026-06-11", "verdicts": []},
        )
        assert ts.latest_watchlist() is None

    def test_picks_most_recent_real_watchlist(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        sched = tmp_path / "schedule"
        for d in ("2026-06-10", "2026-06-12", "2026-06-15"):
            _write_json(sched / f"watchlist_{d}.json", {"date": d, "candidates": []})
        assert ts.latest_watchlist().name == "watchlist_2026-06-15.json"


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
            "sp500": {
                "state": state,
                "ftd": {"ftd_detected": state == "FTD_CONFIRMED", "ftd_date": ftd_date},
            },
            "nasdaq": {"state": "RALLY_ATTEMPT", "ftd": {"ftd_detected": False}},
            "ftd_invalidation": {"invalidated": invalidated},
        }

    def test_market_top_dd_fallback_enables_shorts(self, monkeypatch, tmp_path):
        self._setup(monkeypatch, tmp_path, market_top=self._pressure_top())
        active, reason = ts._short_conditions()
        assert active is True
        assert "market_top" in reason

    def test_stale_generated_at_beats_fresh_mtime(self, monkeypatch, tmp_path):
        """An archive-restored report has a fresh mtime but an old
        metadata.generated_at — it must not arm the short branch."""
        top = self._pressure_top()
        old = (dt.datetime.now() - dt.timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        top["metadata"] = {"generated_at": old}
        self._setup(monkeypatch, tmp_path, market_top=top)
        active, reason = ts._short_conditions()
        assert active is False
        assert "нет свежего" in reason

    def test_low_data_quality_fails_safe(self, monkeypatch, tmp_path):
        top = self._pressure_top()
        top["composite"]["data_quality"] = {"available_count": 2, "total_components": 6}
        self._setup(monkeypatch, tmp_path, market_top=top)
        active, reason = ts._short_conditions()
        assert active is False
        assert "2/6" in reason

    def test_ibd_dd_count_preferred_over_market_top(self, monkeypatch, tmp_path):
        # market_top overstates DD (no rally invalidation); IBD says only 1.
        ibd = {
            "market_distribution_state": {
                "index_results": [
                    {"symbol": "QQQ", "d25_count": 1},
                    {"symbol": "SPY", "d25_count": 0},
                ]
            }
        }
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
            "ticker": "AOS",
            "side": "long",
            "pivot": 59.0,
            "stop": 56.5,
            "target": 63.0,
            "shares": 639,
            "risk_dollars": 517.59,
            "score": 80.3,
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
            "ticker": "AOS",
            "side": "short",
            "pivot": 58.66,
            "stop": 59.47,
            "shares": 639,
            "thesis_id": "th_aos_pvt_20260611_ab12",
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

    def test_top_n_is_one(self):
        # Option C: at most one full ticker-analysis per evening.
        assert ts.AUTO_ANALYZE_TOP_N == 1

    def test_caps_at_one_and_skips_fresh(self, monkeypatch, tmp_path):
        # AOS analyzed today (fresh) → skipped; BBB is the first not-fresh
        # candidate → gets the single deep dive; the cap of 1 leaves CCC alone.
        _patch_trading_dirs(monkeypatch, tmp_path)
        _write_json(
            tmp_path / "trading_profile.json",
            {"account_size": 150000, "risk_pct": 1, "max_position_pct": 25},
        )
        today = dt.date.today().isoformat()
        fresh = tmp_path / "analysis" / "AOS" / today
        fresh.mkdir(parents=True)
        (fresh / "report.md").write_text("x", encoding="utf-8")
        analyzed: list[str] = []
        monkeypatch.setattr(
            ts, "_run_ticker_analysis", lambda ticker, args: analyzed.append(ticker) or True
        )
        monkeypatch.setattr(
            ts, "_parse_signals_md", lambda ticker: {**_AOS_SIGNAL, "ticker": ticker}
        )
        args = types.SimpleNamespace(dry_run=False, timeout=60)
        wl = {
            "date": "2026-06-11",
            "candidates": [
                {"ticker": "AOS", "side": "long", "pivot": 59.0, "stop": 56.5, "shares": 100},
                {"ticker": "BBB", "side": "long", "pivot": 40.0, "stop": 38.0, "shares": 100},
                {"ticker": "CCC", "side": "long", "pivot": 30.0, "stop": 28.0, "shares": 100},
            ],
        }
        wl_path = _write_json(tmp_path / "schedule" / "watchlist_2026-06-11.json", wl)
        ts._auto_analyze_reconcile(wl, wl_path, "2026-06-11", args)
        assert analyzed == ["BBB"]


# --------------------------------------------------------------------------- #
# Chart-validation authoritative levels (Option C)
# --------------------------------------------------------------------------- #
class TestApplyValidationLevels:
    def _apply(self, monkeypatch, tmp_path, *, candidates, verdicts, profile=None):
        _patch_trading_dirs(monkeypatch, tmp_path)
        _write_json(
            tmp_path / "trading_profile.json",
            profile or {"account_size": 150000, "risk_pct": 1, "max_position_pct": 25},
        )
        wl = {"date": "2026-06-11", "candidates": candidates, "source_plan": "plans/p.json"}
        wl_path = _write_json(tmp_path / "schedule" / "watchlist_2026-06-11.json", wl)
        validation = {"date": "2026-06-11", "verdicts": verdicts}
        args = types.SimpleNamespace(dry_run=False, timeout=60)
        return ts._apply_validation_levels(wl, wl_path, validation, args)

    def test_long_pass_overrides_levels_and_resizes(self, monkeypatch, tmp_path):
        cand = {
            "ticker": "AOS",
            "side": "long",
            "pivot": 59.0,
            "stop": 56.5,
            "target": 63.0,
            "shares": 100,
            "score": 80.3,
        }
        v = [
            {
                "ticker": "AOS",
                "verdict": "pass",
                "note": "base ok",
                "entry": 60.0,
                "stop": 56.0,
                "target": 66.0,
            }
        ]
        out = self._apply(monkeypatch, tmp_path, candidates=[cand], verdicts=v)
        c = out["candidates"][0]
        assert c["pivot"] == 60.0 and c["stop"] == 56.0 and c["target"] == 66.0
        assert c["source"] == "chart-validation"
        assert c["shares"] == 375  # 150000×1% / |60−56| risk budget
        assert c["risk_dollars"] == 1500.0
        assert c["worst_entry"] == 61.2  # 60 × 1.02 chase band
        assert c["screener_origin"]["pivot"] == 59.0  # planner number preserved

    def test_pass_without_levels_keeps_planner(self, monkeypatch, tmp_path):
        cand = {"ticker": "AOS", "side": "long", "pivot": 59.0, "stop": 56.5, "shares": 100}
        v = [{"ticker": "AOS", "verdict": "pass", "note": "ok"}]
        out = self._apply(monkeypatch, tmp_path, candidates=[cand], verdicts=v)
        c = out["candidates"][0]
        assert c["pivot"] == 59.0 and c.get("source") != "chart-validation"

    def test_bad_long_geometry_keeps_planner(self, monkeypatch, tmp_path):
        cand = {"ticker": "AOS", "side": "long", "pivot": 59.0, "stop": 56.5, "shares": 100}
        v = [{"ticker": "AOS", "verdict": "pass", "entry": 56.0, "stop": 60.0}]  # stop>entry
        out = self._apply(monkeypatch, tmp_path, candidates=[cand], verdicts=v)
        assert out["candidates"][0]["pivot"] == 59.0

    def test_reject_verdict_with_levels_is_ignored(self, monkeypatch, tmp_path):
        cand = {"ticker": "AOS", "side": "long", "pivot": 59.0, "stop": 56.5, "shares": 100}
        v = [{"ticker": "AOS", "verdict": "reject", "entry": 60.0, "stop": 56.0}]
        out = self._apply(monkeypatch, tmp_path, candidates=[cand], verdicts=v)
        assert out["candidates"][0]["pivot"] == 59.0

    def test_short_pass_overrides_and_sizes_short(self, monkeypatch, tmp_path):
        cand = {"ticker": "WBA", "side": "short", "pivot": 20.0, "stop": 21.0, "shares": 100}
        v = [{"ticker": "WBA", "verdict": "pass", "entry": 19.5, "stop": 20.6, "target": 17.3}]
        out = self._apply(monkeypatch, tmp_path, candidates=[cand], verdicts=v)
        c = out["candidates"][0]
        assert c["pivot"] == 19.5 and c["stop"] == 20.6 and c["target"] == 17.3
        assert c["source"] == "chart-validation"
        assert c["shares"] and c["shares"] > 0
        assert c["worst_entry"] == 19.11  # 19.5 × 0.98 chase band

    def test_no_verdict_for_candidate_is_untouched(self, monkeypatch, tmp_path):
        cand = {"ticker": "AOS", "side": "long", "pivot": 59.0, "stop": 56.5, "shares": 100}
        v = [{"ticker": "OTHER", "verdict": "pass", "entry": 10.0, "stop": 9.0}]
        out = self._apply(monkeypatch, tmp_path, candidates=[cand], verdicts=v)
        assert out["candidates"][0]["pivot"] == 59.0


class TestRecentlyAnalyzed:
    def test_recent_report_is_fresh(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        d = tmp_path / "analysis" / "AOS" / dt.date.today().isoformat()
        d.mkdir(parents=True)
        (d / "report.md").write_text("x", encoding="utf-8")
        assert ts._recently_analyzed("AOS", 5) is True

    def test_old_report_not_fresh(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        d = tmp_path / "analysis" / "AOS" / old
        d.mkdir(parents=True)
        (d / "report.md").write_text("x", encoding="utf-8")
        assert ts._recently_analyzed("AOS", 5) is False

    def test_no_analysis_dir_not_fresh(self, monkeypatch, tmp_path):
        _patch_trading_dirs(monkeypatch, tmp_path)
        assert ts._recently_analyzed("ZZZ", 5) is False


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

        def fake_run_claude(prompt, *, label, dry_run, timeout, expected_output=None):
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
    # Option C: validation must also request authoritative structural levels.
    assert '"entry"' in prompt
    assert '"stop"' in prompt
    assert '"target"' in prompt


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


# --------------------------------------------------------------------------- #
# External-close reconcile (variant B safety-net)
# --------------------------------------------------------------------------- #
def _ok_snapshot(positions, trades=None):
    return {
        "ok": True,
        "account_ids": ["DU1"],
        "summary": {"net_liquidation": 1000.0},
        "positions": positions,
        "trades": trades or [],
        "error": None,
    }


def test_detect_external_closes_flags_missing_position():
    open_positions = [
        {"ticker": "AAPL", "side": "long", "shares": 100, "thesis_id": "th_a", "entry_price": 90.0},
        {"ticker": "MSFT", "side": "long", "shares": 50, "thesis_id": "th_m", "entry_price": 300.0},
    ]
    snap = _ok_snapshot(
        [{"symbol": "MSFT", "position": 50}],
        trades=[
            {"symbol": "AAPL", "side": "SELL", "price": 96.0, "trade_time": "2026-06-15T18:00:00"}
        ],
    )
    out = ts.detect_external_closes(open_positions, snap)
    assert len(out) == 1
    assert out[0]["thesis_id"] == "th_a" and out[0]["ticker"] == "AAPL"
    assert out[0]["price"] == 96.0  # latest closing-side fill, not the entry price


def test_detect_external_closes_zero_qty_is_flat():
    open_positions = [
        {"ticker": "AAPL", "side": "long", "shares": 100, "thesis_id": "th_a", "entry_price": 90.0}
    ]
    snap = _ok_snapshot([{"symbol": "AAPL", "position": 0}])  # flat row -> not held
    out = ts.detect_external_closes(open_positions, snap)
    assert [o["thesis_id"] for o in out] == ["th_a"]
    assert out[0]["price"] == 90.0  # no fill in trades -> entry-price fallback


def test_detect_external_closes_untrusted_snapshot_returns_empty():
    open_positions = [
        {"ticker": "AAPL", "side": "long", "shares": 100, "thesis_id": "th_a", "entry_price": 90.0}
    ]
    # ok=False (Gateway down), None, and ok-but-no-account-context must all be
    # treated as "unknown", never "everything closed".
    assert ts.detect_external_closes(open_positions, {"ok": False, "positions": []}) == []
    assert ts.detect_external_closes(open_positions, None) == []
    assert ts.detect_external_closes(open_positions, {"ok": True, "positions": []}) == []


def test_detect_external_closes_held_position_not_flagged():
    open_positions = [
        {"ticker": "AAPL", "side": "long", "shares": 100, "thesis_id": "th_a", "entry_price": 90.0}
    ]
    snap = _ok_snapshot([{"symbol": "AAPL", "position": 100}])
    assert ts.detect_external_closes(open_positions, snap) == []


def test_exit_price_from_trades_short_uses_buy_side():
    snap = _ok_snapshot(
        [],
        trades=[
            {"symbol": "TSLA", "side": "BUY", "price": 200.0, "trade_time": "2026-06-15T15:00:00"},
            {"symbol": "TSLA", "side": "BUY", "price": 190.0, "trade_time": "2026-06-15T18:00:00"},
            {"symbol": "TSLA", "side": "SELL", "price": 999.0, "trade_time": "2026-06-15T19:00:00"},
        ],
    )
    # Covering a short = BUY; latest BUY fill wins (190 over 200), SELL ignored.
    assert ts._exit_price_from_trades(snap, "TSLA", "short") == 190.0


def test_reconcile_ib_closes_sends_detected_card(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(ts, "_latest", lambda d, p: tmp_path / "portfolio_heat_x.json")
    monkeypatch.setattr(
        ts,
        "_read_json",
        lambda p: {
            "positions": [
                {
                    "ticker": "AAPL",
                    "side": "long",
                    "shares": 100,
                    "thesis_id": "th_a",
                    "entry_price": 90.0,
                },
            ]
        },
    )
    monkeypatch.setattr(
        ts, "_load_ib_snapshot", lambda args: _ok_snapshot([{"symbol": "MSFT", "position": 5}])
    )
    sent = []
    monkeypatch.setattr(ts, "run_skill_script", lambda cmd, **k: sent.append(cmd))
    args = types.SimpleNamespace(dry_run=False, no_telegram=False, timeout=60, ib_fixture=None)
    ts._reconcile_ib_closes("2026-06-15", args)
    assert len(sent) == 1
    cmd = [str(c) for c in sent[0]]
    assert "close-detected-card" in cmd
    assert "--thesis-id" in cmd and "th_a" in cmd
    assert "--ticker" in cmd and "AAPL" in cmd


def test_reconcile_ib_closes_dry_run_noop(monkeypatch):
    monkeypatch.setattr(
        ts, "_load_ib_snapshot", lambda args: (_ for _ in ()).throw(AssertionError("no snapshot"))
    )
    monkeypatch.setattr(
        ts, "run_skill_script", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no card"))
    )
    args = types.SimpleNamespace(dry_run=True, no_telegram=False, timeout=60, ib_fixture=None)
    ts._reconcile_ib_closes("2026-06-15", args)  # returns without touching IB


def test_reconcile_ib_closes_no_positions_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "_latest", lambda d, p: None)  # no heat file
    monkeypatch.setattr(
        ts, "_load_ib_snapshot", lambda args: (_ for _ in ()).throw(AssertionError("no snapshot"))
    )
    sent = []
    monkeypatch.setattr(ts, "run_skill_script", lambda cmd, **k: sent.append(cmd))
    args = types.SimpleNamespace(dry_run=False, no_telegram=False, timeout=60, ib_fixture=None)
    ts._reconcile_ib_closes("2026-06-15", args)
    assert sent == []
