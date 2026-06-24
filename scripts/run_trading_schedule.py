#!/usr/bin/env python3
"""Scheduled orchestrator for the European swing-trading day plan (CET).

Hybrid execution model (chosen by the trader):
  * Deterministic workflow steps run unattended via headless ``claude -p``:
      - market-regime-daily       (exposure posture)
      - swing-opportunity-daily   (candidate screening / watchlist)
      - monthly-performance-review
  * Trigger-driven / broker-manual steps stay with the human in the loop and
    are surfaced as Telegram reminders, never auto-executed:
      - swing-execution-manage    (entry on breakout trigger, in-trade
                                    management, exit on stop/target/break)
      - trade-memory-loop         (postmortem after a position closes)

Key gate (from the plan): new swing risk -- swing-opportunity-daily and any new
entries -- runs ONLY when the latest ``exposure_decision`` is ``allow``. On
``restrict`` / ``cash-priority`` we only remind to manage / close open
positions. If the gate file cannot be read, we fail safe to ``restrict``.

Slots (wall-clock CET == machine local time for a CET-based trader; launchd
StartCalendarInterval follows local wall-clock and auto-adjusts for DST, which
keeps these anchored to the US open/close through the DST transition weeks):

  premarket     ~15:00      US pre-open. Quick regime re-check + reminder to
                            place bracket orders / arm breakout triggers for the
                            watchlist built the previous evening, and to manage
                            open positions through the session.
  intraday      15:30-22:00 AUTO MODE. Cheap headless quote check (public
                            TradingView scanner, no claude): fires concrete
                            OPEN signals when a watchlist trigger is crossed and
                            CLOSE-type signals (stop hit / near stop / +2R) for
                            open positions. Meant to run every ~15 min via the
                            autopilot; each signal is sent to Telegram once a day.
  evening-prep  ~22:15      AUTO MODE (hybrid). Full regime via claude, then the
                            deterministic pipeline: vcp-screener -> portfolio
                            heat -> breakout-trade-planner (+ fundamental floor) -> claude chart
                            validation of the top candidates -> watchlist with
                            exact entry/stop/target/shares + thesis ingest.
                            Under restrict/cash-priority the short branch runs
                            instead (swing-short-screener, plan step 6) when the
                            market-pressure conditions hold.
  weekly        Sat ~12:00  Weekly background block (plan step 8): IBD
                            distribution days, macro regime, FTD detector
                            (deterministic) + market-top via claude/WebSearch.
  monthly       Sun ~11:00  Monthly performance review. launchd fires every
                            Sunday; the script keeps only the FIRST Sunday.

Stdlib only -- no third-party imports -- so it runs under the system python3
without the project venv.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import logging.handlers
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"
TELEGRAM_SCRIPT = PROJECT_ROOT / "skills" / "send-telegram" / "scripts" / "send_telegram.py"

VALID_DECISIONS = ("allow", "restrict", "cash-priority")

# Early-close sessions (13:00 ET): day after Thanksgiving, Christmas Eve when
# it falls on a weekday, July 3 when July 4 is a weekday-observed holiday.
US_MARKET_HALF_DAYS = {
    "2025-07-03",
    "2025-11-28",
    "2025-12-24",
    "2026-11-27",
    "2026-12-24",
    "2027-11-26",
}

# NYSE/NASDAQ full-day closures. Extend yearly. Used only to skip non-trading
# days; an out-of-date list at worst runs a workflow on a holiday (harmless --
# the screeners simply see stale EOD data) or skips one (rare), never trades.
US_MARKET_HOLIDAYS = {
    # 2025
    "2025-01-01",
    "2025-01-20",
    "2025-02-17",
    "2025-04-18",
    "2025-05-26",
    "2025-06-19",
    "2025-07-04",
    "2025-09-01",
    "2025-11-27",
    "2025-12-25",
    # 2026
    "2026-01-01",
    "2026-01-19",
    "2026-02-16",
    "2026-04-03",
    "2026-05-25",
    "2026-06-19",
    "2026-07-03",
    "2026-09-07",
    "2026-11-26",
    "2026-12-25",
    # 2027
    "2027-01-01",
    "2027-01-18",
    "2027-02-15",
    "2027-03-26",
    "2027-05-31",
    "2027-06-18",
    "2027-07-05",
    "2027-09-06",
    "2027-11-25",
    "2027-12-24",
}


# --------------------------------------------------------------------------- #
# Environment (cron/launchd start with a bare environment)
# --------------------------------------------------------------------------- #
def load_env_file(path: Path = ENV_FILE) -> None:
    """Load ``KEY=VALUE`` / ``export KEY=VALUE`` lines into ``os.environ``.

    Telegram credentials and API keys live in the gitignored ``.env``;
    scheduled runs (cron/launchd) never source it, so they would otherwise
    silently skip Telegram. Variables already in the environment always win.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip("'\""))


# Homebrew / user-local bin dirs that cron drops (it starts with
# PATH=/usr/bin:/bin). The vendored `tv` CLI shells out to `node`, and the
# claude CLI lives under ~/.local/bin, so neither is reachable otherwise. A
# bare `PATH=...` line in .env cannot fix this because load_env_file() uses
# setdefault and cron already exports PATH — the prepend must happen in code.
_RUNTIME_BIN_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    str(Path.home() / ".local" / "bin"),
)


def ensure_runtime_path() -> None:
    """Prepend known Homebrew / user-local bin dirs to PATH (idempotent).

    Keeps subprocesses we spawn (the `tv`/`node` data layer, the headless
    `claude` workflows, skill scripts) able to find their executables when the
    scheduler runs under cron/launchd, which start with a minimal PATH.
    """
    parts = os.environ.get("PATH", "").split(os.pathsep)
    missing = [d for d in _RUNTIME_BIN_DIRS if d not in parts and os.path.isdir(d)]
    if missing:
        os.environ["PATH"] = os.pathsep.join([*missing, *parts])


def ensure_venv_interpreter() -> None:
    """Re-exec under the project virtualenv when started with a bare interpreter.

    The scheduler launches every skill script via ``sys.executable``. When it is
    invoked directly with a system/Homebrew python (cron, manual ``python3
    scripts/run_trading_schedule.py``) that lacks the project's declared deps --
    e.g. ``pyyaml`` for ibd-distribution-day-monitor -- those subprocesses die
    with ModuleNotFoundError. The ``.venv`` has the full dependency set, so
    re-exec once into ``.venv/bin/python`` to give the whole process tree the
    provisioned environment. No-op when already inside the venv or it is absent.
    The .sh launcher does the same selection up front; this self-heals the paths
    that bypass it.
    """
    if os.environ.get("_TRADING_SCHED_VENV_REEXEC") == "1":
        return  # guard against an exec loop
    venv_py = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_py.exists():
        return
    try:
        if venv_py.resolve() == Path(sys.executable).resolve():
            return  # already running the venv interpreter
    except OSError:
        return
    os.environ["_TRADING_SCHED_VENV_REEXEC"] = "1"
    os.execv(str(venv_py), [str(venv_py), *sys.argv])


# --------------------------------------------------------------------------- #
# Trading data layout ($TRADING_DATE_DIR)
#
# Every personal trading artifact lives under one root (default trading-data/,
# overridable via TRADING_DATE_DIR in the environment or .env):
#   schedule/   machine-readable gates: exposure_decision / watchlist / monthly
#   market/     regime reads: breadth, uptrend, exposure posture, top, macro...
#   screeners/  candidate screens: vcp / tradingview / swing-short / canslim
#   plans/      breakout trade plans, position sizing
#   journal/    thesis state (theses/), postmortems/, heat, monthly/ notes
#   analysis/   per-ticker deep dives + signals.md
#   logs/       schedule + autopilot logs and state
# --------------------------------------------------------------------------- #
load_env_file()  # the path constants below depend on TRADING_DATE_DIR
ensure_runtime_path()  # cron PATH=/usr/bin:/bin can't see node/tv/claude


def _resolve_trading_data_dir() -> Path:
    raw = os.environ.get("TRADING_DATE_DIR", "trading-data")
    p = Path(raw).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


TRADING_DATA_DIR = _resolve_trading_data_dir()
SCHEDULE_DIR = TRADING_DATA_DIR / "schedule"
MARKET_DIR = TRADING_DATA_DIR / "market"
SCREENERS_DIR = TRADING_DATA_DIR / "screeners"
PLANS_DIR = TRADING_DATA_DIR / "plans"
JOURNAL_DIR = TRADING_DATA_DIR / "journal"
LOG_FILE = TRADING_DATA_DIR / "logs" / "trading_schedule.log"
SIGNALS_STATE_FILE = TRADING_DATA_DIR / "logs" / "intraday_signals_state.json"
# Single-run lock: only one real (non-dry-run) schedule process may drive the
# (single) TradingView Desktop chart + shared trading-data state at a time.
LOCK_FILE = TRADING_DATA_DIR / "logs" / "trading_schedule.lock"
# Exit code for "another run holds the lock" — distinct from 0 (ok) / 1 (error)
# so the autopilot can back off and retry instead of flagging a hard failure.
EXIT_BUSY = 75  # EX_TEMPFAIL

# Auto-mode helper modules (stdlib-only, shared with tests).
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "lib"))
import trading_signals as tsig  # noqa: E402
import tv_alerts as talerts  # noqa: E402

# Bound at module level so tests can monkeypatch the probe in one place.
tv_available = talerts.tv_available

# Tickers we auto-created [WL] TradingView alerts for (cleanup on next sync).
ALERTS_STATE_FILE = TRADING_DATA_DIR / "logs" / "watchlist_alerts_state.json"

# Deterministic skill scripts orchestrated by the auto mode (hybrid pipeline).
SKILLS_DIR = PROJECT_ROOT / "skills"
VCP_SCREEN_SCRIPT = SKILLS_DIR / "vcp-screener" / "scripts" / "screen_vcp.py"
# Optional expanded universe (liquid NASDAQ+NYSE). Built by
# scripts/build_vcp_universe.py; when present BOTH evening-prep branches screen
# it instead of the bundled S&P 500 — the long VCP screen and the short
# swing-short screen. Delete the file to revert both to the S&P 500.
VCP_UNIVERSE_FILE = PROJECT_ROOT / "scripts" / "lib" / "data" / "vcp_universe.txt"
SHORT_SCREEN_SCRIPT = SKILLS_DIR / "swing-short-screener" / "scripts" / "screen_short.py"
PLANNER_SCRIPT = SKILLS_DIR / "breakout-trade-planner" / "scripts" / "plan_breakout_trades.py"
TRADER_MEMORY_CLI = SKILLS_DIR / "trader-memory-core" / "scripts" / "trader_memory_cli.py"
WATCHLIST_ORDERS_SCRIPT = PROJECT_ROOT / "scripts" / "watchlist_orders.py"
IBD_SCRIPT = SKILLS_DIR / "ibd-distribution-day-monitor" / "scripts" / "ibd_monitor.py"
MACRO_SCRIPT = SKILLS_DIR / "macro-regime-detector" / "scripts" / "macro_regime_detector.py"
FTD_SCRIPT = SKILLS_DIR / "ftd-detector" / "scripts" / "ftd_detector.py"
IB_SNAPSHOT_SCRIPT = SKILLS_DIR / "ib-portfolio-manager" / "scripts" / "fetch_ib_snapshot.py"

# Intraday monitoring window: derived per date from US Eastern (see
# intraday_window_local below) — fixed CET constants drift during the
# US/EU DST-mismatch weeks and ignore half days.
# How many top candidates get the claude chart-validation pass (hybrid mode).
VALIDATION_TOP_N = 3
# Short branch market-pressure gates (trading plan step 6): top-risk score OR
# distribution-day count, and no fresh confirmed FTD.
SHORT_TOP_RISK_MIN = 41.0
SHORT_DD_MIN = 3
# A confirmed FTD older than this many weekdays no longer forbids shorting.
FTD_FRESH_WEEKDAYS = 10
# market_top / ftd reports older than this are treated as absent (fail-safe).
MARKET_REPORT_MAX_AGE_DAYS = 7
# How many watchlist candidates get the full auto ticker-analysis + reconcile
# pass each evening. Option C: only the single best *fresh* candidate gets the
# deep news+fundamental+technical dive; every other candidate's levels come
# from the chart-validation step (technical-analyst), which is authoritative.
AUTO_ANALYZE_TOP_N = 1
# A candidate with a report.md under analysis/<TICKER>/<date>/ dated within this
# many weekdays is "fresh": skip its deep dive and spend the single-analysis
# budget on the next, not-recently-analyzed candidate instead.
FRESH_ANALYSIS_WEEKDAYS = 5
# Model for ticker-analysis (mirrors ui/server/src/config.ts ANALYZE_MODEL).
TICKER_ANALYSIS_MODEL = "claude-opus-4-8"
# Per-ticker timeout cap so one slow analysis doesn't block the whole slot.
TICKER_ANALYSIS_TIMEOUT_S = 1100


def _rel(p: Path) -> str:
    """Repo-relative path string for prompts/messages (absolute if outside)."""
    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


CBIN = "claude-p"

# Probed in order when ``claude`` is not on PATH (cron PATH is /usr/bin:/bin).
CLAUDE_FALLBACK_PATHS = [
    Path.home() / ".local" / "bin" / CBIN,
    Path(f"/opt/homebrew/bin/{CBIN}"),
    Path(f"/usr/local/bin/{CBIN}"),
]


def resolve_claude_bin() -> str:
    """Locate the claude CLI: $CLAUDE_BIN > PATH > known install dirs."""
    override = os.environ.get("CLAUDE_BIN")
    if override:
        return override
    if shutil.which(CBIN):
        return CBIN
    for candidate in CLAUDE_FALLBACK_PATHS:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return CBIN  # let run_claude surface the launch error


# claude-p / claude-pee wrap the inner ``claude`` with their OWN wall-time cap
# (the wrapper's ``--timeout`` flag), which DEFAULTS TO 300s. The scheduler's
# per-step ``timeout`` only bounds the *outer* subprocess; unless we forward it,
# the wrapper kills any workflow running past ~5 min with ``StopTimeout`` (rc=2),
# which then silently fail-safes the exposure gate to RESTRICT. Forward the
# budget (minus a small grace, so the wrapper trips just before the outer
# subprocess would hard-kill) so a slot's real timeout is honored.
WRAPPER_TIMEOUT_GRACE_S = 30


def _is_claude_p_wrapper(claude_bin: str) -> bool:
    """True for the claude-p / claude-pee wrappers (which accept ``--timeout``),
    false for a plain ``claude`` pointed at via $CLAUDE_BIN (which does not)."""
    return Path(claude_bin).name.startswith("claude-p")


def _wrapper_timeout_flags(claude_bin: str, timeout: int, extra: list[str]) -> list[str]:
    """``["--timeout", "<seconds>"]`` forwarding the per-step budget to a
    claude-p/claude-pee wrapper, or ``[]`` when it does not apply.

    No-op when the binary is not a wrapper or the operator already pinned
    ``--timeout`` via TRADING_SCHEDULE_CLAUDE_FLAGS.
    """
    if "--timeout" in extra or not _is_claude_p_wrapper(claude_bin):
        return []
    cap = max(timeout - WRAPPER_TIMEOUT_GRACE_S, 1)
    return ["--timeout", str(cap)]


def _child_claude_env() -> dict:
    """Environment for the child ``claude``/``claude-pee`` process.

    When the scheduler runs *inside* an active Claude Code session (e.g. a
    ``/weekly`` slash command spawns ``run_trading_schedule.sh`` as a child),
    the inherited ``CLAUDECODE`` / ``CLAUDE_CODE_*`` markers make the nested
    ``claude`` skip producing the transcript that ``claude-pee`` polls for --
    it then exits rc=0 with empty output and the slot silently no-ops. Strip
    those markers so the child starts a clean session; keep ``CLAUDE_CONFIG_DIR``
    and ``CLAUDE_BIN`` (auth / binary location) which do not match the prefix.
    """
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    # for key in [k for k in env if k.startswith("CLAUDE_CODE_")]:
    #     del env[key]
    env["RUST_LOG"] = "debug"
    log(f"ENVIRONMENT: {' '.join(f'{k}={v!r}' for k, v in env.items())}", logging.INFO)
    return env


