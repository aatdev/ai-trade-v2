"""Tests for scripts/run_trading_autopilot.py (hourly self-dispatching runner)."""

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "run_trading_autopilot.py"
_spec = importlib.util.spec_from_file_location("run_trading_autopilot", _MODULE_PATH)
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)


def _state(**overrides):
    state = ap.default_state("2026-06-09")
    state.update(overrides)
    return state


def _slot_state(slot, status, attempts):
    s = _state()
    s["slots"][slot] = {"status": status, "attempts": attempts, "at": "x"}
    return s


TUE = "2026-06-09"  # Tuesday, trading day


def _at(hhmm: str, date_str: str = TUE) -> dt.datetime:
    return dt.datetime.fromisoformat(f"{date_str}T{hhmm}:00")


# --------------------------------------------------------------------------- #
# decide_action
# --------------------------------------------------------------------------- #
class TestDecideAction:
    def test_weekend_none(self):
        # 2nd Sunday: neither monthly nor a trading day
        action, reason = ap.decide_action(_at("16:00", "2026-06-14"), _state())
        assert action == "none"
        assert "торгов" in reason or "выходн" in reason.lower()

    def test_holiday_none(self):
        action, _ = ap.decide_action(_at("16:00", "2026-06-19"), _state())  # Juneteenth
        assert action == "none"

    def test_before_premarket_none(self):
        action, _ = ap.decide_action(_at("09:00"), _state())
        assert action == "none"

    def test_premarket_window_runs_premarket(self):
        action, _ = ap.decide_action(_at("15:05"), _state())
        assert action == "premarket"

    def test_premarket_done_hands_over_to_intraday(self):
        action, _ = ap.decide_action(_at("16:00"), _slot_state("premarket", "done", 1))
        assert action == "intraday"

    def test_premarket_done_before_intraday_window_is_noop(self):
        action, _ = ap.decide_action(_at("15:15"), _slot_state("premarket", "done", 1))
        assert action == "none"

    def test_premarket_failed_retries(self):
        action, _ = ap.decide_action(_at("16:00"), _slot_state("premarket", "failed", 1))
        assert action == "premarket"

    def test_premarket_exhausted_hands_over_to_intraday(self):
        state = _slot_state("premarket", "failed", ap.MAX_ATTEMPTS)
        action, _ = ap.decide_action(_at("16:00"), state)
        assert action == "intraday"

    def test_between_intraday_and_evening_none(self):
        action, _ = ap.decide_action(_at("22:05"), _state())
        assert action == "none"

    def test_intraday_after_premarket_window(self):
        # 21:00-22:00: premarket window over, session monitoring continues
        action, _ = ap.decide_action(_at("21:30"), _state())
        assert action == "intraday"

    def test_intraday_respects_interval(self):
        state = _slot_state("premarket", "done", 1)
        state["intraday"] = {"last_at": f"{TUE}T15:55:00", "runs": 1}
        action, reason = ap.decide_action(_at("16:00"), state)
        assert action == "none"
        assert "intraday" in reason

    def test_intraday_runs_again_after_interval(self):
        state = _slot_state("premarket", "done", 1)
        state["intraday"] = {"last_at": f"{TUE}T15:40:00", "runs": 1}
        action, _ = ap.decide_action(_at("16:00"), state)
        assert action == "intraday"

    def test_slot_timeout_budget_scales_for_evening_prep(self):
        flat = ap.slot_timeout_budget("premarket", 1800)
        evening = ap.slot_timeout_budget("evening-prep", 1800)
        weekly = ap.slot_timeout_budget("weekly", 1800)
        assert flat == 1800 * 2 + 300
        # evening must at least fit the regime retry + all auto ticker analyses
        assert evening >= 1800 * 4 + ap.schedule.AUTO_ANALYZE_TOP_N * ap.schedule.TICKER_ANALYSIS_TIMEOUT_S
        assert weekly > flat

    def test_intraday_tolerates_cron_jitter(self):
        # The run stamps last_at a second after the cron tick (15:45:01); the
        # next tick at 16:00:00 sees 14m59s elapsed. A strict `< 15` check
        # skipped every other tick (observed 30-min effective cadence) — the
        # tolerance must let this run.
        state = _slot_state("premarket", "done", 1)
        state["intraday"] = {"last_at": f"{TUE}T15:45:01", "runs": 1}
        action, _ = ap.decide_action(_at("16:00"), state)
        assert action == "intraday"

    def test_saturday_before_noon_is_noop(self):
        action, reason = ap.decide_action(_at("11:00", "2026-06-13"), _state())
        assert action == "none"
        assert "12:00" in reason

    def test_saturday_runs_weekly(self):
        action, _ = ap.decide_action(_at("12:05", "2026-06-13"), _state())
        assert action == "weekly"

    def test_saturday_weekly_done_is_noop(self):
        state = _state()
        state["weekly"]["2026-06-13"] = {"status": "done", "attempts": 1}
        action, _ = ap.decide_action(_at("13:00", "2026-06-13"), state)
        assert action == "none"

    def test_saturday_weekly_exhausted_is_noop(self):
        state = _state()
        state["weekly"]["2026-06-13"] = {"status": "failed", "attempts": ap.MAX_ATTEMPTS}
        action, reason = ap.decide_action(_at("13:00", "2026-06-13"), state)
        assert action == "none"
        assert "попыт" in reason

    def test_evening_window_runs_evening(self):
        action, _ = ap.decide_action(_at("22:20"), _state())
        assert action == "evening-prep"

    def test_evening_done_is_noop(self):
        action, _ = ap.decide_action(_at("23:00"), _slot_state("evening-prep", "done", 1))
        assert action == "none"

    def test_evening_failed_retries(self):
        action, _ = ap.decide_action(_at("23:00"), _slot_state("evening-prep", "failed", 1))
        assert action == "evening-prep"

    def test_first_sunday_runs_monthly(self):
        action, _ = ap.decide_action(_at("11:30", "2026-06-07"), _state())
        assert action == "monthly"

    def test_first_sunday_before_11_is_noop(self):
        action, _ = ap.decide_action(_at("10:00", "2026-06-07"), _state())
        assert action == "none"

    def test_second_sunday_is_noop(self):
        action, _ = ap.decide_action(_at("11:30", "2026-06-14"), _state())
        assert action == "none"

    def test_monthly_done_is_noop(self):
        state = _state()
        state["monthly"]["2026-06"] = {"status": "done", "at": "x"}
        action, _ = ap.decide_action(_at("11:30", "2026-06-07"), state)
        assert action == "none"


