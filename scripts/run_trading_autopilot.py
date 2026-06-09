#!/usr/bin/env python3
"""Trading autopilot — hourly self-dispatching wrapper over run_trading_schedule.py.

Designed to be fired every hour (or more often) by cron/launchd. Each run:

  1. Looks at the clock (CET wall time), the US trading calendar, and its own
     state file, and decides which scheduled step — if any — is due:
       * ``premarket``     window 15:00–21:00 CET on US trading days
       * ``evening-prep``  window 22:15–23:59 CET on US trading days
       * ``monthly``       first Sunday of the month, from 11:00 CET
  2. Executes the step by delegating to ``run_trading_schedule.py --slot ...``
     (the battle-tested orchestrator: claude -p workflows, gate files,
     Telegram digests).
  3. Writes a detailed per-run log to ``logs/autopilot/autopilot_<ts>.log``
     (decision, reason, full child output, state before/after) — including
     no-op runs.
  4. Sends Telegram messages for IMPORTANT events only (slot failure /
     retries exhausted / exposure-gate decision change). Successful slots
     already send their own rich digests from the schedule script — the
     autopilot does not duplicate them.

Idempotent per day: a slot that finished successfully is never re-run; a
failed slot is retried on subsequent runs up to MAX_ATTEMPTS while its window
is open. A PID lock file prevents overlapping runs. ``--dry-run`` decides and
logs but mutates no state and sends nothing.

Stdlib only. Manual testing:
    python3 scripts/run_trading_autopilot.py --dry-run
    python3 scripts/run_trading_autopilot.py --now 2026-06-10T15:05:00 --dry-run
    python3 scripts/run_trading_autopilot.py --force-slot premarket
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEDULE_SCRIPT = PROJECT_ROOT / "scripts" / "run_trading_schedule.py"
TELEGRAM_SCRIPT = PROJECT_ROOT / "skills" / "send-telegram" / "scripts" / "send_telegram.py"

STATE_FILE = PROJECT_ROOT / "logs" / "autopilot_state.json"
RUN_LOG_DIR = PROJECT_ROOT / "logs" / "autopilot"
LOCK_FILE = PROJECT_ROOT / "logs" / "autopilot.lock"

PREMARKET_START = dt.time(15, 0)
PREMARKET_END = dt.time(21, 0)
EVENING_START = dt.time(22, 15)
MONTHLY_START = dt.time(11, 0)

MAX_ATTEMPTS = 2
RUN_LOG_RETENTION_DAYS = 30

# Reuse the schedule orchestrator's calendar + gate-file helpers (single
# source of truth for the holiday list and the fail-safe gate parser).
_spec = importlib.util.spec_from_file_location("run_trading_schedule", SCHEDULE_SCRIPT)
schedule = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(schedule)


# --------------------------------------------------------------------------- #
# Per-run logging
# --------------------------------------------------------------------------- #
class RunLog:
    """Append-only per-run log file, mirrored to stdout."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write(f"===== autopilot run start pid={os.getpid()} =====")

    def write(self, msg: str) -> None:
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {msg}"
        print(line)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def prune_old_run_logs(directory: Path, days: int = RUN_LOG_RETENTION_DAYS) -> None:
    cutoff = time.time() - days * 86400
    try:
        for f in Path(directory).glob("autopilot_*.log"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except OSError:
        pass  # retention is best-effort; never fail the run over it


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def default_state(date_str: str = "") -> dict:
    return {"date": date_str, "slots": {}, "monthly": {}, "last_gate_decision": None}


def load_state(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_state()
        for key, value in default_state().items():
            data.setdefault(key, value)
        return data
    except (OSError, json.JSONDecodeError):
        return default_state()


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".autopilot_state.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def rollover_state(state: dict, today: dt.date) -> dict:
    """New day -> reset per-day slot records; keep monthly + last gate."""
    if state.get("date") == today.isoformat():
        return state
    fresh = default_state(today.isoformat())
    fresh["monthly"] = state.get("monthly", {})
    fresh["last_gate_decision"] = state.get("last_gate_decision")
    return fresh


def _slot_record(state: dict, slot: str) -> dict:
    return state.get("slots", {}).get(slot, {})


def _is_done(record: dict) -> bool:
    return record.get("status") == "done"


def _is_exhausted(record: dict) -> bool:
    return record.get("status") == "failed" and record.get("attempts", 0) >= MAX_ATTEMPTS


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def decide_action(now: dt.datetime, state: dict) -> tuple[str, str]:
    """Pick the due step for this wall-clock moment. Returns (action, reason)."""
    d = now.date()
    t = now.time()

    # Monthly review: first Sunday of the month from 11:00.
    if schedule.is_first_sunday(d):
        month_key = d.strftime("%Y-%m")
        record = state.get("monthly", {}).get(month_key, {})
        if t < MONTHLY_START:
            return "none", f"первое воскресенье: monthly запускается с {MONTHLY_START:%H:%M}"
        if _is_done(record):
            return "none", "monthly за этот месяц уже выполнен"
        if _is_exhausted(record):
            return "none", "monthly: попытки исчерпаны (см. лог последнего запуска)"
        return "monthly", "первое воскресенье месяца, время monthly-review"

    if not schedule.is_us_trading_day(d):
        return "none", "не торговый день в США (выходной/праздник) — шагов нет"

    # Evening prep: 22:15 — end of day.
    if t >= EVENING_START:
        record = _slot_record(state, "evening-prep")
        if _is_done(record):
            return "none", "вечерний прогон (evening-prep) уже выполнен сегодня"
        if _is_exhausted(record):
            return "none", "evening-prep: попытки исчерпаны — нужен ручной разбор"
        return "evening-prep", "после закрытия США: полный режим + скрин на завтра"

    # Premarket: 15:00 — 21:00.
    if PREMARKET_START <= t < PREMARKET_END:
        record = _slot_record(state, "premarket")
        if _is_done(record):
            return "none", "premarket уже выполнен — сессия: ведение позиций по алертам"
        if _is_exhausted(record):
            return (
                "none",
                "premarket: попытки исчерпаны — действуй по вчерашнему гейту (fail-safe restrict)",
            )
        return "premarket", "премаркет США: быстрая проверка режима + напоминание про ордера"

    if t < PREMARKET_START:
        return "none", f"до премаркета шагов нет (следующий шаг в {PREMARKET_START:%H:%M} CET)"
    return "none", f"между сессией и вечерним прогоном (следующий шаг в {EVENING_START:%H:%M} CET)"


def detect_gate_change(state: dict, decision: str) -> tuple[str, str] | None:
    """(old, new) when the exposure gate flipped; None on first observation/no change."""
    last = state.get("last_gate_decision")
    if last is None or last == decision:
        return None
    return (last, decision)


def read_gate_decision(date_str: str) -> dict:
    """Today's exposure gate via the schedule module (fail-safe restrict)."""
    return schedule.read_decision(schedule.decision_path(date_str))


# --------------------------------------------------------------------------- #
# Lock
# --------------------------------------------------------------------------- #
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, OSError):
        return True  # exists but not ours / unkillable -> treat as alive
    return True


def acquire_lock(path: Path) -> bool:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            pid = int(path.read_text().strip())
        except (ValueError, OSError):
            pid = -1
        if pid > 0 and _pid_alive(pid):
            return False
        try:
            path.unlink()  # stale lock
        except OSError:
            return False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_lock(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Execution + Telegram
# --------------------------------------------------------------------------- #
def run_slot(slot: str, *, dry_run: bool, no_telegram: bool, timeout: int, run_log: RunLog) -> int:
    """Delegate one slot to run_trading_schedule.py and capture its output."""
    cmd = [sys.executable, str(SCHEDULE_SCRIPT), "--slot", slot, "--timeout", str(timeout)]
    if dry_run:
        cmd.append("--dry-run")
    if no_telegram:
        cmd.append("--no-telegram")

    run_log.write(f"EXEC: {' '.join(cmd)}")
    started = time.monotonic()
    try:
        res = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout * 2 + 300,
        )
    except subprocess.TimeoutExpired:
        run_log.write(f"EXEC TIMEOUT after {time.monotonic() - started:.0f}s")
        return 1
    except OSError as exc:
        run_log.write(f"EXEC LAUNCH ERROR: {exc}")
        return 1

    elapsed = time.monotonic() - started
    if res.stdout:
        run_log.write(f"--- {slot} stdout ---\n{res.stdout}")
    if res.stderr:
        run_log.write(f"--- {slot} stderr ---\n{res.stderr}")
    run_log.write(f"EXEC DONE: rc={res.returncode} in {elapsed:.0f}s")
    return res.returncode


def send_telegram(text: str) -> None:
    """Best-effort Telegram push (autopilot's own important events only)."""
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        return
    cmd = [sys.executable, str(TELEGRAM_SCRIPT), "--message", text]
    try:
        subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120)
    except (subprocess.SubprocessError, OSError):
        pass


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--now", help="Override wall-clock (ISO, e.g. 2026-06-10T15:05:00) for testing.")
    p.add_argument(
        "--force-slot",
        choices=("premarket", "evening-prep", "monthly"),
        help="Bypass windows/dedupe and run this slot now.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Decide and log only: schedule runs in --dry-run, state is NOT mutated.",
    )
    p.add_argument("--no-telegram", action="store_true", help="Suppress all Telegram messages.")
    p.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("TRADING_SCHEDULE_TIMEOUT", "1800")),
        help="Per-workflow claude timeout passed to the schedule script (default 1800s).",
    )
    args = p.parse_args(argv)

    if args.now:
        try:
            now = dt.datetime.fromisoformat(args.now)
        except ValueError:
            print(f"bad --now {args.now!r}, expected ISO datetime", file=sys.stderr)
            return 2
    else:
        now = dt.datetime.now()
    date_str = now.date().isoformat()

    run_log = RunLog(RUN_LOG_DIR / f"autopilot_{now.strftime('%Y-%m-%d_%H%M%S')}.log")
    prune_old_run_logs(RUN_LOG_DIR)

    def tg(text: str) -> None:
        run_log.write(f"TELEGRAM: {text}")
        if not (args.no_telegram or args.dry_run):
            send_telegram(text)

    if not acquire_lock(LOCK_FILE):
        run_log.write("another autopilot run is in progress (lock busy) — exiting")
        return 0

    try:
        state = rollover_state(load_state(STATE_FILE), now.date())
        run_log.write(
            f"now={now.isoformat(timespec='seconds')} state={json.dumps(state, ensure_ascii=False)}"
        )

        if args.force_slot:
            action, reason = args.force_slot, "форсировано флагом --force-slot"
        else:
            action, reason = decide_action(now, state)
        run_log.write(f"DECISION: {action} — {reason}")

        if action == "none":
            if not args.dry_run:
                save_state(STATE_FILE, state)
            run_log.write("no-op run complete")
            return 0

        # Record the attempt up-front so a crash still counts toward retries.
        if not args.dry_run:
            if action == "monthly":
                month_key = now.strftime("%Y-%m")
                record = state["monthly"].get(month_key, {"attempts": 0})
                record["attempts"] = record.get("attempts", 0) + 1
                record["status"] = "running"
                record["at"] = now.isoformat(timespec="seconds")
                state["monthly"][month_key] = record
            else:
                record = state["slots"].get(action, {"attempts": 0})
                record["attempts"] = record.get("attempts", 0) + 1
                record["status"] = "running"
                record["at"] = now.isoformat(timespec="seconds")
                state["slots"][action] = record
            save_state(STATE_FILE, state)

        rc = run_slot(
            action,
            dry_run=args.dry_run,
            no_telegram=args.no_telegram,
            timeout=args.timeout,
            run_log=run_log,
        )

        if not args.dry_run:
            record["status"] = "done" if rc == 0 else "failed"
            record["rc"] = rc
            save_state(STATE_FILE, state)

            if action in ("premarket", "evening-prep"):
                decision = str(read_gate_decision(date_str).get("decision", "restrict"))
                change = detect_gate_change(state, decision)
                state["last_gate_decision"] = decision
                save_state(STATE_FILE, state)
                if change:
                    tg(
                        f"⚠️ Autopilot · {date_str}\n"
                        f"Гейт экспозиции изменился: {change[0]} → {change[1]}.\n"
                        f"Проверь открытые позиции и план на сессию."
                    )

            if rc != 0:
                exhausted = record.get("attempts", 0) >= MAX_ATTEMPTS
                tail = (
                    "Попытки исчерпаны — шаг сегодня больше не повторится, разбери вручную."
                    if exhausted
                    else "Автопилот повторит попытку при следующем запуске."
                )
                tg(
                    f"⛔️ Autopilot · {date_str}\n"
                    f"Слот {action} завершился с ошибкой (rc={rc}).\n"
                    f"Лог: {run_log.path}\n{tail}"
                )

        run_log.write(f"run complete rc={rc}")
        return rc
    finally:
        release_lock(LOCK_FILE)


if __name__ == "__main__":
    raise SystemExit(main())