# --------------------------------------------------------------------------- #
# Calendar helpers
# --------------------------------------------------------------------------- #
def is_us_trading_day(d: dt.date) -> bool:
    """True on weekdays that are not full-day US market holidays."""
    if d.weekday() >= 5:  # Sat/Sun
        return False
    return d.isoformat() not in US_MARKET_HOLIDAYS


def is_first_sunday(d: dt.date) -> bool:
    """True only on the first Sunday of the month (day 1-7, weekday Sunday)."""
    return d.weekday() == 6 and d.day <= 7


# --------------------------------------------------------------------------- #
# US-session windows (anchored to US Eastern, converted to local wall time)
#
# The trader's machine runs CET, but fixed CET constants drift an hour during
# the 2-3 DST-mismatch weeks each year (the US switches before the EU): the
# session actually starts at 14:30 CET, the old 15:30 window missed the first
# hour. Windows are therefore defined in ET and converted per date.
# --------------------------------------------------------------------------- #
US_EASTERN = ZoneInfo("America/New_York")
# Test override for deterministic conversions; None -> system local zone.
LOCAL_TZ: ZoneInfo | None = None

INTRADAY_START_ET = dt.time(9, 30)
SESSION_CLOSE_ET = dt.time(16, 0)
HALF_DAY_CLOSE_ET = dt.time(13, 0)


def us_session_close_et(d: dt.date) -> dt.time:
    """Session close in ET for the date (13:00 on early-close half days)."""
    return HALF_DAY_CLOSE_ET if d.isoformat() in US_MARKET_HALF_DAYS else SESSION_CLOSE_ET


def et_to_local(d: dt.date, t: dt.time) -> dt.time:
    """Local wall-clock time of an ET hh:mm on date ``d``."""
    return dt.datetime.combine(d, t, tzinfo=US_EASTERN).astimezone(LOCAL_TZ).time()


def intraday_window_local(d: dt.date) -> tuple[dt.time, dt.time]:
    """(start, end) of the US session in local wall time for the date."""
    return et_to_local(d, INTRADAY_START_ET), et_to_local(d, us_session_close_et(d))


# --------------------------------------------------------------------------- #
# Logging / notification
# --------------------------------------------------------------------------- #
logger = logging.getLogger("trading_schedule")


def setup_logging(*, verbose: bool = False) -> None:
    """Configure the module logger: stdout + daily-rotating file (30-day keep).

    Idempotent -- safe to call repeatedly (rebuilds handlers each time, so a
    fresh ``sys.stdout`` is picked up, which keeps pytest's capsys working).
    """
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            LOG_FILE, when="midnight", backupCount=30, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError as exc:  # logging must never crash the run
        logger.warning("could not open log file %s: %s", LOG_FILE, exc)


def log(msg: str, level: int = logging.INFO) -> None:
    """Emit one log record. Lazily configures handlers for direct-call contexts."""
    if not logger.handlers:
        setup_logging()
    logger.log(level, msg)


def notify(text: str, *, dry_run: bool, no_telegram: bool, file: str | None = None) -> None:
    """Push a Telegram message (and optional file). Degrades to a log line."""
    log("NOTIFY:\n" + text)
    if dry_run or no_telegram:
        log(f"(telegram suppressed: dry_run={dry_run} no_telegram={no_telegram})")
        return
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        log("(telegram skipped: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)", logging.WARNING)
        return
    cmd = [sys.executable, str(TELEGRAM_SCRIPT), "--message", text]
    if file:
        cmd += ["--file", file]
    try:
        res = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120)
        if res.returncode != 0:
            log(
                f"(telegram send failed rc={res.returncode}): {res.stderr.strip()}", logging.WARNING
            )
        else:
            log(f"(telegram sent{' + file' if file else ''})")
    except (subprocess.SubprocessError, OSError) as exc:
        log(f"(telegram send error): {exc}", logging.ERROR)


# --------------------------------------------------------------------------- #
# Headless Claude execution
# --------------------------------------------------------------------------- #
import tempfile as _tempfile