# --------------------------------------------------------------------------- #
# State persistence and rollover
# --------------------------------------------------------------------------- #
class TestState:
    def test_load_missing_returns_default(self, tmp_path):
        state = ap.load_state(tmp_path / "nope.json")
        assert state["slots"] == {}
        assert state["monthly"] == {}

    def test_save_load_roundtrip(self, tmp_path):
        path = tmp_path / "state.json"
        state = _slot_state("premarket", "done", 1)
        ap.save_state(path, state)
        assert ap.load_state(path)["slots"]["premarket"]["status"] == "done"

    def test_rollover_resets_slots_keeps_monthly_and_gate(self):
        state = _slot_state("premarket", "done", 1)
        state["monthly"]["2026-05"] = {"status": "done", "at": "x"}
        state["last_gate_decision"] = "allow"
        rolled = ap.rollover_state(state, dt.date(2026, 6, 10))
        assert rolled["date"] == "2026-06-10"
        assert rolled["slots"] == {}
        assert rolled["monthly"]["2026-05"]["status"] == "done"
        assert rolled["last_gate_decision"] == "allow"

    def test_rollover_keeps_weekly_resets_intraday(self):
        state = _state()
        state["weekly"]["2026-06-06"] = {"status": "done", "attempts": 1}
        state["intraday"] = {"last_at": "2026-06-09T16:00:00", "runs": 9}
        rolled = ap.rollover_state(state, dt.date(2026, 6, 10))
        assert rolled["weekly"]["2026-06-06"]["status"] == "done"
        assert rolled["intraday"] == {}

    def test_rollover_same_day_is_identity(self):
        state = _slot_state("premarket", "done", 1)
        rolled = ap.rollover_state(state, dt.date(2026, 6, 9))
        assert rolled["slots"]["premarket"]["status"] == "done"