def _stream_enabled() -> bool:
    """Whether to echo the claude subprocess output to the console live.

    Off by default (output is captured and only logged as a tail after the run,
    which keeps cron/launchd logs compact). Set ``TRADING_SCHEDULE_STREAM=1``
    when debugging to see claude-pee's progress + RUST_LOG diagnostics in real
    time — e.g. ``TRADING_SCHEDULE_STREAM=1 RUST_LOG=debug bash
    scripts/run_trading_schedule.sh --slot weekly``.
    """
    return os.environ.get("TRADING_SCHEDULE_STREAM", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _popen_tee(cmd, *, env, timeout, stream):
    """Run ``cmd`` and return a ``subprocess.run``-shaped result.

    Default (``stream=False``): identical to ``subprocess.run(capture_output=
    True)``. When ``stream=True``, the child's combined output (stderr folded
    into stdout so RUST_LOG/diagnostics interleave in order) is echoed to the
    console line by line as it arrives AND accumulated, so downstream
    rc/expected_output/empty-output checks keep working. ``res.stderr`` is ""
    in stream mode (it went to stdout). ``timeout`` is enforced via a watchdog
    that kills the process and raises ``TimeoutExpired`` like ``run`` does.
    """
    if not stream:
        return subprocess.run(
            cmd, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=timeout
        )

    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        bufsize=1,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    timed_out = {"v": False}

    def _kill() -> None:
        timed_out["v"] = True
        proc.kill()

    timer = threading.Timer(timeout, _kill)
    timer.start()
    chunks: list[str] = []
    try:
        for line in proc.stdout:  # ends when the child closes stdout (exit/kill)
            sys.stdout.write(line)
            sys.stdout.flush()
            chunks.append(line)
        proc.wait()
    finally:
        timer.cancel()

    if timed_out["v"]:
        raise subprocess.TimeoutExpired(cmd, timeout, output="".join(chunks))
    return types.SimpleNamespace(returncode=proc.returncode, stdout="".join(chunks), stderr="")


def _run_claude_kill_ppid(
    prompt: str,
    *,
    label: str,
    dry_run: bool,
    timeout: int,
    claude_bin: str,
    perm_mode: str,
    extra: list[str],
) -> bool:
    """Run Claude with file-output + ``kill $PPID`` termination pattern.

    Wraps the prompt so Claude writes its final answer to a temp file and then
    kills its parent process (the ``subprocess.run`` call), which releases the
    blocking wait.  Used when the interactive ``-p/--print`` flag is unavailable
    or unreliable in the current environment.
    """
    fd, raw_path = _tempfile.mkstemp(prefix="claude_sched_", suffix=".txt")
    os.close(fd)
    output_path = Path(raw_path)
    wrapped = (
        f"{prompt}\n\n"
        "После завершения задачи выполни строго эти шаги:\n"
        f"1) Запиши финальный ответ в UTF-8 файл: {output_path}\n"
        "2) Выполни в bash команду: kill $PPID\n"
        "3) После этого ничего не пиши и не делай."
    )
    cmd = [claude_bin, "--permission-mode", perm_mode, *extra, wrapped]

    log(
        f"--- claude workflow START: {label} (timeout={timeout}s, perm={perm_mode}, mode=kill-ppid) ---"
    )
    log(
        f"command: {claude_bin} --permission-mode {perm_mode} <wrapped prompt {len(wrapped)} chars>",
        logging.INFO,
    )
    if dry_run:
        log(f"(dry-run) kill-ppid prompt:\n{wrapped}")
        output_path.unlink(missing_ok=True)
        return True

    started = time.monotonic()
    try:
        res = _popen_tee(cmd, env=_child_claude_env(), timeout=timeout, stream=_stream_enabled())
    except subprocess.TimeoutExpired:
        log(f"{label}: claude timed out after {timeout}s", logging.ERROR)
        return False
    except OSError as exc:
        log(f"{label}: could not launch claude ({exc})", logging.ERROR)
        return False

    elapsed = time.monotonic() - started
    # Log stderr on every path (not only on failure): claude-pee writes its
    # RUST_LOG diagnostics + the inner claude's warnings here, which are exactly
    # what you need when debugging a nested no-op / session-greeting capture.
    if res.stderr and res.stderr.strip():
        log(f"{label} stderr (tail):\n" + res.stderr[-2000:])
    try:
        file_text = output_path.read_text(encoding="utf-8").strip()
    except OSError:
        file_text = ""

    if file_text:
        log(f"{label} output-file (tail):\n{file_text[-2000:]}")
        log(f"--- claude workflow DONE: {label} in {elapsed:.0f}s ---")
        output_path.unlink(missing_ok=True)
        return True

    if res.stdout:
        log(f"{label} stdout (tail):\n" + res.stdout[-2000:])
    log(
        f"{label}: no output in {output_path} (rc={res.returncode}, {elapsed:.0f}s)",
        logging.ERROR,
    )
    return False


def run_claude(
    prompt: str,
    *,
    label: str,
    dry_run: bool,
    timeout: int,
    expected_output: Path | str | None = None,
) -> bool:
    """Run one workflow headlessly via ``claude``. Returns success bool.

    ``rc == 0`` alone is NOT treated as success: a nested ``claude-pee`` can exit
    cleanly with no output (see :func:`_child_claude_env`). When ``expected_output``
    is given, that file must exist and be non-empty; otherwise the run must have
    produced non-empty stdout. This prevents a fast empty exit from silently
    reading as DONE.

    Configurable via env:
      CLAUDE_BIN                        path to the claude CLI (default: PATH,
                                        then known install dirs)
      TRADING_SCHEDULE_PERMISSION_MODE  --permission-mode value (default bypassPermissions)
      TRADING_SCHEDULE_CLAUDE_FLAGS     extra space-separated flags appended verbatim
      TRADING_SCHEDULE_CLAUDE_EXIT_MODE "normal" (default) or "kill-ppid"
    """
    claude_bin = resolve_claude_bin()
    perm_mode = os.environ.get("TRADING_SCHEDULE_PERMISSION_MODE", "bypassPermissions")
    extra = os.environ.get("TRADING_SCHEDULE_CLAUDE_FLAGS", "").split()
    # Raise the wrapper's 300s default cap to the per-step budget (both paths).
    extra = [*extra, *_wrapper_timeout_flags(claude_bin, timeout, extra)]
    exit_mode = os.environ.get("TRADING_SCHEDULE_CLAUDE_EXIT_MODE", "normal").strip().lower()

    if exit_mode == "kill-ppid":
        return _run_claude_kill_ppid(
            prompt,
            label=label,
            dry_run=dry_run,
            timeout=timeout,
            claude_bin=claude_bin,
            perm_mode=perm_mode,
            extra=extra,
        )

    cmd = [claude_bin, "--permission-mode", perm_mode, *extra, prompt]

    log(f"--- claude workflow START: {label} (timeout={timeout}s, perm={perm_mode}) ---")
    log(
        f"command: {claude_bin} -p <prompt {len(prompt)} chars> --permission-mode {perm_mode}  {' '.join(extra)}",
        logging.INFO,
    )
    if dry_run:
        log("(dry-run) prompt that would be sent to claude:\n" + prompt)
        return True

    started = time.monotonic()
    try:
        res = _popen_tee(cmd, env=_child_claude_env(), timeout=timeout, stream=_stream_enabled())
    except subprocess.TimeoutExpired:
        log(
            f"{label}: claude timed out after {timeout}s ({time.monotonic() - started:.0f}s elapsed)",
            logging.ERROR,
        )
        return False
    except OSError as exc:
        log(f"{label}: could not launch claude ({exc})", logging.ERROR)
        return False

    elapsed = time.monotonic() - started
    if res.stdout:
        log(f"{label} stdout (tail):\n" + res.stdout[-2000:])
    # Log stderr on every path (not only on failure): claude-pee writes its
    # RUST_LOG diagnostics + the inner claude's warnings here, which are exactly
    # what you need when debugging a nested no-op / session-greeting capture.
    if res.stderr and res.stderr.strip():
        log(f"{label} stderr (tail):\n" + res.stderr[-2000:])
    if res.returncode != 0:
        log(f"{label}: FAILED rc={res.returncode} in {elapsed:.0f}s", logging.ERROR)
        return False

    # rc==0 is not enough: a nested/failed claude-pee exits clean with no output.
    if expected_output is not None:
        out = Path(expected_output)
        try:
            produced = out.is_file() and out.stat().st_size > 0
        except OSError:
            produced = False
        if not produced:
            log(
                f"{label}: no expected output at {_rel(out)} (rc=0, {elapsed:.0f}s) -- "
                "claude produced nothing",
                logging.ERROR,
            )
            return False
    elif not (res.stdout and res.stdout.strip()):
        log(
            f"{label}: claude produced no output (rc=0, {elapsed:.0f}s) -- "
            "likely empty/failed session",
            logging.ERROR,
        )
        return False

    log(f"--- claude workflow DONE: {label} in {elapsed:.0f}s ---")
    return True


# --------------------------------------------------------------------------- #
# Deterministic skill-script execution (auto mode)
# --------------------------------------------------------------------------- #
def _now_time() -> dt.time:
    """Current wall-clock time. Separated so tests can freeze it."""
    return dt.datetime.now().time()


def _read_json(path) -> dict | None:
    """Tolerant JSON-object reader: None on any I/O / parse / shape problem."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _read_vcp_universe() -> list[str]:
    """Tickers from the optional expanded VCP universe file (one per line, `#`
    comments and blanks skipped). Returns [] when the file is absent/empty, in
    which case the screener falls back to its bundled S&P 500."""
    try:
        lines = VCP_UNIVERSE_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [s.strip() for ln in lines if (s := ln.strip()) and not s.startswith("#")]


def _latest(directory: Path, pattern: str) -> Path | None:
    """Newest file matching pattern (by mtime), or None."""
    try:
        files = sorted(Path(directory).glob(pattern), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return files[-1] if files else None


def run_skill_script(
    cmd: list, *, label: str, dry_run: bool, timeout: int, output_glob: tuple | None = None
) -> Path | None:
    """Run one deterministic skill script under the current interpreter.

    Returns the newest file matching ``output_glob=(directory, pattern)`` that
    appeared during the run, or None on failure / dry-run / no output asked.
    Failures are logged and degrade the pipeline instead of crashing the slot.
    """
    full = [sys.executable] + [str(c) for c in cmd]
    log(f"--- skill script START: {label} (timeout={timeout}s) ---")
    if dry_run:
        log(f"(dry-run) would run: {' '.join(full)}")
        return None

    started_wall = time.time()
    started = time.monotonic()
    # TV_NO_CACHE=1: the trader wants screeners/heat on LIVE TradingView chart
    # data, never the metrics-cache snapshot (which can lag a day).
    env = {**os.environ, "TV_NO_CACHE": "1"}
    try:
        res = subprocess.run(
            full, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        log(f"{label}: timed out after {timeout}s", logging.ERROR)
        return None
    except OSError as exc:
        log(f"{label}: launch error: {exc}", logging.ERROR)
        return None

    elapsed = time.monotonic() - started
    if res.returncode != 0:
        log(
            f"{label}: FAILED rc={res.returncode} in {elapsed:.0f}s\n{(res.stderr or '')[-1000:]}",
            logging.ERROR,
        )
        return None
    log(f"--- skill script DONE: {label} in {elapsed:.0f}s ---")

    if not output_glob:
        return None
    directory, pattern = output_glob
    try:
        produced = [
            p for p in Path(directory).glob(pattern) if p.stat().st_mtime >= started_wall - 2
        ]
    except OSError:
        produced = []
    if not produced:
        log(f"{label}: завершился успешно, но не создал файл {pattern}", logging.WARNING)
        return None
    return max(produced, key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------- #
# Gate file (exposure_decision) I/O
# --------------------------------------------------------------------------- #
def decision_path(date_str: str) -> Path:
    return SCHEDULE_DIR / f"exposure_decision_{date_str}.json"


def read_decision(path: Path) -> dict:
    """Read the machine-readable exposure gate. Fail safe to ``restrict``."""
    fallback = {
        "decision": "restrict",
        "rationale": "Файл-гейт exposure_decision отсутствует или не читается; "
        "по умолчанию restrict (fail-safe — новый риск не открываем).",
        "degraded": True,
    }
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        fallback["rationale"] = f"не удалось разобрать {path.name} ({exc}); fail-safe restrict."
        return fallback
    decision = str(data.get("decision", "")).strip().lower()
    if decision not in VALID_DECISIONS:
        fallback["rationale"] = (
            f"exposure_decision='{data.get('decision')}' не входит в {VALID_DECISIONS}; "
            "fail-safe restrict."
        )
        return fallback
    data["decision"] = decision
    data.setdefault("degraded", False)
    return data


# --------------------------------------------------------------------------- #
# Prompt builders
# --------------------------------------------------------------------------- #
def regime_prompt(date_str: str, gate_path: Path, *, quick: bool) -> str:
    mode = (
        "Quick pre-open RE-CHECK of the market regime (the full screen ran the "
        "previous evening); be fast."
        if quick
        else "Full end-of-day market regime read on fresh EOD data."
    )
    return f"""Run the scheduled `market-regime-daily` workflow for {date_str} (CET schedule, US equities).
{mode}

Read the manifest at workflows/market-regime-daily.yaml and execute its steps in
order by invoking the named skills: market-breadth-analyzer, uptrend-analyzer,
(optional) market-top-detector and market-news-analyst, then exposure-coach.
Save the report artifacts (market_breadth, uptrend, market_top, exposure_posture,
news) under {_rel(MARKET_DIR)}/ — pass --output-dir {_rel(MARKET_DIR)}/ to every script.

CRITICAL -- also write a machine-readable gate file to EXACTLY this path:
  {gate_path}
as a single JSON object:
{{
  "workflow": "market-regime-daily",
  "date": "{date_str}",
  "decision": "allow" | "restrict" | "cash-priority",
  "net_exposure_ceiling_pct": <number or null>,
  "rationale": "<= 2 предложения, НА РУССКОМ ЯЗЫКЕ",
  "key_signals": ["короткие пункты НА РУССКОМ ЯЗЫКЕ", "..."]
}}
`decision` MUST be exactly one of allow / restrict / cash-priority -- the
exposure-coach posture (keep this enum value in English -- it is machine-read).
LANGUAGE: the free-text fields `rationale` and `key_signals` MUST be written in
Russian. Translate any English skill output into Russian for these two fields;
keep ticker symbols, skill names and numeric metrics as-is. Write the file even
on partial data; choose the most defensive posture the evidence supports. Do NOT
place any trades.

EXECUTION RULES (unattended headless run -- you must finish autonomously):
- Perform EVERY step with tool calls. Never end your reply with prose that only
  announces the next step (e.g. "running uptrend-analyzer next") -- run it
  immediately instead. A message with no tool call ENDS the session.
- Writing the gate file at {gate_path} is MANDATORY and must be your FINAL action.
  Do not stop, summarise, or hand back control until that file exists on disk.
- After writing it, Read it back to confirm it parses as valid JSON, then give a
  1-2 line summary. "Be fast" / short narration means terse prose, NOT skipping
  steps or the gate file."""


def monthly_prompt(date_str: str, summary_path: Path) -> str:
    return f"""Run the scheduled `monthly-performance-review` workflow for {date_str} (CET schedule).

Read the manifest at workflows/monthly-performance-review.yaml and execute its
steps via trader-memory-core, signal-postmortem and backtest-expert: aggregate
the month's closed theses (state dir {_rel(JOURNAL_DIR)}/theses), run the aggregate
postmortem and performance-coach review, revalidate hypotheses, and produce the
monthly decision log plus rule changes for next month. Save the report
artifacts under {_rel(JOURNAL_DIR)}/ (postmortems into {_rel(JOURNAL_DIR)}/postmortems/).

Also write a concise summary gate file to EXACTLY this path:
  {summary_path}
as JSON:
{{
  "workflow": "monthly-performance-review",
  "date": "{date_str}",
  "trades_closed": <int>,
  "win_rate_pct": <num or null>,
  "avg_R": <num or null>,
  "decision_log": ["short bullet", "..."],
  "rule_changes_for_next_month": ["short bullet", "..."]
}}
This is review only -- do NOT place any trades. Keep narration short."""


def validation_prompt(date_str: str, candidates: list, validation_path: Path) -> str:
    lines = "\n".join(
        f"- {c.get('ticker')} ({c.get('side')}): план вход {c.get('pivot')}, стоп {c.get('stop')}"
        for c in candidates
    )
    return f"""Hybrid auto-mode chart validation for {date_str} (US equities, swing horizon).

For EACH candidate below, apply the technical-analyst skill on the daily AND
weekly chart (TradingView Desktop MCP if running, otherwise the skill's data
layer). Judge structural integrity: base intact, not climactic or
over-extended, support not broken, volume consistent with the setup. Reject
ONLY on clear structural damage — when in doubt, pass.

For every candidate you PASS, ALSO return refined structural levels read off the
chart. These become the AUTHORITATIVE entry/stop/target for the watchlist
(position size is recomputed downstream from the risk profile, so return prices
only). Use the planner numbers above only as a starting reference and correct
them to the real chart structure:
  - "entry"  — breakout/trigger price (long) or breakdown trigger (short)
  - "stop"   — invalidation just beyond the structural low (long) / high (short)
  - "target" — first measured-move / next resistance (long) or support (short)
Geometry MUST hold: long → stop < entry < target; short → target < entry < stop.
For a "reject" verdict omit the levels (set them null).

Candidates:
{lines}

Write EXACTLY this JSON file to: {validation_path}
{{
  "date": "{date_str}",
  "verdicts": [
    {{"ticker": "XXX", "verdict": "pass" | "reject", "note": "<= 1 sentence",
      "entry": <num or null>, "stop": <num or null>, "target": <num or null>}}
  ]
}}
One verdict per candidate. Do NOT place any trades. Keep narration short."""


def weekly_prompt(date_str: str, summary_path: Path) -> str:
    return f"""Run the scheduled Saturday weekly market block for {date_str} (CET schedule).

The deterministic reports (IBD distribution days QQQ/SPY, macro regime, FTD
detector) were just generated under {_rel(MARKET_DIR)}/. Now:
1. Find the two manual market-top inputs via WebSearch: percent of S&P 500
   stocks above their 50DMA (barchart.com) and the CBOE equity put/call ratio
   (cboe.com).
2. Run skills/market-top-detector/scripts/market_top_detector.py
   --breadth-50dma <num> --put-call <num> --output-dir {_rel(MARKET_DIR)}/
3. Synthesize the week ahead from all fresh reports (exposure-coach
   perspective): selling pressure, top risk, macro regime, FTD status, what it
   means for the long and short branches.

Write a machine-readable summary to EXACTLY this path:
  {summary_path}
as JSON:
{{
  "workflow": "weekly-market-block",
  "date": "{date_str}",
  "top_risk_score": <num or null>,
  "top_risk_zone": "<zone>",
  "distribution_days": {{"qqq": <int or null>, "spy": <int or null>}},
  "macro_regime": "<regime>",
  "ftd_status": "<status>",
  "implications": ["short bullet", "..."]
}}
This is analysis only -- do NOT place any trades. Keep narration short."""


# --------------------------------------------------------------------------- #
# Reminder text helpers
#
# Telegram messages mirror the MyNotes journal templates
# (Финансы/Трейдинг/*/_TEMPLATE.md): a header line (Workflow · Артефакт), then
# 📌 ВЕРДИКТ -> 🧭 СВОДКА (одной строкой каждый) -> 💬 ОБОСНОВАНИЕ -> ✅ ДЕЙСТВИЕ.
# Kept compact for chat; the matching full journal note uses the same sections.
# --------------------------------------------------------------------------- #
SESSION_FOOTER = (
    "В сессию 15:30–22:00: вести позиции (trim +2R / трейл стопа), выход по "
    "стопу/таргету/слому. После закрытия любой сделки — trade-memory-loop."
)


def _ceiling_str(dec: dict) -> str:
    ceil = dec.get("net_exposure_ceiling_pct")
    return f" (потолок ~{ceil}%)" if ceil not in (None, "") else ""


def _verdict_exposure(dec: dict) -> str:
    flag = " ⚠️FAIL-SAFE" if dec.get("degraded") else ""
    return f"{dec['decision'].upper()}{flag}{_ceiling_str(dec)}"


def _signals_block(dec: dict, *, title: str = "СВОДКА") -> str:
    signals = [str(s).strip() for s in (dec.get("key_signals") or []) if str(s).strip()]
    if not signals:
        return ""
    body = "\n".join(f"• {s}" for s in signals[:6])
    return f"\n\n🧭 {title} (одной строкой каждый)\n{body}"


def _rationale_block(dec: dict) -> str:
    rationale = (dec.get("rationale") or "").strip()
    return f"\n\n💬 ОБОСНОВАНИЕ\n{rationale}" if rationale else ""


# Date-suffixed watchlist files ONLY. A bare glob("watchlist_*.json") also
# matches watchlist_validation_<date>.json (a different schema -- {date, verdicts},
# no `candidates`), which sorts AFTER watchlist_<date>.json lexicographically
# ('v' > any digit) and would be wrongly returned as the latest list -- making a
# fresh watchlist look stale/missing and suppressing OPEN signals + order cards.
_WATCHLIST_FILE_RE = re.compile(r"^watchlist_\d{4}-\d{2}-\d{2}\.json$")


def latest_watchlist() -> Path | None:
    files = sorted(
        f for f in SCHEDULE_DIR.glob("watchlist_*.json") if _WATCHLIST_FILE_RE.match(f.name)
    )
    return files[-1] if files else None


def _prev_us_trading_day(d: dt.date) -> dt.date:
    cur = d - dt.timedelta(days=1)
    while not is_us_trading_day(cur):
        cur -= dt.timedelta(days=1)
    return cur


def _watchlist_is_fresh(wl: dict | None, today: dt.date) -> bool:
    """A watchlist arms OPEN signals only when built today or on the previous
    US trading day (the evening run builds tomorrow's list). After a few
    failed evenings the 'latest' file is days old — its levels are stale and
    must not trigger entries. Unparsable/missing date counts as stale."""
    raw = (wl or {}).get("date")
    try:
        built = dt.date.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return False
    return built >= _prev_us_trading_day(today)


def _candidate_lines(candidates: list) -> str:
    lines = []
    for c in candidates[:8]:
        if not isinstance(c, dict):
            continue
        ticker = c.get("ticker", "?")
        setup = c.get("setup", "")
        parts = []
        if c.get("pivot") not in (None, ""):
            parts.append(f"вход ${c['pivot']}")
        if c.get("stop") not in (None, ""):
            parts.append(f"стоп ${c['stop']}")
        if c.get("target") not in (None, ""):
            parts.append(f"цель ${c['target']}")
        if c.get("shares") not in (None, ""):
            parts.append(f"{c['shares']} акц")
        tail = (" · " + " / ".join(parts)) if parts else ""
        sep = f" — {setup}" if setup else ""
        lines.append(f"• {ticker}{sep}{tail}")
    return "\n".join(lines)


def build_premarket_msg(date_str: str, dec: dict, wl: Path | None) -> str:
    allowed = dec["decision"] == "allow"
    # _rel (not relative_to): TRADING_DATE_DIR outside the repo would crash here.
    wl_line = f"Watchlist: {_rel(wl)}" if wl else "Watchlist не найден."
    if allowed:
        action = (
            "Поставить bracket-ордера по триггерам брейкаута (swing-execution-manage / вход).\n"
            f"{wl_line}\nСверить, что exposure всё ещё allow перед каждой постановкой."
        )
    else:
        action = (
            "НОВЫХ входов нет — только ведение/закрытие открытых позиций "
            "(swing-execution-manage). Watchlist не открывать."
        )
    return (
        f"🟢 PRE-OPEN · {date_str} (~15:00 CET)\n"
        "Workflow: market-regime-daily · exposure_decision\n\n"
        "📌 ВЕРДИКТ\n"
        f"• Экспозиция: {_verdict_exposure(dec)}\n"
        f"• Новые свинги: {'да' if allowed else 'нет'}"
        f"{_signals_block(dec)}"
        f"{_rationale_block(dec)}\n\n"
        f"✅ ДЕЙСТВИЕ\n{action}\n\n{SESSION_FOOTER}"
    )


def build_evening_closed_msg(date_str: str, dec: dict, extra: str = "") -> str:
    extra_block = f"\n{extra}" if extra else ""
    return (
        f"🌙 EVENING PREP · {date_str} (~22:15 CET)\n"
        "Workflow: market-regime-daily · exposure_decision\n\n"
        "📌 ВЕРДИКТ\n"
        f"• Экспозиция: {_verdict_exposure(dec)}\n"
        "• Гейт: ЗАКРЫТ — swing-opportunity-daily не запускался"
        f"{_signals_block(dec)}"
        f"{_rationale_block(dec)}\n\n"
        "✅ ДЕЙСТВИЕ\n"
        "Завтра: только ведение/закрытие открытых позиций. "
        "trade-memory-loop по закрытым сделкам."
        f"{extra_block}"
    )


def build_evening_short_msg(date_str: str, dec: dict, wl_rel, candidates: list, reason: str) -> str:
    cand_block = (
        f"\n\n🧭 ШОРТ-КАНДИДАТЫ (одной строкой каждый)\n{_candidate_lines(candidates)}"
        if candidates
        else "\n\n🧭 ШОРТ-КАНДИДАТЫ: скринер не дал кандидатов грейда B и выше."
    )
    return (
        f"🌙 EVENING PREP · {date_str} (~22:15 CET)\n"
        "Workflow: market-regime-daily + swing-short-screener · ШОРТ-ветка\n\n"
        "📌 ВЕРДИКТ\n"
        f"• Экспозиция: {_verdict_exposure(dec)}\n"
        f"• Рынок под давлением: {reason}\n"
        f"• Шорт-кандидатов на завтра: {len(candidates)}"
        f"{_rationale_block(dec)}"
        f"{cand_block}\n\n"
        "✅ ДЕЙСТВИЕ\n"
        "Завтра в сессию (15:30–22:00) мониторинг пришлёт триггеры OPEN_SHORT.\n"
        "Правила шорта: риск 1% (половинный), стоп НАД последним нижним максимумом, "
        "тайм-стоп 10 т.д., крыть 50% на 2R, НЕ держать через отчёт.\n"
        f"Файл: {wl_rel}"
    )


def _usd(v) -> str:
    if v in (None, ""):
        return "$?"
    try:
        return f"${float(v):g}"
    except (TypeError, ValueError):
        return f"${v}"


def _intraday_signal_lines(s: dict) -> str:
    ticker, sig_type, price = s["ticker"], s["type"], s.get("price")
    c = s.get("candidate") or {}
    p = s.get("position") or {}
    if sig_type in (tsig.OPEN_LONG, tsig.OPEN_SHORT):
        is_long = sig_type == tsig.OPEN_LONG
        qty = f"{c['shares']} акц" if c.get("shares") else "размер: position-sizer"
        risk = f" · риск {_usd(c.get('risk_dollars'))}" if c.get("risk_dollars") else ""
        tid = c.get("thesis_id")
        _cli = "python3 skills/trader-memory-core/scripts/trader_memory_cli.py"
        shares_str = str(c["shares"]) if c.get("shares") else "<N>"
        # `store` prefix: the CLI launcher has no bare `transition`.
        # `%z` (not `%:z`): BSD date on macOS emits a literal `:z`.
        journal_tail = (
            f"   {_cli} store transition {tid} ENTRY_READY --reason trigger\n"
            f"   {_cli} store open-position {tid}"
            f" --actual-price <ЦЕНА> --actual-date $(date +%FT%T%z) --shares {shares_str}"
            if tid
            else ""
        )
        if is_long:
            if tid:
                tail = f"   Bracket-ордер (вход + стоп + тейк). Записать вход:\n{journal_tail}"
            else:
                tail = "   Bracket-ордер (вход + стоп + тейк); после исполнения записать вход в журнал (шаг 3)."
        else:
            tail = "   Sell-short bracket; правила: риск 1%, тайм-стоп 10 т.д., не держать через отчёт."
            if journal_tail:
                tail += f"\n   Записать вход:\n{journal_tail}"
        return (
            f"{'🟢' if is_long else '🔻'} ОТКРОЙ {'ЛОНГ' if is_long else 'ШОРТ'} {ticker} "
            f"@ {_usd(price)} — триггер {_usd(c.get('pivot'))} сработал\n"
            f"   {qty} · стоп {_usd(c.get('stop'))} · цель {_usd(c.get('target'))}{risk}\n"
            f"{tail}"
        )
    if sig_type == tsig.MISSED:
        return (
            f"🚫 {ticker}: цена {_usd(price)} вне зоны входа "
            f"(хуже {_usd(c.get('worst_entry'))}) — НЕ гнаться."
        )
    if sig_type == tsig.SKIPPED_CAPACITY:
        return (
            f"⏸ {ticker}: триггер сработал @ {_usd(price)}, "
            f"но {s.get('reason', 'лимиты портфеля заняты')} — пропуск."
        )
    if sig_type == tsig.SKIPPED_EARNINGS:
        return (
            f"📅 {ticker}: триггер шорта @ {_usd(price)}, но отчёт {s.get('earnings_date', '?')} "
            f"(через {s.get('days_to_earnings', '?')} т.д.) — шорт НЕ открывать "
            "(правило 6.4: не держать шорт через отчёт)."
        )
    if sig_type == tsig.EARNINGS_SOON:
        head = (
            f"📅 {ticker}: отчёт {s.get('earnings_date', '?')} "
            f"(через {s.get('days_to_earnings', '?')} т.д.)"
        )
        if s.get("side") == "short":
            return head + " — шорт ЗАКРЫТЬ до отчёта (правило 6.4)."
        return head + " — реши заранее: держать лонг через отчёт или сократить."
    if sig_type == tsig.STOP_HIT:
        return (
            f"⛔️ {ticker}: цена {_usd(price)} за стопом {_usd(p.get('stop_loss'))} — "
            "проверь, что стоп-ордер исполнился. Вечером: закрыть тезис + постмортем (шаг 7)."
        )
    if sig_type == tsig.NEAR_STOP:
        return (
            f"⚠️ {ticker}: {_usd(price)} — до стопа {_usd(p.get('stop_loss'))} меньше 1%. "
            "Стоп не двигать; просто будь у экрана."
        )
    if sig_type == tsig.TWO_R:
        return (
            f"💰 {ticker}: +2R @ {_usd(price)} — продай 50% позиции, "
            f"стоп остатка в безубыток {_usd(p.get('entry_price'))}."
        )
    return f"• {ticker}: {sig_type} @ {_usd(price)}"


def build_intraday_msg(date_str: str, signals: list) -> str:
    body = "\n\n".join(_intraday_signal_lines(s) for s in signals)
    return (
        f"⚡️ INTRADAY · {date_str}\n"
        "Мониторинг watchlist + открытых позиций (цены могут отставать до ~15 мин)\n\n"
        f"{body}\n\n"
        "Журнал входов/выходов: python3 skills/trader-memory-core/scripts/trader_memory_cli.py store list"
    )


def build_weekly_msg(date_str: str, data: dict | None, det: dict) -> str:
    det_line = " · ".join(f"{'✅' if ok else '⚠️'} {name}" for name, ok in det.items())
    if not data:
        body = f"Сводный JSON не создан — см. отчёты в {_rel(MARKET_DIR)}/ и лог."
    else:
        dd = data.get("distribution_days") or {}
        body = (
            "📌 ВЕРДИКТ\n"
            f"• Top-risk: {data.get('top_risk_score', '?')} ({data.get('top_risk_zone', '?')})\n"
            f"• Distribution days: QQQ {dd.get('qqq', '?')} / SPY {dd.get('spy', '?')}\n"
            f"• Макрорежим: {data.get('macro_regime', '?')}\n"
            f"• FTD: {data.get('ftd_status', '?')}"
        )
        implications = data.get("implications") or []
        if implications:
            body += "\n\n🧭 НА НЕДЕЛЮ\n" + "\n".join(f"• {i}" for i in implications[:6])
    return (
        f"📅 WEEKLY · {date_str} (суббота ~12:00 CET)\n"
        "Workflow: weekly-market-block · ibd + macro + ftd + market-top\n\n"
        f"{body}\n\n"
        f"Скрипты: {det_line}\n\n"
        "✅ ДЕЙСТВИЕ\n"
        "Ничего руками: вечерние прогоны всю неделю подхватывают свежие JSON из "
        f"{_rel(MARKET_DIR)}/ (гейт + условия шорт-ветки)."
    )


def build_evening_allow_msg(date_str: str, dec: dict, wl_rel, candidates: list) -> str:
    cand_block = (
        f"\n\n🧭 КАНДИДАТЫ (одной строкой каждый)\n{_candidate_lines(candidates)}"
        if candidates
        else "\n\n🧭 КАНДИДАТЫ: ни один не прошёл гейты сегодня."
    )
    return (
        f"🌙 EVENING PREP · {date_str} (~22:15 CET)\n"
        "Workflow: market-regime-daily + swing-opportunity-daily · "
        "exposure_decision + watchlist\n\n"
        "📌 ВЕРДИКТ\n"
        f"• Экспозиция: {_verdict_exposure(dec)}\n"
        f"• Watchlist на завтра: {len(candidates)} кандидат(ов)"
        f"{_rationale_block(dec)}"
        f"{cand_block}\n\n"
        "✅ ДЕЙСТВИЕ\n"
        "Завтра pre-open (~15:00): сверить режим, поставить bracket-ордера по триггерам.\n"
        f"Файл: {wl_rel}"
    )


def build_monthly_msg(date_str: str, data: dict | None) -> str:
    if not data:
        body = f"Отчёт сформирован — см. {_rel(JOURNAL_DIR)}/."
    else:
        rules = data.get("rule_changes_for_next_month", []) or []
        rules_block = (
            "\n\n🧭 ПРАВИЛА НА СЛЕД. МЕСЯЦ\n" + "\n".join(f"• {r}" for r in rules[:8])
            if rules
            else ""
        )
        body = (
            "📌 ИТОГИ\n"
            f"• Закрыто сделок: {data.get('trades_closed', '?')} · "
            f"win-rate {data.get('win_rate_pct', '?')}% · avg R {data.get('avg_R', '?')}"
            f"{rules_block}\n\n"
            f"✅ ДЕЙСТВИЕ\nПеренести правила в {_rel(JOURNAL_DIR)}/monthly/{date_str[:7]}.md. "
            f"Полный отчёт в {_rel(JOURNAL_DIR)}/."
        )
    return (
        f"📊 MONTHLY REVIEW · {date_str} (1-е вс месяца, ~11:00 CET)\n"
        "Workflow: monthly-performance-review · monthly_decision_log + rule_changes\n\n"
        f"{body}"
    )


def regime_finish_prompt(date_str: str, gate_path: Path) -> str:
    return f"""Recovery run: a previous `market-regime-daily` attempt for {date_str} ended
WITHOUT writing the gate file. Your ONLY required deliverable now is that file.

Write the machine-readable gate to EXACTLY this path:
  {gate_path}
as a single JSON object:
{{
  "workflow": "market-regime-daily",
  "date": "{date_str}",
  "decision": "allow" | "restrict" | "cash-priority",
  "net_exposure_ceiling_pct": <number or null>,
  "rationale": "<= 2 предложения, НА РУССКОМ ЯЗЫКЕ",
  "key_signals": ["короткие пункты НА РУССКОМ ЯЗЫКЕ", "..."]
}}
The `decision` value stays in English (machine-read enum); the free-text fields
`rationale` and `key_signals` MUST be written in Russian.
Reuse any regime artifacts already saved under {_rel(MARKET_DIR)}/ (market_breadth,
uptrend, market_top, exposure_posture). If a needed input is missing, run that one
skill quickly (market-breadth-analyzer / uptrend-analyzer / exposure-coach) with
--output-dir {_rel(MARKET_DIR)}/. `decision` MUST be exactly allow / restrict /
cash-priority; choose the most defensive posture the evidence supports.

Perform every step with TOOL CALLS -- never end with prose that only announces a
step. Writing the file is MANDATORY and must be your FINAL action; after writing,
Read it back to confirm it parses. Do NOT place any trades."""


def run_regime_gate(
    date_str: str, gate: Path, *, quick: bool, label: str, args
) -> tuple[bool, dict]:
    """Run the regime workflow and guarantee a gate-file write is attempted.

    Headless ``claude -p`` occasionally ends a turn on a narration line before
    completing the workflow (e.g. "running uptrend-analyzer next") and never
    writes the gate, which then fail-safes to ``restrict``. When the process
    exits cleanly but the gate is missing, retry once with a focused
    finish-and-write prompt before falling back to the safe default.
    """
    ok = run_claude(
        regime_prompt(date_str, gate, quick=quick),
        label=label,
        dry_run=args.dry_run,
        timeout=args.timeout,
        expected_output=gate,
    )
    if not args.dry_run and not gate.exists():
        log(
            f"{label}: gate file not written on first pass -- retrying once (finish-and-write).",
            logging.WARNING,
        )
        ok2 = run_claude(
            regime_finish_prompt(date_str, gate),
            label=f"{label} [gate retry]",
            dry_run=False,
            timeout=args.timeout,
            expected_output=gate,
        )
        ok = ok or ok2

    if args.dry_run or ok:
        return ok, read_decision(gate)
    return ok, {
        "decision": "restrict",
        "rationale": "workflow market-regime-daily не завершился; fail-safe.",
        "degraded": True,
    }


# --------------------------------------------------------------------------- #
# Watchlist-order automation (Шаг 2) — producer hook + armed-ticker lookup
# --------------------------------------------------------------------------- #
def _pending_orders_path(date_str: str) -> Path:
    return TRADING_DATA_DIR / "logs" / f"pending_orders_{date_str}.json"


def armed_order_tickers(date_str: str) -> set[str]:
    """Tickers whose bracket is already placed/filled today (suppress OPEN dupes)."""
    ledger = _read_json(_pending_orders_path(date_str))
    if not ledger:
        return set()
    out: set[str] = set()
    for entry in (ledger.get("orders") or {}).values():
        if isinstance(entry, dict) and entry.get("status") in {"placed", "filled"}:
            ticker = entry.get("ticker")
            if ticker:
                out.add(str(ticker).upper())
    return out


def _gap_line(flag: dict) -> str:
    pm = flag.get("premarket_price")
    pm_s = f"${pm:g}" if isinstance(pm, (int, float)) else "?"
    return f"• {flag['ticker']} [{flag['verdict']}] премаркет {pm_s} — {flag.get('reason', '')}"


def _premarket_gap_block(date_str: str, wl_file, wl_data: dict, dec: dict, args) -> str:
    """Pre-open gap triage on the fresh watchlist.

    Fetches the pre-open price for each candidate, flags names that gapped out of
    their plan (EXTENDED past the chase band / INVALIDATED through the stop /
    earnings the morning of), and DROPS them from the watchlist file -- both the
    order-card producer and the intraday monitor read that file, so neither arms
    a gapped-out name. Returns a Telegram block (empty string when nothing is
    flagged or quotes are unavailable). Network-free on dry-run."""
    candidates = (wl_data or {}).get("candidates") or []
    tickers = sorted({str(c.get("ticker", "")).upper() for c in candidates} - {""})
    if not tickers:
        return ""
    if args.dry_run:
        log(
            f"(dry-run) premarket gap-gate: запросил бы премаркет-котировки для {', '.join(tickers)}"
        )
        return ""
    try:
        quotes = tsig.fetch_quotes(tickers, premarket=True)
    except tsig.QuotesError as exc:
        log(f"premarket gap-gate: премаркет-котировки недоступны: {exc}", logging.WARNING)
        return ""
    try:
        today = dt.date.fromisoformat(date_str)
    except ValueError:
        today = dt.date.today()
    flagged = tsig.premarket_gap_gate(wl_data, quotes, dec["decision"], today=today)
    if not flagged:
        log(f"premarket gap-gate: {len(tickers)} тикеров проверено — гэпов вне плана нет")
        return ""

    blocked = {f["ticker"] for f in flagged}
    kept = [c for c in candidates if str(c.get("ticker", "")).upper() not in blocked]
    gapped_out = [c for c in candidates if str(c.get("ticker", "")).upper() in blocked]
    if wl_file and gapped_out:
        wl_data["candidates"] = kept
        wl_data["gapped_out"] = (wl_data.get("gapped_out") or []) + gapped_out
        wl_data["gap_gated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        _atomic_write_json(Path(wl_file), wl_data)
        log(f"premarket gap-gate: снято с арминга {', '.join(sorted(blocked))}")
    return "⚠️ ПРЕМАРКЕТ-ГЭПЫ (сняты с входа на сегодня)\n" + "\n".join(
        _gap_line(f) for f in flagged
    )


def _send_watchlist_cards(date_str: str, dec: dict, args) -> None:
    """Producer hook: send Telegram order cards for ENTRY_READY candidates.

    Fires on any clean (non-degraded) gate — ``allow`` rings long candidates,
    ``restrict`` / ``cash-priority`` rings short candidates (the producer filters
    per side). Never on dry-run / --no-telegram. Placing the orders is a separate,
    opt-in step handled by the ``watchlist_orders.py listen`` daemon — this only
    rings the trader."""
    if args.dry_run or args.no_telegram:
        return
    # Fire on any clean (non-degraded) gate: allow -> long cards, restrict /
    # cash-priority -> short cards. The producer filters cards per side.
    if dec.get("degraded"):
        return
    run_skill_script(
        [WATCHLIST_ORDERS_SCRIPT, "send", "--date", date_str],
        label="watchlist-orders send (Шаг 2 cards)",
        dry_run=args.dry_run,
        timeout=args.timeout,
    )


def _send_scale_cards(date_str: str, signals: list, args) -> None:
    """For each +2R (TWO_R) signal, send an actionable scale-out confirmation card.

    The listen daemon places the sell-50% + breakeven-stop on confirmation. Per-day
    dedup is handled by the intraday `sent` state (so the card is sent once) and by
    the producer's own ledger guard."""
    if args.dry_run or args.no_telegram:
        return
    for s in signals:
        if s.get("type") != tsig.TWO_R:
            continue
        pos = s.get("position") or {}
        tid, shares, entry = pos.get("thesis_id"), pos.get("shares"), pos.get("entry_price")
        if not (tid and shares and entry):
            continue
        run_skill_script(
            [
                WATCHLIST_ORDERS_SCRIPT,
                "scale-card",
                "--date",
                date_str,
                "--thesis-id",
                tid,
                "--ticker",
                s["ticker"],
                "--side",
                s.get("side", "long"),
                "--shares",
                str(shares),
                "--entry",
                str(entry),
                "--price",
                str(s.get("price", entry)),
            ],
            label=f"+2R card {s['ticker']}",
            dry_run=args.dry_run,
            timeout=args.timeout,
        )


# --------------------------------------------------------------------------- #
# Slot handlers
# --------------------------------------------------------------------------- #
def slot_premarket(date_str: str, args) -> int:
    # Fresh heat snapshot so the intraday monitor sees today's open positions
    # (stops / entries) even before any evening run. Non-fatal on failure.
    run_skill_script(
        [TRADER_MEMORY_CLI, "heat"],
        label="portfolio-heat (premarket refresh)",
        dry_run=args.dry_run,
        timeout=args.timeout,
        output_glob=(JOURNAL_DIR, "portfolio_heat_*.json"),
    )
    gate = decision_path(date_str)
    ok, dec = run_regime_gate(
        date_str, gate, quick=True, label="market-regime-daily (premarket re-check)", args=args
    )
    log(
        f"exposure decision: {dec['decision'].upper()}"
        + (" (degraded/fail-safe)" if dec.get("degraded") else ""),
        logging.WARNING if dec.get("degraded") else logging.INFO,
    )

    wl_file = latest_watchlist()
    msg = build_premarket_msg(date_str, dec, wl_file)
    wl_data = _read_json(wl_file) if wl_file else None
    try:
        _today = dt.date.fromisoformat(date_str)
    except ValueError:
        _today = dt.date.today()
    if wl_data is not None and not _watchlist_is_fresh(wl_data, _today):
        msg = (
            f"⚠️ Watchlist устарел ({wl_data.get('date')}) — вечерний прогон не отработал; "
            "OPEN-сигналы сегодня НЕ придут, ордера по нему не ставить.\n\n" + msg
        )
    # Surface a missing TradingView Desktop IMMEDIATELY: tonight's screen and
    # alert sync need it (the heat refresh above reads only local YAML).
    if not args.dry_run and not tv_available():
        msg = (
            _tv_down_text("Premarket: вечерний скрин и алерты без TradingView не сработают.")
            + "\n\n"
            + msg
        )
    # Pre-open gap triage: drop candidates that gapped out of their plan overnight
    # so the order cards / intraday monitor below never arm them.
    if wl_data is not None and _watchlist_is_fresh(wl_data, _today):
        gap_block = _premarket_gap_block(date_str, wl_file, wl_data, dec, args)
        if gap_block:
            msg += f"\n\n{gap_block}"
    notify(msg, dry_run=args.dry_run, no_telegram=args.no_telegram)
    # Шаг 2: if the gate is open and the watchlist is fresh, ring the trader with
    # per-candidate order cards (the listen daemon places them on confirmation).
    if wl_data is not None and _watchlist_is_fresh(wl_data, _today):
        _send_watchlist_cards(date_str, dec, args)
    # Шаг 3: rule-violation exit cards for open positions (gate-independent).
    _send_close_cards(date_str, args)
    # Safety-net: positions closed outside the system since the last check.
    _reconcile_ib_closes(date_str, args)
    return 0 if ok or args.dry_run else 1


def _tv_down_text(context: str) -> str:
    return (
        "❗️ TradingView Desktop недоступен (CDP :9222).\n"
        f"{context}\n"
        "Запусти TradingView (tv launch / launch_tv_debug_mac.sh) и повтори слот."
    )


def _sync_tv_alerts(wl: dict, args) -> str:
    """Sync TV alerts with the watchlist; returns a digest line for Telegram.
    On TV being down sends its own IMMEDIATE notification."""
    if args.dry_run:
        log("(dry-run) would sync TradingView alerts with the watchlist")
        return ""
    if not tv_available():
        notify(
            _tv_down_text("Алерты TradingView по watchlist НЕ выставлены и не сняты."),
            dry_run=args.dry_run,
            no_telegram=args.no_telegram,
        )
        return "⏰ Алерты TV: НЕ выставлены — TradingView недоступен"
    res = talerts.sync_watchlist_alerts(wl, ALERTS_STATE_FILE, project_root=PROJECT_ROOT)
    line = (
        f"⏰ Алерты TV: +{res['created']} новых, −{res['deleted']} устаревших, "
        f"без изменений {res['kept']}"
    )
    if res.get("not_found_in_ui"):
        line += f", не найдено в UI {res['not_found_in_ui']} (удали вручную)"
    if res["errors"]:
        line += f", ошибок {res['errors']}"
        log("alert sync errors: " + "; ".join(res["error_details"]), logging.WARNING)
    log(line)
    return line


def _resolve_mcp_config() -> str | None:
    """Write a temp MCP-config JSON pointing at the vendored TradingView server.
    Returns the file path, or None if the vendored server is absent."""
    server_entry = PROJECT_ROOT / "vendor" / "tradingview-mcp" / "src" / "server.js"
    if not server_entry.is_file():
        return None
    config = {"mcpServers": {"tradingview": {"command": "node", "args": [str(server_entry)]}}}
    out = Path(_tempfile.gettempdir()) / "trading-schedule-tradingview-mcp.json"
    try:
        out.write_text(json.dumps(config), encoding="utf-8")
        return str(out)
    except OSError:
        return None


# Block heading the ticker-analysis skill actually writes (date FIRST):
#   ## 2026-06-12 — AOS — 🟢 BUY (reversal)
_SIGNAL_HEADING_RE = re.compile(
    r"^##\s+(\d{4}-\d{2}-\d{2})\s*[—\-]\s*([A-Za-z0-9.\-]+)\s*(?:[—\-]\s*(.*))?$",
    re.MULTILINE,
)


def _first_dollar(s: str) -> float | None:
    """First $-prefixed number in a line; bare number as fallback."""
    m = re.search(r"\$\s*(\d+(?:\.\d+)?)", s)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _all_dollars(s: str) -> list[float]:
    out = [float(x) for x in re.findall(r"\$\s*(\d+(?:\.\d+)?)", s)]
    if not out:
        out = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)", s)]
    return out


def _parse_signals_md(ticker: str) -> dict | None:
    """Parse the latest signal block for ticker from analysis/signals.md.

    Mirrors ui/server/src/lib/signals.ts (parseSignalBlocks + parseSignalLevels)
    against the REAL format the ticker-analysis skill writes: date-first
    heading, status emoji (🟢 BUY / 🟡 HOLD / 🔴 SELL), `$`-prefixed numbers,
    `**Trigger для Long/Short:**` lines, «Альтернатива» lines skipped. The old
    implementation expected a ticker-first heading, a nonexistent `Direction:`
    field and bare numbers — it parsed 0 real blocks, leaving the auto-analyze
    reconcile dead. A 🟡 HOLD block never arms levels.

    Returns direction/trigger/stop/t1/t2/t3 + entry_low/entry_high, or None.
    """
    signals_file = TRADING_DATA_DIR / "analysis" / "signals.md"
    try:
        text = signals_file.read_text(encoding="utf-8")
    except OSError:
        return None

    T = ticker.upper()
    latest: tuple[str, str, str] | None = None  # (date, status, body)
    for chunk in text.split("\n---\n"):
        m = _SIGNAL_HEADING_RE.search(chunk)
        if m and m.group(2).upper() == T:
            latest = (m.group(1), (m.group(3) or "").strip(), chunk)
    if latest is None:
        return None
    date, status, body = latest

    if "HOLD" in status.upper() or "🟡" in status:
        return None

    direction = None
    if re.search(r"🟢\s*BUY", body):
        direction = "long"
    elif re.search(r"🔴\s*SELL", body):
        direction = "short"

    trigger = None
    for line in body.splitlines():
        m = re.search(
            r"\*\*Trigger\s+(?:для\s+)?(Long|Short)\b[^:]*:\*\*\s*(.+)$", line, re.IGNORECASE
        )
        if m:
            trigger = _first_dollar(m.group(2))
            if direction is None:
                direction = "long" if m.group(1).lower() == "long" else "short"
            break
    if direction not in ("long", "short") or trigger is None:
        return None

    stop = t1 = t2 = t3 = entry_low = entry_high = None
    for line in body.splitlines():
        if re.search(r"Альтернатив[ау]", line, re.IGNORECASE):
            continue
        if stop is None:
            m = re.search(r"\*\*Stop:\*\*\s*(.+)$", line, re.IGNORECASE)
            if m:
                stop = _first_dollar(m.group(1))
                continue
        if t1 is None:
            m = re.search(r"\*\*T1(?:\s*/\s*T2)?(?:\s*/\s*T3)?:\*\*\s*(.+)$", line, re.IGNORECASE)
            if m:
                nums = _all_dollars(m.group(1))
                t1 = nums[0] if nums else None
                t2 = nums[1] if len(nums) > 1 else None
                t3 = nums[2] if len(nums) > 2 else None
                continue
        if entry_low is None:
            m = re.search(r"\*\*Entry[^:]*:\*\*\s*(.+)$", line, re.IGNORECASE)
            if m:
                nums = _all_dollars(m.group(1))
                if nums:
                    entry_low, entry_high = min(nums), max(nums)

    if None in (trigger, stop, t1):
        return None
    return {
        "ticker": T,
        "date": date,
        "direction": direction,
        "trigger": trigger,
        "stop": stop,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "entry_low": entry_low,
        "entry_high": entry_high,
    }


def _run_ticker_analysis(ticker: str, args) -> bool:
    """Run ticker-analysis skill via headless Claude + TradingView MCP.

    Delegates to run_claude() (respects TRADING_SCHEDULE_CLAUDE_EXIT_MODE).
    MCP flags injected via TRADING_SCHEDULE_CLAUDE_FLAGS for this call only.
    Capped at TICKER_ANALYSIS_TIMEOUT_S so one slow ticker doesn't block the slot.
    """
    prompt = (
        f"Проанализируй тикер {ticker}: запусти скил ticker-analysis — полный комплексный анализ "
        f"(новости, фундаментал, технический анализ через TradingView MCP). "
        f"Сохрани четыре markdown-файла и daily/weekly скриншоты в "
        f"trading-data/analysis/{ticker}/. Алерты в TradingView НЕ создавай."
    )
    mcp_config = _resolve_mcp_config()
    extra_flags = f"--model {TICKER_ANALYSIS_MODEL}"
    if mcp_config:
        extra_flags += f" --mcp-config {mcp_config} --strict-mcp-config"

    if not args.dry_run:
        (TRADING_DATA_DIR / "analysis" / ticker).mkdir(parents=True, exist_ok=True)

    # Temporarily extend CLAUDE_FLAGS for MCP + model; restore after the call.
    orig = os.environ.get("TRADING_SCHEDULE_CLAUDE_FLAGS", "")
    combined = (orig + " " + extra_flags).strip()
    os.environ["TRADING_SCHEDULE_CLAUDE_FLAGS"] = combined
    try:
        return run_claude(
            prompt,
            label=f"ticker-analysis ({ticker})",
            dry_run=args.dry_run,
            timeout=min(args.timeout, TICKER_ANALYSIS_TIMEOUT_S),
        )
    finally:
        if orig:
            os.environ["TRADING_SCHEDULE_CLAUDE_FLAGS"] = orig
        else:
            os.environ.pop("TRADING_SCHEDULE_CLAUDE_FLAGS", None)


def _invalidate_thesis(thesis_id: str, *, reason: str) -> None:
    """Move a thesis to INVALIDATED via trader-memory-cli (best-effort).

    The state machine forbids `transition <id> INVALIDATED` (terminal states go
    through terminate()), and the CLI launcher routes only store/ingest/review/
    heat — so the correct invocation is `store terminate`."""
    cmd = [
        sys.executable,
        str(TRADER_MEMORY_CLI),
        "store",
        "terminate",
        thesis_id,
        "--terminal-status",
        "INVALIDATED",
        "--exit-reason",
        reason,
    ]
    try:
        res = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            log(
                f"thesis invalidate {thesis_id}: rc={res.returncode} {res.stderr.strip()}",
                logging.WARNING,
            )
        else:
            log(f"thesis invalidated: {thesis_id} ({reason})")
    except (subprocess.SubprocessError, OSError) as exc:
        log(f"thesis invalidate {thesis_id}: {exc}", logging.WARNING)


# Pre-position thesis states (registered but not yet an open position). Theses in
# these states are safe to invalidate on a regime flip; ACTIVE / PARTIALLY_CLOSED
# back real positions and are never auto-terminated here.
NON_OPEN_THESIS_STATES = ("IDEA", "ENTRY_READY")


def _list_theses() -> list[dict]:
    """Current theses (thesis_id / ticker / side / status / ...) via the
    trader-memory `store list` JSON output. Empty list on any failure."""
    cmd = [sys.executable, str(TRADER_MEMORY_CLI), "store", "list"]
    try:
        res = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120)
    except (subprocess.SubprocessError, OSError) as exc:
        log(f"thesis list failed: {exc}", logging.WARNING)
        return []
    if res.returncode != 0:
        log(f"thesis list: rc={res.returncode} {(res.stderr or '').strip()[:200]}", logging.WARNING)
        return []
    try:
        data = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _terminate_offside_theses(dec: dict, args) -> list[str]:
    """Regime-flip hygiene: INVALIDATE non-open (IDEA / ENTRY_READY) theses whose
    side is opposite the current regime -- pending SHORT ideas when the gate is
    ``allow``, pending LONG ideas when it is restrict / cash-priority. Open
    positions (ACTIVE / PARTIALLY_CLOSED) and already-terminal theses are never
    touched. Skipped on a degraded fail-safe gate (regime unknown) and, for the
    actual writes, on dry-run (lists + logs only). Returns the thesis_ids that
    were (or, in dry-run, would be) invalidated."""
    if dec.get("degraded"):
        return []
    decision = dec.get("decision")
    regime = "long" if decision == "allow" else "short"
    wrong_side = "short" if decision == "allow" else "long"
    offside = [
        t
        for t in _list_theses()
        if str(t.get("status", "")).upper() in NON_OPEN_THESIS_STATES
        and str(t.get("side") or "long").lower() == wrong_side
        and t.get("thesis_id")
    ]
    if not offside:
        return []
    ids = [t["thesis_id"] for t in offside]
    if args.dry_run:
        log(
            f"(dry-run) would invalidate {len(ids)} off-side ({wrong_side}) non-open "
            f"thesis/theses on {regime} regime: " + ", ".join(ids)
        )
        return ids
    for t in offside:
        _invalidate_thesis(
            t["thesis_id"],
            reason=f"regime -> {regime} ({decision}): off-side {wrong_side} thesis, not yet a position",
        )
    log(
        f"regime-flip hygiene: invalidated {len(ids)} off-side ({wrong_side}) non-open "
        f"thesis/theses on {regime} regime: " + ", ".join(ids)
    )
    return ids


def _offside_note(terminated: list[str] | None) -> str:
    """One-line Telegram note summarising regime-flip thesis invalidation."""
    if not terminated:
        return ""
    return (
        f"♻️ Смена режима: инвалидировано {len(terminated)} непозиционных "
        "тезисов другого направления (IDEA/ENTRY_READY)."
    )


def _profile_sized_shares(profile: dict, pivot, stop) -> tuple[int | None, float | None]:
    """Risk-budget sizing for an analysis-updated candidate (mirrors the UI's
    reconcile.ts): shares = account x risk% / |pivot - stop|, capped at
    max_position_pct of the account. Never inherits the candidate's previous
    risk_dollars — that is the *achieved post-cap* risk of the old geometry,
    not a budget (a capped 0.1%-risk short once resized a flipped long to 1/9
    of the intended risk). Returns (None, None) when the profile cannot size."""
    try:
        account = float(profile.get("account_size") or 0)
        pivot_f = float(pivot)
        dist = abs(pivot_f - float(stop))
    except (TypeError, ValueError):
        return None, None
    if account <= 0 or pivot_f <= 0 or dist <= 0:
        return None, None
    risk_pct = float(profile.get("risk_pct") or 1.0)
    cap_pct = float(profile.get("max_position_pct") or 25.0)
    shares = min(int(account * risk_pct / 100 / dist), int(account * cap_pct / 100 / pivot_f))
    if shares <= 0:
        return None, None
    return shares, round(shares * dist, 2)