# --------------------------------------------------------------------------- #
# Gate-change detection
# --------------------------------------------------------------------------- #
class TestGateChange:
    def test_first_observation_is_not_a_change(self):
        assert ap.detect_gate_change(_state(), "restrict") is None

    def test_same_decision_is_not_a_change(self):
        state = _state(last_gate_decision="restrict")
        assert ap.detect_gate_change(state, "restrict") is None

    def test_change_is_reported(self):
        state = _state(last_gate_decision="restrict")
        assert ap.detect_gate_change(state, "allow") == ("restrict", "allow")


# --------------------------------------------------------------------------- #
# Lock
# --------------------------------------------------------------------------- #
class TestLock:
    def test_acquire_fresh(self, tmp_path):
        lock = tmp_path / "ap.lock"
        assert ap.acquire_lock(lock) is True
        assert lock.read_text().strip() == str(os.getpid())
        ap.release_lock(lock)
        assert not lock.exists()

    def test_alive_pid_blocks(self, tmp_path):
        lock = tmp_path / "ap.lock"
        lock.write_text(str(os.getpid()))
        assert ap.acquire_lock(lock) is False

    def test_stale_pid_is_replaced(self, tmp_path):
        lock = tmp_path / "ap.lock"
        lock.write_text("999999999")
        assert ap.acquire_lock(lock) is True
        ap.release_lock(lock)


# --------------------------------------------------------------------------- #
# Slot execution (subprocess mocked)
# --------------------------------------------------------------------------- #
class TestRunSlot:
    def test_command_construction_and_logging(self, tmp_path, monkeypatch):
        captured = {}

        class _Res:
            returncode = 0
            stdout = "slot output here"
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Res()

        monkeypatch.setattr(ap.subprocess, "run", fake_run)
        run_log = ap.RunLog(tmp_path / "run.log")
        rc = ap.run_slot("premarket", dry_run=True, no_telegram=True, timeout=600, run_log=run_log)
        assert rc == 0
        cmd = captured["cmd"]
        assert str(ap.SCHEDULE_SCRIPT) in cmd
        assert "--slot" in cmd and "premarket" in cmd
        assert "--dry-run" in cmd and "--no-telegram" in cmd
        text = (tmp_path / "run.log").read_text()
        assert "slot output here" in text

    def test_nonzero_rc_propagates(self, tmp_path, monkeypatch):
        class _Res:
            returncode = 1
            stdout = ""
            stderr = "boom"

        monkeypatch.setattr(ap.subprocess, "run", lambda *a, **k: _Res())
        run_log = ap.RunLog(tmp_path / "run.log")
        rc = ap.run_slot(
            "evening-prep", dry_run=False, no_telegram=True, timeout=600, run_log=run_log
        )
        assert rc == 1
        assert "boom" in (tmp_path / "run.log").read_text()