def _coerce_price(x) -> float | None:
    """Positive float or None (validation levels may arrive as str / None / 0)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _recently_analyzed(ticker: str, n_weekdays: int) -> bool:
    """True when a full ticker-analysis report.md for ``ticker`` exists under
    analysis/<TICKER>/<date>/ dated within the last ``n_weekdays`` weekdays.
    Lets the deep dive skip a name we already analyzed this week and spend the
    single-analysis budget on the next, not-recently-analyzed candidate."""
    adir = TRADING_DATA_DIR / "analysis" / ticker.upper()
    if not adir.is_dir():
        return False
    for sub in adir.iterdir():
        if not sub.is_dir() or not (sub / "report.md").exists():
            continue
        ws = _weekdays_since(sub.name)
        if ws is not None and ws <= n_weekdays:
            return True
    return False


def _apply_validation_levels(wl: dict, wl_path: Path, validation: dict | None, args) -> dict:
    """Option C: make the technical-analyst chart-validation step authoritative
    for entry/stop/target. For every surviving candidate validation PASSED *with*
    structural levels, override the planner's mechanical pivot/stop/target with
    the chart levels and re-size from the risk profile (planner sizing math on the
    new stop). The planner's original numbers are preserved under
    ``screener_origin``; the candidate is tagged ``source="chart-validation"``.
    Candidates with no levels (or a reject / bad geometry) keep the planner
    numbers untouched. Writes the watchlist in-place; returns wl."""
    verdicts = {
        str(v.get("ticker", "")).upper(): v
        for v in (validation or {}).get("verdicts") or []
        if isinstance(v, dict) and v.get("ticker")
    }
    if not verdicts:
        return wl
    profile = _read_json(TRADING_DATA_DIR / "trading_profile.json") or {}
    account = float(profile.get("account_size") or 0)
    chase = tsig.DEFAULT_CHASE_PCT / 100
    candidates = list(wl.get("candidates") or [])
    changed = False

    for i, cand in enumerate(candidates):
        ticker = str(cand.get("ticker", "")).upper()
        v = verdicts.get(ticker)
        if not v or str(v.get("verdict", "")).lower() != "pass":
            continue
        entry, stop = _coerce_price(v.get("entry")), _coerce_price(v.get("stop"))
        if entry is None or stop is None:
            continue
        target = _coerce_price(v.get("target"))
        side = str(cand.get("side", "long")).lower()

        if side == "short":
            if stop <= entry or (target is not None and target >= entry):
                log(
                    f"chart-validation: {ticker} bad short geometry "
                    f"(entry={entry}, stop={stop}, target={target}) — keeping planner levels",
                    logging.WARNING,
                )
                continue
            worst = round(entry * (1 - chase), 2)
            shares = tsig.size_short(account, entry, stop) or None
            risk_dollars = round(shares * (stop - entry), 2) if shares else None
        else:
            if stop >= entry or (target is not None and target <= entry):
                log(
                    f"chart-validation: {ticker} bad long geometry "
                    f"(entry={entry}, stop={stop}, target={target}) — keeping planner levels",
                    logging.WARNING,
                )
                continue
            worst = round(entry * (1 + chase), 2)
            shares, risk_dollars = _profile_sized_shares(profile, entry, stop)
            if shares is None:
                shares, risk_dollars = cand.get("shares"), cand.get("risk_dollars")

        screener_origin = cand.get("screener_origin") or {
            "side": cand.get("side"),
            "pivot": cand.get("pivot"),
            "stop": cand.get("stop"),
            "target": cand.get("target"),
            "shares": cand.get("shares"),
            "score": cand.get("score"),
            "source_plan": wl.get("source_plan"),
        }
        base_note = (cand.get("validation_note") or "").strip()
        note = (
            f"{base_note} · уровни из chart-validation"
            if base_note
            else "уровни из chart-validation"
        )
        candidates[i] = {
            **cand,
            "pivot": entry,
            "worst_entry": worst,
            "stop": stop,
            "target": target if target is not None else cand.get("target"),
            "shares": shares,
            "risk_dollars": risk_dollars,
            "validation_note": note,
            "validated": True,
            "source": "chart-validation",
            "screener_origin": screener_origin,
        }
        changed = True
        log(
            f"chart-validation: {ticker} levels-authoritative entry={entry} stop={stop}"
            + (f" target={target}" if target is not None else "")
        )

    if changed:
        wl["candidates"] = candidates
        if not args.dry_run:
            _atomic_write_json(wl_path, wl)
            log(f"chart-validation: watchlist saved → {_rel(wl_path)}")
    return wl


def _auto_analyze_reconcile(wl: dict, wl_path: Path, date_str: str, args) -> dict:
    """Deep ticker-analysis + reconcile for up to AUTO_ANALYZE_TOP_N (=1) of the
    best *fresh* watchlist candidates — the single deepest news+fundamental pass.

    Walks candidates in rank order, skipping any analyzed within
    FRESH_ANALYSIS_WEEKDAYS, and deep-dives the first not-fresh one(s) up to the
    budget. For each analyzed name:
    - direction match  → update pivot/stop/target/shares from analysis signal
    - direction-flip   → remove from candidates (→ rejected_by_validation), invalidate thesis
    - no signal parsed → keep as-is (log warning)

    Every other candidate keeps its chart-validation levels. Writes the watchlist
    in-place and returns the mutated wl dict."""
    candidates = list(wl.get("candidates") or [])
    if not candidates:
        return wl

    runs = 0
    for cand in list(candidates):
        if runs >= AUTO_ANALYZE_TOP_N:
            break
        ticker = str(cand.get("ticker", "")).upper()
        if not ticker:
            continue
        if _recently_analyzed(ticker, FRESH_ANALYSIS_WEEKDAYS):
            log(
                f"auto-analyze: {ticker} analyzed within {FRESH_ANALYSIS_WEEKDAYS} weekdays "
                "— keeping chart-validation levels, skipping deep dive"
            )
            continue
        runs += 1
        log(f"auto-analyze: deep ticker-analysis for {ticker} (budget {runs}/{AUTO_ANALYZE_TOP_N})")

        ok = _run_ticker_analysis(ticker, args)
        if not ok and not args.dry_run:
            log(f"auto-analyze: {ticker} analysis failed — keeping watchlist unchanged")
            continue

        signal = _parse_signals_md(ticker)
        if signal is None:
            log(f"auto-analyze: {ticker} — no signal parsed from signals.md, keeping as-is")
            continue

        sig_dir = signal["direction"]
        cand_side = str(cand.get("side", "")).lower()

        if sig_dir != cand_side:
            log(f"auto-analyze: {ticker} direction-flip ({cand_side} → {sig_dir}) — excluding")
            excluded = dict(cand)
            excluded["validation_note"] = (
                f"Excluded by auto-analysis: signal={sig_dir}, screener={cand_side} ({signal['date']})"
            )
            excluded["source"] = "analysis-excluded"
            excluded["validated"] = False
            candidates = [c for c in candidates if str(c.get("ticker", "")).upper() != ticker]
            rejected = [
                c
                for c in (wl.get("rejected_by_validation") or [])
                if str(c.get("ticker", "")).upper() != ticker
            ]
            wl["rejected_by_validation"] = rejected + [excluded]
            wl["candidates"] = candidates
            tid = cand.get("thesis_id")
            if tid and not args.dry_run:
                _invalidate_thesis(tid, reason=f"analysis direction-flip: signal={sig_dir}")
        else:
            log(
                f"auto-analyze: {ticker} levels-updated from analysis ({signal['date']}): "
                f"trigger={signal['trigger']} stop={signal['stop']} t1={signal['t1']}"
            )
            profile = _read_json(TRADING_DATA_DIR / "trading_profile.json") or {}
            shares, risk_dollars = _profile_sized_shares(profile, signal["trigger"], signal["stop"])
            if shares is None:
                shares, risk_dollars = cand.get("shares"), cand.get("risk_dollars")
            # worst_entry from the analysis Entry range when present, else the
            # standard chase band — never the trigger itself (zero chase room
            # turns the first tick past the trigger into MISSED + alert purge).
            chase = tsig.DEFAULT_CHASE_PCT / 100
            if cand_side == "short":
                worst = signal.get("entry_low") or round(signal["trigger"] * (1 - chase), 2)
            else:
                worst = signal.get("entry_high") or round(signal["trigger"] * (1 + chase), 2)
            screener_origin = cand.get("screener_origin") or {
                "side": cand.get("side"),
                "pivot": cand.get("pivot"),
                "stop": cand.get("stop"),
                "target": cand.get("target"),
                "shares": cand.get("shares"),
                "score": cand.get("score"),
                "source_plan": wl.get("source_plan"),
            }
            updated = {
                **cand,
                "pivot": signal["trigger"],
                "worst_entry": worst,
                "stop": signal["stop"],
                "target": signal["t1"],
                "t1": signal["t1"],
                "t2": signal["t2"],
                "t3": signal["t3"],
                "shares": shares,
                "risk_dollars": risk_dollars,
                "validation_note": f"From ticker-analysis (signals.md {signal['date']})",
                "validated": True,
                "source": "analysis",
                "screener_origin": screener_origin,
            }
            candidates = [
                updated if str(c.get("ticker", "")).upper() == ticker else c for c in candidates
            ]
            wl["candidates"] = candidates

    if not args.dry_run:
        _atomic_write_json(wl_path, wl)
        log(f"auto-analyze: watchlist saved → {_rel(wl_path)}")
    return wl


def _run_chart_validation(date_str: str, candidates: list, args) -> dict | None:
    """Hybrid step: claude + technical-analyst verdicts for the top candidates.
    None (no filtering) when there is nothing to validate or claude failed."""
    if not candidates:
        return None
    vpath = SCHEDULE_DIR / f"watchlist_validation_{date_str}.json"
    ok = run_claude(
        validation_prompt(date_str, candidates, vpath),
        label="chart validation (technical-analyst)",
        dry_run=args.dry_run,
        timeout=args.timeout,
        expected_output=vpath,
    )
    if not ok or args.dry_run:
        return None
    return _read_json(vpath)


def _validation_candidates_from_plan(plan: dict | None) -> list[dict]:
    orders = (plan or {}).get("actionable_orders") or []
    ranked = sorted(orders, key=lambda o: o.get("composite_score") or 0, reverse=True)
    return [
        {
            "ticker": o.get("symbol"),
            "side": "long",
            "pivot": (o.get("trade_plan") or {}).get("signal_entry"),
            "stop": (o.get("trade_plan") or {}).get("stop_loss_price"),
        }
        for o in ranked[:VALIDATION_TOP_N]
    ]


def _validation_note(wl: dict, validation: dict | None) -> str:
    rejected = wl.get("rejected_by_validation") or []
    if validation is None:
        if not (wl.get("candidates") or rejected):
            return ""
        return "графики не проверены (валидация недоступна — действуй по чек-листу 5.3)"
    passed = sum(1 for c in wl.get("candidates") or [] if c.get("validated"))
    note = f"проверено по графикам: {passed} прошло, {len(rejected)} отклонено"
    if rejected:
        note += " (" + ", ".join(c.get("ticker", "?") for c in rejected) + ")"
    return note


def _atomic_write_json(path: Path, data: dict) -> None:
    """Crash/torn-read-safe JSON write (tmp + rename): the intraday monitor
    and the UI read these files while the evening pipeline rewrites them."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_watchlist(wl: dict, date_str: str, args) -> Path:
    wl_path = SCHEDULE_DIR / f"watchlist_{date_str}.json"
    if not args.dry_run:
        SCHEDULE_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(wl_path, wl)
    return wl_path


def _ingest_theses(
    source: str,
    input_path: Path,
    wl: dict,
    wl_path: Path,
    date_str: str,
    args,
    *,
    plan_path: Path | None = None,
) -> None:
    """Register watchlist candidates as theses and inject thesis_id back into
    the watchlist (the intraday OPEN signal embeds the journal commands)."""
    ids_output_path = SCHEDULE_DIR / f"thesis_ids_{date_str}.json"
    ingest_cmd = [
        TRADER_MEMORY_CLI,
        "ingest",
        "--source",
        source,
        "--input",
        str(input_path),
        "--watchlist-filter",
        str(wl_path),
        "--ids-output",
        str(ids_output_path),
    ]
    if plan_path:
        ingest_cmd += ["--plan-input", str(plan_path)]
    run_skill_script(
        ingest_cmd,
        label=f"thesis-ingest ({source})",
        dry_run=args.dry_run,
        timeout=args.timeout,
    )
    ticker_to_tid = _read_json(ids_output_path) if ids_output_path.exists() else {}
    if ticker_to_tid:
        for cand in wl.get("candidates") or []:
            tid = ticker_to_tid.get(str(cand.get("ticker", "")).upper())
            if tid:
                cand["thesis_id"] = tid
        _atomic_write_json(wl_path, wl)
        log(f"thesis-ingest: thesis_id injected into {len(ticker_to_tid)} watchlist candidate(s)")


def _evening_long_branch(date_str: str, args) -> tuple[Path, dict, str]:
    """Deterministic screen -> plan -> hybrid validation -> watchlist + theses."""
    # Heat FIRST (cheap, local): without the ledger the planner would assume a
    # zero-risk baseline and silently ignore the 6% heat ceiling with real
    # positions open. No heat -> no new risk (fail-safe), and we skip the
    # expensive screen entirely.
    heat = run_skill_script(
        [TRADER_MEMORY_CLI, "heat"],
        label="portfolio-heat",
        dry_run=args.dry_run,
        timeout=args.timeout,
        output_glob=(JOURNAL_DIR, "portfolio_heat_*.json"),
    )
    if heat is None and not args.dry_run:
        log(
            "portfolio-heat недоступен — лонг-пайплайн пропущен "
            "(fail-safe: без heat-леджера новый риск не планируем)",
            logging.ERROR,
        )
        wl = tsig.build_watchlist(
            date_str,
            "allow",
            None,
            None,
            None,
            notes="heat-отчёт не построился — скрин пропущен, новый риск заблокирован (fail-safe)",
        )
        wl_path = _write_watchlist(wl, date_str, args)
        return (
            wl_path,
            wl,
            (
                "⚠️ heat-отчёт не построился — скрин и планирование пропущены, "
                "новый риск заблокирован (fail-safe). Проверь trader_memory_cli heat вручную."
            ),
        )

    vcp_cmd = [VCP_SCREEN_SCRIPT, "--top", "10"]
    universe = _read_vcp_universe()
    vcp_label = "vcp-screener (top 10)"
    if universe:
        # Bars are fetched for the whole universe at the quote/pre-filter stage,
        # so raising --max-candidates only adds (cheap) local VCP math — it does
        # NOT add network cost. Without it the pre-filter would cap full analysis
        # at the default 100 and the wider universe would be largely wasted.
        vcp_cmd += ["--universe", *universe, "--max-candidates", str(len(universe))]
        vcp_label = f"vcp-screener (top 10, universe={len(universe)})"
    vcp = run_skill_script(
        vcp_cmd,
        label=vcp_label,
        dry_run=args.dry_run,
        timeout=args.timeout,
        output_glob=(SCREENERS_DIR, "vcp_screener_*.json"),
    )
    plan_path = None
    if vcp:
        cmd = [PLANNER_SCRIPT, "--input", vcp]
        if heat:
            cmd += ["--current-exposure-json", heat]
        plan_path = run_skill_script(
            cmd,
            label="breakout-trade-planner",
            dry_run=args.dry_run,
            timeout=args.timeout,
            output_glob=(PLANS_DIR, "breakout_trade_plan_*.json"),
        )
    plan = _read_json(plan_path) if plan_path else None

    validation = _run_chart_validation(date_str, _validation_candidates_from_plan(plan), args)
    wl = tsig.build_watchlist(
        date_str,
        "allow",
        plan,
        None,
        validation,
        source_plan=_rel(plan_path) if plan_path else None,
    )
    wl_path = _write_watchlist(wl, date_str, args)
    if vcp and not args.dry_run:
        _ingest_theses("vcp-screener", vcp, wl, wl_path, date_str, args, plan_path=plan_path)
    # Chart-validation levels are authoritative for every passed candidate;
    # the single deep ticker-analysis then refines / flips at most one of them.
    wl = _apply_validation_levels(wl, wl_path, validation, args)
    wl = _auto_analyze_reconcile(wl, wl_path, date_str, args)
    return wl_path, wl, _validation_note(wl, validation)


def _weekdays_since(date_iso: str) -> int | None:
    """Weekdays elapsed since a YYYY-MM-DD date; None on a bad date."""
    try:
        start = dt.date.fromisoformat(date_iso)
    except (TypeError, ValueError):
        return None
    return tsig.weekdays_until(dt.date.today().isoformat(), start)


def _ftd_from_detector(path: Path | None) -> tuple[bool, str] | None:
    """FTD verdict from the dedicated ftd-detector report (the full O'Neil
    state machine: per-index state, invalidation, dates). Returns
    (blocking, note) or None when the report is missing/unreadable.

    Only a CONFIRMED, non-invalidated FTD within FTD_FRESH_WEEKDAYS blocks the
    short branch: the old recursive ftd_detected grep treated a 3-month-old or
    even an *invalidated* FTD (the textbook short trigger) as fresh.
    """
    data = _read_json(path) if path else None
    if not data:
        return None
    invalidated = bool((data.get("ftd_invalidation") or {}).get("invalidated"))
    best_date = None
    confirmed = False
    for idx in ("sp500", "nasdaq"):
        node = data.get(idx) or {}
        ftd = node.get("ftd") or {}
        if str(node.get("state") or "") == "FTD_CONFIRMED" and ftd.get("ftd_detected"):
            confirmed = True
            d = ftd.get("ftd_date")
            if d and (best_date is None or d > best_date):
                best_date = d
    if not confirmed:
        return False, "ftd-detector: подтверждённого FTD нет"
    if invalidated:
        return False, "ftd-detector: FTD инвалидирован (пробой ниже лоу FTD)"
    if best_date is not None:
        age = _weekdays_since(best_date)
        if age is not None and age > FTD_FRESH_WEEKDAYS:
            return False, f"FTD {best_date} старше {FTD_FRESH_WEEKDAYS} т.д. — не блокирует"
    return True, f"подтверждённый FTD {best_date or '(дата неизвестна)'}"


def _dd_count_from_ibd(path: Path | None) -> float | None:
    """Worst-index d25 count from the IBD monitor (the O'Neil-correct count
    with 25-session expiry and 5% rally invalidation). None when unavailable."""
    data = _read_json(path) if path else None
    rows = ((data or {}).get("market_distribution_state") or {}).get("index_results") or []
    counts = [r.get("d25_count") for r in rows if isinstance(r, dict)]
    counts = [c for c in counts if isinstance(c, (int, float))]
    return max(counts) if counts else None


def _report_age_seconds(path: Path, data: dict) -> float:
    """Report age from its own metadata.generated_at when present — mtimes lie
    after cp/archive restores; file mtime is only the fallback."""
    raw = str(((data or {}).get("metadata") or {}).get("generated_at") or "")
    try:
        generated = dt.datetime.fromisoformat(raw.replace(" ", "T"))
        return max(0.0, (dt.datetime.now() - generated).total_seconds())
    except ValueError:
        return time.time() - path.stat().st_mtime