# --------------------------------------------------------------------------- #
# main() integration (run_slot mocked)
# --------------------------------------------------------------------------- #
class TestMain:
    def _wire(self, tmp_path, monkeypatch, *, rc=0, gate_decision=None):
        calls = {"slots": [], "telegrams": []}
        monkeypatch.setattr(ap, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(ap, "RUN_LOG_DIR", tmp_path / "runs")
        monkeypatch.setattr(ap, "LOCK_FILE", tmp_path / "ap.lock")

        def fake_run_slot(slot, **kwargs):
            calls["slots"].append(slot)
            return rc

        monkeypatch.setattr(ap, "run_slot", fake_run_slot)
        monkeypatch.setattr(ap, "send_telegram", lambda text, **kw: calls["telegrams"].append(text))
        if gate_decision is not None:
            monkeypatch.setattr(
                ap, "read_gate_decision", lambda date_str: {"decision": gate_decision}
            )
        return calls

    def test_premarket_dispatch_and_dedupe(self, tmp_path, monkeypatch):
        calls = self._wire(tmp_path, monkeypatch, gate_decision="restrict")
        rc = ap.main(["--now", f"{TUE}T15:05:00", "--no-telegram"])
        assert rc == 0
        assert calls["slots"] == ["premarket"]
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["slots"]["premarket"]["status"] == "done"
        run_logs = list((tmp_path / "runs").glob("autopilot_*.log"))
        assert len(run_logs) == 1
        assert "premarket" in run_logs[0].read_text()

        # Premarket done -> the next run inside the session monitors intraday
        rc2 = ap.main(["--now", f"{TUE}T16:05:00", "--no-telegram"])
        assert rc2 == 0
        assert calls["slots"] == ["premarket", "intraday"]
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["intraday"]["last_at"] == f"{TUE}T16:05:00"

        # ...but not more often than the 15-minute interval
        rc3 = ap.main(["--now", f"{TUE}T16:10:00", "--no-telegram"])
        assert rc3 == 0
        assert calls["slots"] == ["premarket", "intraday"]  # unchanged

        rc4 = ap.main(["--now", f"{TUE}T16:25:00", "--no-telegram"])
        assert rc4 == 0
        assert calls["slots"] == ["premarket", "intraday", "intraday"]

    def test_weekly_dispatch_and_dedupe(self, tmp_path, monkeypatch):
        calls = self._wire(tmp_path, monkeypatch)
        rc = ap.main(["--now", "2026-06-13T12:05:00", "--no-telegram"])
        assert rc == 0
        assert calls["slots"] == ["weekly"]
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["weekly"]["2026-06-13"]["status"] == "done"

        rc2 = ap.main(["--now", "2026-06-13T13:05:00", "--no-telegram"])
        assert rc2 == 0
        assert calls["slots"] == ["weekly"]  # unchanged

    def test_intraday_failures_alert_only_after_streak(self, tmp_path, monkeypatch):
        calls = self._wire(tmp_path, monkeypatch, rc=1, gate_decision="restrict")
        base = ap.default_state(TUE)
        base["slots"]["premarket"] = {"status": "done", "attempts": 1}
        (tmp_path / "state.json").write_text(json.dumps(base))

        ap.main(["--now", f"{TUE}T16:00:00"])
        ap.main(["--now", f"{TUE}T16:20:00"])
        assert not any("Интрадей" in t for t in calls["telegrams"])
        ap.main(["--now", f"{TUE}T16:40:00"])
        assert any("Интрадей" in t for t in calls["telegrams"])
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["intraday"]["consecutive_failures"] == 3

    def test_intraday_success_resets_failure_streak(self, tmp_path, monkeypatch):
        calls = self._wire(tmp_path, monkeypatch, rc=0, gate_decision="restrict")
        base = ap.default_state(TUE)
        base["slots"]["premarket"] = {"status": "done", "attempts": 1}
        base["intraday"] = {"last_at": f"{TUE}T15:40:00", "runs": 2, "consecutive_failures": 2}
        (tmp_path / "state.json").write_text(json.dumps(base))

        ap.main(["--now", f"{TUE}T16:00:00", "--no-telegram"])
        assert calls["slots"] == ["intraday"]
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["intraday"]["consecutive_failures"] == 0

    def test_noop_run_still_writes_log(self, tmp_path, monkeypatch):
        calls = self._wire(tmp_path, monkeypatch)
        rc = ap.main(["--now", f"{TUE}T09:00:00", "--no-telegram"])
        assert rc == 0
        assert calls["slots"] == []
        run_logs = list((tmp_path / "runs").glob("autopilot_*.log"))
        assert len(run_logs) == 1

    def test_failure_marks_state_and_telegrams(self, tmp_path, monkeypatch):
        calls = self._wire(tmp_path, monkeypatch, rc=1, gate_decision="restrict")
        rc = ap.main(["--now", f"{TUE}T15:05:00"])
        assert rc == 1
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["slots"]["premarket"]["status"] == "failed"
        assert state["slots"]["premarket"]["attempts"] == 1
        assert any("premarket" in t for t in calls["telegrams"])

    def test_gate_change_sends_telegram(self, tmp_path, monkeypatch):
        calls = self._wire(tmp_path, monkeypatch, gate_decision="allow")
        (tmp_path / "state.json").write_text(
            json.dumps(ap.default_state(TUE) | {"last_gate_decision": "restrict"})
        )
        ap.main(["--now", f"{TUE}T15:05:00"])
        assert any("restrict" in t and "allow" in t for t in calls["telegrams"])
        state = json.loads((tmp_path / "state.json").read_text())
        assert state["last_gate_decision"] == "allow"

    def test_force_slot_bypasses_windows(self, tmp_path, monkeypatch):
        calls = self._wire(tmp_path, monkeypatch, gate_decision="restrict")
        rc = ap.main(["--now", f"{TUE}T09:00:00", "--force-slot", "premarket", "--no-telegram"])
        assert rc == 0
        assert calls["slots"] == ["premarket"]

    def test_lock_busy_exits_cleanly(self, tmp_path, monkeypatch):
        calls = self._wire(tmp_path, monkeypatch)
        (tmp_path / "ap.lock").write_text(str(os.getpid()))
        rc = ap.main(["--now", f"{TUE}T15:05:00", "--no-telegram"])
        assert rc == 0
        assert calls["slots"] == []


# --------------------------------------------------------------------------- #
# Telegram delivery under cron (bare environment + .env credentials)
# --------------------------------------------------------------------------- #
class TestSendTelegram:
    def test_invokes_script_when_creds_present(self, monkeypatch):
        captured = {}
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
        monkeypatch.setattr(ap.subprocess, "run", lambda cmd, **kw: captured.setdefault("cmd", cmd))
        ap.send_telegram("hello")
        assert str(ap.TELEGRAM_SCRIPT) in captured["cmd"]
        assert "hello" in captured["cmd"]

    def test_skips_without_creds(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        called = []
        monkeypatch.setattr(ap.subprocess, "run", lambda *a, **k: called.append(a))
        ap.send_telegram("hello")
        assert called == []


def test_main_loads_env_file(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(ap, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(ap, "RUN_LOG_DIR", tmp_path / "runs")
    monkeypatch.setattr(ap, "LOCK_FILE", tmp_path / "ap.lock")
    monkeypatch.setattr(ap.schedule, "load_env_file", lambda *a, **k: called.append(True))
    rc = ap.main(["--now", f"{TUE}T09:00:00", "--no-telegram"])
    assert rc == 0
    assert called == [True]


def test_schedule_busy_backs_off_quietly(tmp_path, monkeypatch):
    """When the schedule child reports EXIT_BUSY (a concurrent run holds the
    lock), the slot stays dispatchable, the attempt bump is rolled back, and no
    failure Telegram is sent."""
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(ap, "STATE_FILE", state_file)
    monkeypatch.setattr(ap, "RUN_LOG_DIR", tmp_path / "runs")
    monkeypatch.setattr(ap, "LOCK_FILE", tmp_path / "ap.lock")
    monkeypatch.setattr(ap.schedule, "load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(ap, "run_slot", lambda *a, **k: ap.schedule.EXIT_BUSY)
    sent = []
    monkeypatch.setattr(ap, "send_telegram", sent.append)

    rc = ap.main(["--force-slot", "evening-prep", "--now", f"{TUE}T22:30:00"])

    assert rc == 0
    record = ap.load_state(state_file)["slots"]["evening-prep"]
    assert record["status"] == "pending"  # left dispatchable for the next tick
    assert record["attempts"] == 0  # the up-front attempt bump was rolled back
    assert sent == []  # quiet backoff — no failure alarm