def _short_conditions() -> tuple[bool, str]:
    """Plan step 6: shorts only under market pressure and with no fresh FTD.
    Missing/stale/low-quality evidence -> no shorts (fail-safe)."""
    path = _latest(MARKET_DIR, "market_top_*.json")
    max_age = MARKET_REPORT_MAX_AGE_DAYS * 86400
    data = (_read_json(path) or {}) if path else {}
    if not path or _report_age_seconds(path, data) > max_age:
        return (
            False,
            "нет свежего market_top отчёта (суббота, шаг 8) — шорт-скрин пропущен (fail-safe)",
        )
    # Don't arm shorts off a half-built report (e.g. WebSearch inputs missing).
    dq = (data.get("composite") or {}).get("data_quality") or {}
    avail, total = dq.get("available_count"), dq.get("total_components")
    if avail is not None and total and avail < total / 2:
        return (
            False,
            f"market_top собран лишь на {avail}/{total} компонентах — "
            "шорт-скрин пропущен (fail-safe)",
        )
    score = (data.get("composite") or {}).get("composite_score") or 0

    # DD count: prefer the IBD monitor (correct rally-invalidation rules);
    # market_top's invalidation-free count is systematically overstated.
    dd_source = "ibd"
    dd_count = None
    ibd_path = _latest(MARKET_DIR, "ibd_distribution_day_monitor_*.json")
    if ibd_path and time.time() - ibd_path.stat().st_mtime <= max_age:
        dd_count = _dd_count_from_ibd(ibd_path)
    if dd_count is None:
        dd = (data.get("components") or {}).get("distribution_days") or {}
        dd_count = dd.get("effective_count") or (dd.get("clustering") or {}).get("total_count") or 0
        dd_source = "market_top"

    # FTD: the dedicated detector wins whenever its report exists (regardless
    # of mtime ordering); market_top's break-on-detect flag is the fallback.
    verdict = _ftd_from_detector(_latest(MARKET_DIR, "ftd_detector_*.json"))
    if verdict is not None:
        ftd, ftd_note = verdict
    else:
        ftd = bool((data.get("follow_through_day") or {}).get("ftd_detected"))
        ftd_note = "market_top.follow_through_day"

    if not (score >= SHORT_TOP_RISK_MIN or dd_count >= SHORT_DD_MIN):
        return (
            False,
            f"давления нет (top-risk {score}, DD {dd_count} [{dd_source}]) — шорт-скрин не нужен",
        )
    if ftd:
        return False, f"свежий FTD — шортить запрещено (правило 6.4): {ftd_note}"
    return True, f"top-risk {score} / DD {dd_count} [{dd_source}], свежего FTD нет"


# Plan rule 6.4: shorts work on a faster clock than the long 15-t.d. time stop.
SHORT_TIME_STOP_TRADING_DAYS = 10
# Start warning this many trading days before the time stop fires.
TIME_STOP_WARN_AHEAD_DAYS = 2
# "Over 4 weeks in trade" threshold for the SMA50 trail rule (plan step 3).
SMA50_TRAIL_AFTER_TRADING_DAYS = 20


def _position_care_signals(args) -> list[dict]:
    """Structured step-3 management events (shared by the digest text + close cards).

    Each event carries ticker/side/shares/thesis_id/price + a digest ``text`` and,
    when actionable, a short ``reason`` and ``exit_reason`` ('time_stop'/'manual').
    Advisory-only events (time-stop approaching) have ``exit_reason=None``."""
    if args.dry_run:
        return []
    heat_path = _latest(JOURNAL_DIR, "portfolio_heat_*.json")
    heat = _read_json(heat_path) if heat_path else None
    positions = (heat or {}).get("positions") or []
    if not positions:
        return []
    profile = _read_json(TRADING_DATA_DIR / "trading_profile.json") or {}
    long_ts = int(profile.get("time_stop_trading_days") or 15)
    tickers = sorted({str(p.get("ticker", "")).upper() for p in positions} - {""})
    try:
        indicators = tsig.fetch_indicators(tickers)
    except tsig.QuotesError as exc:
        log(f"position care: индикаторы недоступны: {exc}", logging.WARNING)
        indicators = {}

    events: list[dict] = []
    for p in positions:
        ticker = str(p.get("ticker", "")).upper()
        if not ticker:
            continue
        side = str(p.get("side") or "long").lower()
        ind = indicators.get(ticker) or {}
        close, ema20, sma50 = ind.get("close"), ind.get("ema20"), ind.get("sma50")
        base = {
            "ticker": ticker,
            "side": side,
            "shares": p.get("shares"),
            "thesis_id": p.get("thesis_id"),
            "price": close or p.get("entry_price"),
        }
        limit = SHORT_TIME_STOP_TRADING_DAYS if side == "short" else long_ts
        days = None
        entry_date = str(p.get("entry_date") or "")[:10]
        if entry_date:
            days = _weekdays_since(entry_date)
        if days is not None:
            if days >= limit:
                events.append(
                    {
                        **base,
                        "text": f"⏱ {ticker}: {days} т.д. в позиции — тайм-стоп {limit} т.д. "
                        "НАСТУПИЛ: закрыть, если нет +1R",
                        "reason": f"тайм-стоп {limit} т.д. наступил",
                        "exit_reason": "time_stop",
                    }
                )
            elif days >= limit - TIME_STOP_WARN_AHEAD_DAYS:
                events.append(
                    {
                        **base,
                        "text": f"⏱ {ticker}: {days} т.д. в позиции — тайм-стоп {limit} т.д. "
                        f"через {limit - days} т.д.",
                        "reason": None,
                        "exit_reason": None,
                    }
                )
        if close and ema20:
            if side == "long" and close < ema20:
                events.append(
                    {
                        **base,
                        "text": f"📉 {ticker}: закрытие {close:g} ниже EMA20(≈21) {ema20:g} — "
                        "по плану выйти из остатка",
                        "reason": "закрытие ниже EMA20",
                        "exit_reason": "manual",
                    }
                )
            elif side == "short" and close > ema20:
                events.append(
                    {
                        **base,
                        "text": f"📈 {ticker}: закрытие {close:g} выше EMA20(≈21) {ema20:g} — "
                        "слабость шорта, рассмотреть выход",
                        "reason": "закрытие выше EMA20 (слабость шорта)",
                        "exit_reason": "manual",
                    }
                )
        if (
            side == "long"
            and days is not None
            and days >= SMA50_TRAIL_AFTER_TRADING_DAYS
            and close
            and sma50
            and close < sma50
        ):
            events.append(
                {
                    **base,
                    "text": f"📉 {ticker}: >4 недель в позиции и закрытие {close:g} ниже "
                    f"SMA50 {sma50:g} — трейл-выход",
                    "reason": "трейл-выход ниже SMA50",
                    "exit_reason": "manual",
                }
            )
    return events


def _position_care_warnings(args) -> list[str]:
    """Plan step-3 management rules as advisory digest lines (text only)."""
    return [e["text"] for e in _position_care_signals(args)]


def _send_close_cards(date_str: str, args) -> None:
    """Send a rule-violation exit confirmation card per position with an actionable
    exit (time-stop / close<EMA20 / SMA50 trail). On confirmation the listen daemon
    market-closes the position and cancels its protective legs. One card per thesis
    per day (producer ledger dedup); time_stop wins as the recorded exit reason."""
    if args.dry_run or args.no_telegram:
        return
    grouped: dict[str, dict] = {}
    for e in _position_care_signals(args):
        if not e.get("exit_reason"):
            continue
        tid = e.get("thesis_id")
        if not tid or not e.get("shares"):
            continue
        g = grouped.setdefault(tid, {"event": e, "reasons": [], "exit_reason": "manual"})
        if e.get("reason"):
            g["reasons"].append(e["reason"])
        if e["exit_reason"] == "time_stop":
            g["exit_reason"] = "time_stop"
    for g in grouped.values():
        e = g["event"]
        run_skill_script(
            [
                WATCHLIST_ORDERS_SCRIPT,
                "close-card",
                "--date",
                date_str,
                "--thesis-id",
                e["thesis_id"],
                "--ticker",
                e["ticker"],
                "--side",
                e["side"],
                "--shares",
                str(e["shares"]),
                "--price",
                str(e["price"]),
                "--reason",
                "; ".join(g["reasons"]) or "правило ведения",
                "--exit-reason",
                g["exit_reason"],
            ],
            label=f"close card {e['ticker']}",
            dry_run=args.dry_run,
            timeout=args.timeout,
        )


def _exit_price_from_trades(snapshot: dict, ticker: str, side: str) -> float | None:
    """Best-effort exit price for a closed position: the latest closing-side fill
    for ``ticker`` in the snapshot trades (SELL for a long, BUY for a short).
    None when no usable fill is present (caller falls back to the entry price)."""
    closing = "SELL" if side == "long" else "BUY"
    best_time, best_price = "", None
    for t in snapshot.get("trades") or []:
        if str(t.get("symbol", "")).upper() != ticker:
            continue
        if t.get("side") and t.get("side") != closing:
            continue
        price = t.get("price")
        if price is None:
            continue
        tt = str(t.get("trade_time") or "")
        if best_price is None or tt >= best_time:
            best_time, best_price = tt, price
    return best_price


def detect_external_closes(open_positions: list, snapshot: dict | None) -> list[dict]:
    """Tracked-open theses that are no longer present in the live IB snapshot.

    SAFETY: returns ``[]`` unless the snapshot is trustworthy — ``ok`` is True AND
    it carries account context (``account_ids``/``summary``). A failed, empty, or
    unauthenticated snapshot must never be read as "every position closed", which
    would fire spurious close cards. Even past this guard the result only drives a
    confirm-gated Telegram card, so a stale snapshot at worst costs a dismissal.

    Each returned dict carries ticker/side/shares/thesis_id + an approximate exit
    ``price`` (latest matching fill, else the entry price) for the card body."""
    if not open_positions:
        return []
    if not isinstance(snapshot, dict) or snapshot.get("ok") is not True:
        return []
    if not snapshot.get("account_ids") and not snapshot.get("summary"):
        return []
    held = {
        str(p.get("symbol", "")).upper()
        for p in snapshot.get("positions") or []
        if p.get("position")  # 0 / None == flat -> not held
    } - {""}
    out: list[dict] = []
    for p in open_positions:
        ticker = str(p.get("ticker", "")).upper()
        thesis_id = p.get("thesis_id")
        if not ticker or not thesis_id or ticker in held:
            continue
        side = str(p.get("side") or "long").lower()
        price = _exit_price_from_trades(snapshot, ticker, side)
        if price is None:
            price = p.get("entry_price")
        out.append(
            {
                "thesis_id": thesis_id,
                "ticker": ticker,
                "side": side,
                "shares": p.get("shares"),
                "price": price,
            }
        )
    return out


def _load_ib_snapshot(args) -> dict | None:
    """Fetch the read-only IB snapshot as a dict (None on launch/parse failure).

    Runs ``fetch_ib_snapshot.py`` and parses its stdout JSON. The script prints an
    ``ok:false`` snapshot (exit code 2) when the Gateway is down/unauthenticated;
    we still parse it so :func:`detect_external_closes` applies the trust guard."""
    cmd = [sys.executable, str(IB_SNAPSHOT_SCRIPT)]
    fixture = getattr(args, "ib_fixture", None)
    if fixture:
        cmd += ["--fixture", str(fixture)]
    try:
        res = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError) as exc:
        log(f"reconcile: IB snapshot launch error: {exc}", logging.WARNING)
        return None
    try:
        data = json.loads(res.stdout)
    except (json.JSONDecodeError, ValueError):
        log(f"reconcile: IB snapshot stdout not JSON (rc={res.returncode})", logging.WARNING)
        return None
    return data if isinstance(data, dict) else None


def _reconcile_ib_closes(date_str: str, args) -> None:
    """Safety-net (variant B): tracked-open theses that vanished from the live IB
    snapshot were likely closed OUTSIDE the system. Send one confirm-to-record
    card per missing thesis. No order is placed; confirming records the close +
    postmortem. Deduped per day by the watchlist_orders ledger, and suppressed
    when the same thesis was already closed through the system today."""
    if args.dry_run or args.no_telegram:
        return
    heat_path = _latest(JOURNAL_DIR, "portfolio_heat_*.json")
    heat = _read_json(heat_path) if heat_path else None
    positions = (heat or {}).get("positions") or []
    if not positions:
        return
    closes = detect_external_closes(positions, _load_ib_snapshot(args))
    if not closes:
        return
    for c in closes:
        cmd = [
            WATCHLIST_ORDERS_SCRIPT,
            "close-detected-card",
            "--date",
            date_str,
            "--thesis-id",
            c["thesis_id"],
            "--ticker",
            c["ticker"],
            "--side",
            c["side"],
        ]
        if c.get("shares") is not None:
            cmd += ["--shares", str(c["shares"])]
        if c.get("price") is not None:
            cmd += ["--price", str(c["price"])]
        run_skill_script(
            cmd,
            label=f"detected-close card {c['ticker']}",
            dry_run=args.dry_run,
            timeout=args.timeout,
        )


def _care_block(args) -> str:
    care = _position_care_warnings(args)
    return ("\n\n🛎 ОТКРЫТЫЕ ПОЗИЦИИ\n" + "\n".join(care)) if care else ""


def _filter_shorts_on_earnings(shorts: list, args) -> tuple[list, str]:
    """Plan rule 6.4: never hold a short through earnings — drop candidates
    reporting within the profile earnings gate (default 10 trading days, the
    short time-stop horizon). Fails open with an explicit warning note when the
    scanner is unreachable; the trader then checks the dates manually."""
    if not shorts or args.dry_run:
        return shorts, ""
    profile = _read_json(TRADING_DATA_DIR / "trading_profile.json") or {}
    gate_days = int(profile.get("earnings_gate_days") or 10)
    symbols = [str(s.get("symbol", "")).upper() for s in shorts if s.get("symbol")]
    try:
        quotes = tsig.fetch_quotes(symbols)
    except tsig.QuotesError as exc:
        log(f"short earnings gate: даты отчётов недоступны: {exc}", logging.WARNING)
        return shorts, (
            "⚠️ Даты отчётов недоступны — проверь earnings вручную "
            "(шорт через отчёт запрещён, правило 6.4)."
        )
    today = dt.date.today()
    kept, dropped = [], []
    for s in shorts:
        sym = str(s.get("symbol", "")).upper()
        ed = (quotes.get(sym) or {}).get("earnings_date")
        days = None
        if ed:
            try:
                days = tsig.weekdays_until(ed, today)
            except ValueError:
                days = None
        if days is not None and days <= gate_days:
            dropped.append(f"{sym} ({ed}, {days} т.д.)")
        else:
            kept.append(s)
    if dropped:
        log("short earnings gate: исключены перед отчётом: " + ", ".join(dropped))
        return kept, "📅 Исключены перед отчётом (правило 6.4): " + ", ".join(dropped)
    return kept, ""


def _evening_short_branch(
    date_str: str,
    dec: dict,
    args,
    *,
    regime_ok: bool,
    terminated_offside: list[str] | None = None,
) -> int:
    rc = 0 if regime_ok or args.dry_run else 1
    offside_note = _offside_note(terminated_offside)
    active, reason = _short_conditions()
    if not active:
        msg = build_evening_closed_msg(date_str, dec, extra=f"Шорт-ветка: {reason}.")
        if offside_note:
            msg += f"\n\n{offside_note}"
        msg += _care_block(args)
        notify(msg, dry_run=args.dry_run, no_telegram=args.no_telegram)
        return rc

    # The short screen reads LIVE chart data (cache off) and the alerts go
    # through TradingView — without TV Desktop the branch cannot run.
    if not args.dry_run and not tv_available():
        notify(
            _tv_down_text(
                "Вечерний ШОРТ-скрин и алерты невозможны: данные и алерты идут через TradingView."
            ),
            dry_run=args.dry_run,
            no_telegram=args.no_telegram,
        )
        return 1

    # Fresh heat snapshot (mirrors the long branch): tomorrow's intraday
    # monitor needs today's open positions and the capacity budget.
    run_skill_script(
        [TRADER_MEMORY_CLI, "heat"],
        label="portfolio-heat (short branch)",
        dry_run=args.dry_run,
        timeout=args.timeout,
        output_glob=(JOURNAL_DIR, "portfolio_heat_*.json"),
    )

    short_cmd = [SHORT_SCREEN_SCRIPT, "--min-grade", "B", "--top", "10"]
    universe = _read_vcp_universe()
    short_label = "swing-short-screener (grade B+)"
    if universe:
        # Same expanded liquid NASDAQ+NYSE universe the long VCP branch screens
        # (scripts/lib/data/vcp_universe.txt). --universe overrides the S&P 500
        # AND bypasses the screener's --max-candidates cap (that cap only applies
        # to the default S&P 500 path), so every name gets full weakness analysis.
        short_cmd += ["--universe", *universe]
        short_label = f"swing-short-screener (grade B+, universe={len(universe)})"
    else:
        # Fallback: full S&P 500. Without --full-sp500 the screener caps the
        # universe at --max-candidates 100, i.e. the first ~100 names alphabetically.
        short_cmd.append("--full-sp500")
    short_path = run_skill_script(
        short_cmd,
        label=short_label,
        dry_run=args.dry_run,
        timeout=args.timeout,
        output_glob=(SCREENERS_DIR, "swing_short_screener_*.json"),
    )
    shorts = ((_read_json(short_path) or {}).get("candidates") or []) if short_path else []
    shorts, earnings_note = _filter_shorts_on_earnings(shorts, args)
    val_candidates = [
        {
            "ticker": s.get("symbol"),
            "side": "short",
            "pivot": (s.get("trade_levels") or {}).get("entry"),
            "stop": (s.get("trade_levels") or {}).get("stop"),
        }
        for s in shorts[:VALIDATION_TOP_N]
    ]
    validation = _run_chart_validation(date_str, val_candidates, args)
    profile = _read_json(TRADING_DATA_DIR / "trading_profile.json") or {}
    wl = tsig.build_watchlist(
        date_str,
        dec["decision"],
        None,
        shorts,
        validation,
        account_size=profile.get("account_size"),
        notes=reason,
        source_plan=_rel(short_path) if short_path else None,
    )
    wl_path = _write_watchlist(wl, date_str, args)
    if short_path and not args.dry_run:
        _ingest_theses("swing-short-screener", short_path, wl, wl_path, date_str, args)
    # Chart-validation levels are authoritative for the short watchlist too
    # (no deep ticker-analysis on the short branch — detection-only).
    wl = _apply_validation_levels(wl, wl_path, validation, args)
    msg = build_evening_short_msg(date_str, dec, _rel(wl_path), wl.get("candidates") or [], reason)
    if earnings_note:
        msg += f"\n\n{earnings_note}"
    val_note = _validation_note(wl, validation)
    if val_note:
        msg += f"\n\n🔎 Валидация: {val_note}"
    if offside_note:
        msg += f"\n\n{offside_note}"
    msg += _care_block(args)
    alert_line = _sync_tv_alerts(wl, args)
    if alert_line:
        msg += f"\n\n{alert_line}"
    notify(
        msg,
        file=str(wl_path) if wl_path.exists() else None,
        dry_run=args.dry_run,
        no_telegram=args.no_telegram,
    )
    return rc


def slot_evening_prep(date_str: str, args) -> int:
    gate = decision_path(date_str)
    ok, dec = run_regime_gate(
        date_str, gate, quick=False, label="market-regime-daily (evening EOD)", args=args
    )
    log(
        f"exposure decision: {dec['decision'].upper()}"
        + (" (degraded/fail-safe)" if dec.get("degraded") else ""),
        logging.WARNING if dec.get("degraded") else logging.INFO,
    )

    # Regime-flip hygiene: drop pending (non-position) theses of the now-wrong
    # side before screening/ingesting this evening's same-side candidates.
    terminated_offside = _terminate_offside_theses(dec, args)

    # Шаг 3: rule-violation exit cards on the fresh daily close (gate-independent;
    # runs for both branches, deduped per day with the premarket cards).
    _send_close_cards(date_str, args)
    # Safety-net: positions closed outside the system during the session.
    _reconcile_ib_closes(date_str, args)

    if dec["decision"] != "allow":
        return _evening_short_branch(
            date_str, dec, args, regime_ok=ok, terminated_offside=terminated_offside
        )

    # The long pipeline reads LIVE chart data (cache off) and ends with TV
    # alert sync — both need TradingView Desktop. Notify IMMEDIATELY when down.
    if not args.dry_run and not tv_available():
        notify(
            _tv_down_text(
                "Вечерний скрин невозможен: скринеры читают живой график (кэш отключён), алерты не выставить."
            ),
            dry_run=args.dry_run,
            no_telegram=args.no_telegram,
        )
        return 1

    # Gate is allow -> deterministic long pipeline + hybrid chart validation
    wl_path, wl, val_note = _evening_long_branch(date_str, args)
    candidates = wl.get("candidates") or []
    wl_rel = _rel(wl_path) if wl_path.exists() else "(файл не создан)"
    msg = build_evening_allow_msg(date_str, dec, wl_rel, candidates)
    if val_note:
        msg += f"\n\n🔎 Валидация: {val_note}"
    offside_note = _offside_note(terminated_offside)
    if offside_note:
        msg += f"\n\n{offside_note}"
    msg += _care_block(args)
    alert_line = _sync_tv_alerts(wl, args)
    if alert_line:
        msg += f"\n\n{alert_line}"
    notify(
        msg,
        file=str(wl_path) if wl_path.exists() else None,
        dry_run=args.dry_run,
        no_telegram=args.no_telegram,
    )
    return 0 if ok or args.dry_run else 1


def slot_intraday(date_str: str, args) -> int:
    """Cheap quote check (no claude): OPEN triggers from the watchlist and
    manage-signals (stop / near-stop / +2R) for open positions, via Telegram."""
    if not args.force:
        try:
            _wd = dt.date.fromisoformat(date_str)
        except ValueError:
            _wd = dt.date.today()
        start_t, end_t = intraday_window_local(_wd)
        now_t = _now_time()
        if not (start_t <= now_t < end_t):
            log(
                f"intraday: {now_t:%H:%M} вне окна "
                f"{start_t:%H:%M}–{end_t:%H:%M} (US-сессия, локальное время) — пропуск"
            )
            return 0

    # Safety-net: positions closed outside the system (IB diff) -> confirm cards.
    _reconcile_ib_closes(date_str, args)

    wl_path = latest_watchlist()
    wl = _read_json(wl_path) if wl_path else None
    try:
        _today = dt.date.fromisoformat(date_str)
    except ValueError:
        _today = dt.date.today()
    if wl is not None and not _watchlist_is_fresh(wl, _today):
        log(
            f"intraday: watchlist {wl.get('date')!r} устарел (старше прошлого торгового "
            "дня) — OPEN-сигналы не армируются, мониторим только открытые позиции",
            logging.WARNING,
        )
        wl = None
    heat_path = _latest(JOURNAL_DIR, "portfolio_heat_*.json")
    heat = _read_json(heat_path) if heat_path else None
    dec = read_decision(decision_path(date_str))

    tickers = sorted(
        (
            {str(c.get("ticker", "")).upper() for c in (wl or {}).get("candidates") or []}
            | {str(p.get("ticker", "")).upper() for p in (heat or {}).get("positions") or []}
        )
        - {""}
    )
    if not tickers:
        log("intraday: нет тикеров для мониторинга (watchlist пуст, открытых позиций нет)")
        return 0
    if args.dry_run:
        log(
            f"(dry-run) intraday: запросил бы котировки для {', '.join(tickers)} "
            f"(гейт {dec['decision']}); сигналы не оцениваются"
        )
        return 0

    state = tsig.load_signals_state(SIGNALS_STATE_FILE, date_str)
    try:
        quotes = tsig.fetch_quotes(tickers)
    except tsig.QuotesError as exc:
        log(f"intraday: не удалось получить котировки: {exc}", logging.ERROR)
        return 1

    signals = tsig.evaluate_signals(
        wl,
        heat,
        quotes,
        dec["decision"],
        set(state.get("sent") or {}),
        armed_tickers=armed_order_tickers(date_str),
    )
    if not signals:
        log(f"intraday: {len(tickers)} тикеров проверено — новых сигналов нет")
        return 0

    # +2R signals also get an actionable Telegram confirmation card (sell 50% +
    # stop->breakeven on tap), handled by the listen daemon. The one-way digest
    # still lists them for context.
    _send_scale_cards(date_str, signals, args)

    # Entries that ran away (MISSED) are no longer actionable -> drop their
    # [WL] TradingView alerts right now; warn when TV is down.
    msg = build_intraday_msg(date_str, signals)
    missed = sorted({s["ticker"] for s in signals if s["type"] == tsig.MISSED})
    if missed:
        if tv_available():
            res = talerts.purge_watchlist_alerts(
                missed, ALERTS_STATE_FILE, project_root=PROJECT_ROOT
            )
            line = f"⏰ Сняты алерты TV: {', '.join(missed)} (−{res['deleted']})"
            if res["errors"]:
                line += f", ошибок {res['errors']}"
                log("alert purge errors: " + "; ".join(res["error_details"]), logging.WARNING)
            msg += f"\n\n{line}"
        else:
            msg += f"\n\n❗️ TradingView недоступен — сними алерты по {', '.join(missed)} вручную"

    notify(
        msg,
        dry_run=args.dry_run,
        no_telegram=args.no_telegram,
    )
    tsig.mark_sent(
        state, [s["key"] for s in signals], dt.datetime.now().isoformat(timespec="seconds")
    )
    tsig.save_signals_state(SIGNALS_STATE_FILE, state)
    return 0


def slot_weekly(date_str: str, args) -> int:
    """Saturday weekly block (plan step 8): deterministic background reports,
    then claude for the two manual market-top inputs + synthesis."""
    # The deterministic scripts read LIVE chart data (cache off) — without
    # TradingView Desktop they cannot run. Notify IMMEDIATELY and skip them.
    tv_ok = args.dry_run or tv_available()
    if not tv_ok:
        notify(
            _tv_down_text("Недельный блок: DD/macro/FTD скрипты читают живой график и пропущены."),
            dry_run=args.dry_run,
            no_telegram=args.no_telegram,
        )

    det: dict[str, bool] = {}
    steps = [
        (
            "ibd-distribution-days",
            [IBD_SCRIPT, "--symbols", "QQQ,SPY"],
            "ibd_distribution_day_monitor_*.json",
        ),
        ("macro-regime", [MACRO_SCRIPT], "macro_regime_*.json"),
        ("ftd-detector", [FTD_SCRIPT], "ftd_detector_*.json"),
    ]
    for label, cmd, pattern in steps:
        det[label] = tv_ok and (
            run_skill_script(
                cmd,
                label=label,
                dry_run=args.dry_run,
                timeout=args.timeout,
                output_glob=(MARKET_DIR, pattern),
            )
            is not None
        )

    summary = SCHEDULE_DIR / f"weekly_review_{date_str}.json"
    ok = run_claude(
        weekly_prompt(date_str, summary),
        label="weekly market-top + synthesis",
        dry_run=args.dry_run,
        timeout=args.timeout,
        expected_output=summary,
    )
    data = _read_json(summary)
    notify(
        build_weekly_msg(date_str, data, det),
        file=str(summary) if summary.exists() else None,
        dry_run=args.dry_run,
        no_telegram=args.no_telegram,
    )
    return 0 if ok or args.dry_run else 1


def slot_monthly(date_str: str, args) -> int:
    summary = SCHEDULE_DIR / f"monthly_review_{date_str}.json"
    ok = run_claude(
        monthly_prompt(date_str, summary),
        label="monthly-performance-review",
        dry_run=args.dry_run,
        timeout=args.timeout,
        expected_output=summary,
    )
    data = None
    if summary.exists():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = None
    msg = build_monthly_msg(date_str, data)
    notify(
        msg,
        file=str(summary) if summary.exists() else None,
        dry_run=args.dry_run,
        no_telegram=args.no_telegram,
    )
    return 0 if ok or args.dry_run else 1


SLOTS = {
    "premarket": slot_premarket,
    "evening-prep": slot_evening_prep,
    "intraday": slot_intraday,
    "weekly": slot_weekly,
    "monthly": slot_monthly,
}


# --------------------------------------------------------------------------- #
# Single-run lock (PID file; mirrors run_trading_autopilot's lock helpers so the
# two scripts stay decoupled). The autopilot holds its own autopilot.lock and
# spawns this script as a child, so a *separate* lock file here is deliberate:
# it serialises concurrent schedule runs (manual + manual, or manual + the
# autopilot's child) without the parent/child deadlock a shared file would cause.
# --------------------------------------------------------------------------- #
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, OSError):
        return True  # exists but not ours / unkillable -> treat as alive
    return True


def _lock_holder(path: Path) -> str:
    try:
        return Path(path).read_text().strip() or "?"
    except OSError:
        return "?"


def acquire_lock(path: Path) -> bool:
    """Atomically claim the lock. False if a live process already holds it."""
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
            path.unlink()  # stale lock from a dead process
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
# Main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ensure_venv_interpreter()  # self-heal cron/manual runs under a bare python
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--slot", required=True, choices=sorted(SLOTS), help="Which scheduled slot is firing."
    )
    p.add_argument("--date", help="Override run date (YYYY-MM-DD). Default: today (local).")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not call claude or Telegram; print prompts and intended messages.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Ignore the trading-day / first-Sunday gate and run anyway.",
    )
    p.add_argument("--no-telegram", action="store_true", help="Skip Telegram notifications.")
    p.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("TRADING_SCHEDULE_TIMEOUT", "1800")),
        help="Per-workflow claude timeout in seconds (default 1800).",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose (DEBUG-level) logging.")
    p.add_argument(
        "--ib-fixture",
        default=None,
        help="Read the IB snapshot from a fixture JSON instead of the live Gateway "
        "(offline testing of the external-close reconcile).",
    )
    args = p.parse_args(argv)

    setup_logging(verbose=args.verbose)
    load_env_file()
    run_started = time.monotonic()

    if args.date:
        try:
            today = dt.date.fromisoformat(args.date)
        except ValueError:
            log(f"bad --date {args.date!r}, expected YYYY-MM-DD", logging.ERROR)
            return 2
    else:
        today = dt.date.today()
    date_str = today.isoformat()

    SCHEDULE_DIR.mkdir(parents=True, exist_ok=True)
    log(
        f"===== RUN START pid={os.getpid()} slot={args.slot} date={date_str} "
        f"dry_run={args.dry_run} force={args.force} ====="
    )

    def _finish(rc: int) -> int:
        log(
            f"===== RUN END slot={args.slot} rc={rc} "
            f"elapsed={time.monotonic() - run_started:.1f}s ====="
        )
        return rc

    # Calendar gates
    if (
        args.slot in ("premarket", "evening-prep", "intraday")
        and not args.force
        and not is_us_trading_day(today)
    ):
        log(f"{date_str} is not a US trading day -- skipping slot {args.slot}.")
        return _finish(0)
    if args.slot == "weekly" and not args.force and today.weekday() != 5:
        log(f"{date_str} is not a Saturday -- skipping weekly block.")
        return _finish(0)
    if args.slot == "monthly" and not args.force and not is_first_sunday(today):
        log(f"{date_str} is not the first Sunday of the month -- skipping monthly review.")
        return _finish(0)

    # Serialise real runs: never let two schedule processes drive the single
    # TradingView chart / shared state at once. Dry-runs touch neither, so they
    # skip the lock (and stay test-friendly).
    if not args.dry_run and not acquire_lock(LOCK_FILE):
        log(
            f"another run_trading_schedule is active (pid {_lock_holder(LOCK_FILE)}) -- "
            f"exiting to avoid racing the TradingView chart",
            logging.WARNING,
        )
        return _finish(EXIT_BUSY)
    try:
        return _finish(SLOTS[args.slot](date_str, args))
    except Exception as exc:  # never leave launchd without a breadcrumb
        log(f"slot {args.slot} crashed: {exc!r}", logging.ERROR)
        notify(
            f"❌ trading-schedule slot `{args.slot}` упал {date_str}: {exc!r}",
            dry_run=args.dry_run,
            no_telegram=args.no_telegram,
        )
        return _finish(1)
    finally:
        if not args.dry_run:
            release_lock(LOCK_FILE)


if __name__ == "__main__":
    sys.exit(main())
