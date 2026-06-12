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
                            heat -> breakout-trade-planner -> claude chart
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
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
WORKFLOWS_DIR = PROJECT_ROOT / "workflows"
TELEGRAM_SCRIPT = PROJECT_ROOT / "skills" / "send-telegram" / "scripts" / "send_telegram.py"

VALID_DECISIONS = ("allow", "restrict", "cash-priority")

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
SHORT_SCREEN_SCRIPT = SKILLS_DIR / "swing-short-screener" / "scripts" / "screen_short.py"
PLANNER_SCRIPT = SKILLS_DIR / "breakout-trade-planner" / "scripts" / "plan_breakout_trades.py"
TRADER_MEMORY_CLI = SKILLS_DIR / "trader-memory-core" / "scripts" / "trader_memory_cli.py"
IBD_SCRIPT = SKILLS_DIR / "ibd-distribution-day-monitor" / "scripts" / "ibd_monitor.py"
MACRO_SCRIPT = SKILLS_DIR / "macro-regime-detector" / "scripts" / "macro_regime_detector.py"
FTD_SCRIPT = SKILLS_DIR / "ftd-detector" / "scripts" / "ftd_detector.py"

# Intraday monitoring window (CET wall clock; US session 15:30-22:00).
INTRADAY_START = dt.time(15, 30)
INTRADAY_END = dt.time(22, 0)
# How many top candidates get the claude chart-validation pass (hybrid mode).
VALIDATION_TOP_N = 3
# Short branch market-pressure gates (trading plan step 6): top-risk score OR
# distribution-day count, and no fresh confirmed FTD.
SHORT_TOP_RISK_MIN = 41.0
SHORT_DD_MIN = 3
# market_top / ftd reports older than this are treated as absent (fail-safe).
MARKET_REPORT_MAX_AGE_DAYS = 7


def _rel(p: Path) -> str:
    """Repo-relative path string for prompts/messages (absolute if outside)."""
    try:
        return str(p.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(p)


# Probed in order when ``claude`` is not on PATH (cron PATH is /usr/bin:/bin).
CLAUDE_FALLBACK_PATHS = [
    Path.home() / ".local" / "bin" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
]


def resolve_claude_bin() -> str:
    """Locate the claude CLI: $CLAUDE_BIN > PATH > known install dirs."""
    override = os.environ.get("CLAUDE_BIN")
    if override:
        return override
    if shutil.which("claude"):
        return "claude"
    for candidate in CLAUDE_FALLBACK_PATHS:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "claude"  # let run_claude surface the launch error


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
def run_claude(prompt: str, *, label: str, dry_run: bool, timeout: int) -> bool:
    """Run one workflow headlessly via ``claude -p``. Returns success bool.

    Configurable via env:
      CLAUDE_BIN                        path to the claude CLI (default: PATH,
                                        then known install dirs)
      TRADING_SCHEDULE_PERMISSION_MODE  --permission-mode value (default bypassPermissions)
      TRADING_SCHEDULE_CLAUDE_FLAGS     extra space-separated flags appended verbatim
    """
    claude_bin = resolve_claude_bin()
    perm_mode = os.environ.get("TRADING_SCHEDULE_PERMISSION_MODE", "bypassPermissions")
    cmd = [claude_bin, "-p", prompt, "--permission-mode", perm_mode, "--output-format", "text"]
    extra = os.environ.get("TRADING_SCHEDULE_CLAUDE_FLAGS", "").split()
    cmd += extra

    log(f"--- claude workflow START: {label} (timeout={timeout}s, perm={perm_mode}) ---")
    log(f"command: {claude_bin} -p <prompt {len(prompt)} chars> {' '.join(cmd[3:])}", logging.DEBUG)
    if dry_run:
        log("(dry-run) prompt that would be sent to claude -p:\n" + prompt)
        return True

    started = time.monotonic()
    try:
        res = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout)
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
    if res.returncode != 0:
        log(
            f"{label}: FAILED rc={res.returncode} in {elapsed:.0f}s\n{res.stderr[-1000:]}",
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
        "rationale": "exposure_decision gate file missing or unreadable; "
        "defaulting to restrict (fail-safe -- no new risk).",
        "degraded": True,
    }
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        fallback["rationale"] = f"could not parse {path.name} ({exc}); fail-safe restrict."
        return fallback
    decision = str(data.get("decision", "")).strip().lower()
    if decision not in VALID_DECISIONS:
        fallback["rationale"] = (
            f"exposure_decision='{data.get('decision')}' not one of {VALID_DECISIONS}; "
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
  "rationale": "<= 2 sentences",
  "key_signals": ["short bullet", "..."]
}}
`decision` MUST be exactly one of allow / restrict / cash-priority -- the
exposure-coach posture. Write the file even on partial data; choose the most
defensive posture the evidence supports. Do NOT place any trades.

EXECUTION RULES (unattended headless run -- you must finish autonomously):
- Perform EVERY step with tool calls. Never end your reply with prose that only
  announces the next step (e.g. "running uptrend-analyzer next") -- run it
  immediately instead. A message with no tool call ENDS the session.
- Writing the gate file at {gate_path} is MANDATORY and must be your FINAL action.
  Do not stop, summarise, or hand back control until that file exists on disk.
- After writing it, Read it back to confirm it parses as valid JSON, then give a
  1-2 line summary. "Be fast" / short narration means terse prose, NOT skipping
  steps or the gate file."""


def opportunity_prompt(date_str: str, gate_path: Path, watchlist_path: Path) -> str:
    return f"""Run the scheduled `swing-opportunity-daily` workflow for {date_str} (CET schedule, US equities).

The market-regime-daily gate at {gate_path} is `allow`, so new swing risk is
permitted. Read the manifest at workflows/swing-opportunity-daily.yaml and
execute its steps: run the screeners (vcp-screener, optionally canslim-screener
/ theme-detector), validate setups with technical-analyst, size with
position-sizer, and register each surviving idea as an IDEA thesis via
trader-memory-core. Save artifacts into the trading data layout:
screener output under {_rel(SCREENERS_DIR)}/, position sizing under {_rel(PLANS_DIR)}/,
thesis state dir {_rel(JOURNAL_DIR)}/theses.

Also write a concise watchlist gate file to EXACTLY this path:
  {watchlist_path}
as JSON:
{{
  "workflow": "swing-opportunity-daily",
  "date": "{date_str}",
  "exposure_decision": "allow",
  "candidates": [
    {{"ticker": "XXX", "setup": "VCP/...", "pivot": <num>, "stop": <num>,
      "target": <num>, "shares": <num>, "risk_R_pct": <num>}}
  ],
  "notes": "<= 2 sentences"
}}
If no candidates pass the gates, write an empty `candidates` list and say so.
This is PLANNING only -- do NOT place any orders. Keep narration short."""


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
        f"- {c.get('ticker')} ({c.get('side')}): вход {c.get('pivot')}, стоп {c.get('stop')}"
        for c in candidates
    )
    return f"""Hybrid auto-mode chart validation for {date_str} (US equities, swing horizon).

For EACH candidate below, apply the technical-analyst skill on the daily AND
weekly chart (TradingView Desktop MCP if running, otherwise the skill's data
layer). Judge ONLY structural integrity: base intact, not climactic or
over-extended, support not broken, volume consistent with the setup. Reject
ONLY on clear structural damage — when in doubt, pass.

Candidates:
{lines}

Write EXACTLY this JSON file to: {validation_path}
{{
  "date": "{date_str}",
  "verdicts": [
    {{"ticker": "XXX", "verdict": "pass" | "reject", "note": "<= 1 sentence"}}
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


def latest_watchlist() -> Path | None:
    files = sorted(SCHEDULE_DIR.glob("watchlist_*.json"))
    return files[-1] if files else None


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
    wl_line = f"Watchlist: {wl.relative_to(PROJECT_ROOT)}" if wl else "Watchlist не найден."
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
        tail = (
            "   Bracket-ордер (вход + стоп + тейк); после исполнения записать вход в журнал (шаг 3)."
            if is_long
            else "   Sell-short bracket; правила: риск 1%, тайм-стоп 10 т.д., не держать через отчёт."
        )
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
  "rationale": "<= 2 sentences",
  "key_signals": ["short bullet", "..."]
}}
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
        )
        ok = ok or ok2

    if args.dry_run or ok:
        return ok, read_decision(gate)
    return ok, {
        "decision": "restrict",
        "rationale": "regime workflow did not complete; fail-safe.",
        "degraded": True,
    }


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

    msg = build_premarket_msg(date_str, dec, latest_watchlist())
    # Surface a missing TradingView Desktop IMMEDIATELY: without it the heat
    # refresh above and tonight's screen/alert sync will not work.
    if not args.dry_run and not tv_available():
        msg = (
            _tv_down_text(
                "Premarket: heat-обновление и вечерний скрин/алерты без TradingView не сработают."
            )
            + "\n\n"
            + msg
        )
    notify(msg, dry_run=args.dry_run, no_telegram=args.no_telegram)
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
    if res["errors"]:
        line += f", ошибок {res['errors']}"
        log("alert sync errors: " + "; ".join(res["error_details"]), logging.WARNING)
    log(line)
    return line


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


def _write_watchlist(wl: dict, date_str: str, args) -> Path:
    wl_path = SCHEDULE_DIR / f"watchlist_{date_str}.json"
    if not args.dry_run:
        SCHEDULE_DIR.mkdir(parents=True, exist_ok=True)
        wl_path.write_text(json.dumps(wl, indent=2, ensure_ascii=False), encoding="utf-8")
    return wl_path


def _evening_long_branch(date_str: str, args) -> tuple[Path, dict, str]:
    """Deterministic screen -> plan -> hybrid validation -> watchlist + theses."""
    vcp = run_skill_script(
        [VCP_SCREEN_SCRIPT, "--top", "10"],
        label="vcp-screener (top 10)",
        dry_run=args.dry_run,
        timeout=args.timeout,
        output_glob=(SCREENERS_DIR, "vcp_screener_*.json"),
    )
    heat = run_skill_script(
        [TRADER_MEMORY_CLI, "heat"],
        label="portfolio-heat",
        dry_run=args.dry_run,
        timeout=args.timeout,
        output_glob=(JOURNAL_DIR, "portfolio_heat_*.json"),
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
        run_skill_script(
            [TRADER_MEMORY_CLI, "ingest", "--source", "vcp-screener", "--input", vcp],
            label="thesis-ingest (trader-memory-core)",
            dry_run=args.dry_run,
            timeout=args.timeout,
        )
    return wl_path, wl, _validation_note(wl, validation)


def _any_ftd_detected(node) -> bool:
    """Recursive search for a truthy ftd_detected anywhere in a report JSON."""
    if isinstance(node, dict):
        if node.get("ftd_detected"):
            return True
        return any(_any_ftd_detected(v) for v in node.values())
    if isinstance(node, list):
        return any(_any_ftd_detected(v) for v in node)
    return False


def _short_conditions() -> tuple[bool, str]:
    """Plan step 6: shorts only under market pressure and with no fresh FTD.
    Missing/stale evidence -> no shorts (fail-safe)."""
    path = _latest(MARKET_DIR, "market_top_*.json")
    max_age = MARKET_REPORT_MAX_AGE_DAYS * 86400
    if not path or time.time() - path.stat().st_mtime > max_age:
        return (
            False,
            "нет свежего market_top отчёта (суббота, шаг 8) — шорт-скрин пропущен (fail-safe)",
        )
    data = _read_json(path) or {}
    score = (data.get("composite") or {}).get("composite_score") or 0
    dd = (data.get("components") or {}).get("distribution_days") or {}
    dd_count = dd.get("effective_count") or (dd.get("clustering") or {}).get("total_count") or 0
    ftd = bool((data.get("follow_through_day") or {}).get("ftd_detected"))
    ftd_path = _latest(MARKET_DIR, "ftd_detector_*.json")
    if ftd_path and ftd_path.stat().st_mtime > path.stat().st_mtime:
        ftd = _any_ftd_detected(_read_json(ftd_path) or {})

    if not (score >= SHORT_TOP_RISK_MIN or dd_count >= SHORT_DD_MIN):
        return False, f"давления нет (top-risk {score}, DD {dd_count}) — шорт-скрин не нужен"
    if ftd:
        return False, "подтверждён свежий FTD — шортить запрещено (правило 6.4)"
    return True, f"top-risk {score} / DD {dd_count}, свежего FTD нет"


def _evening_short_branch(date_str: str, dec: dict, args, *, regime_ok: bool) -> int:
    rc = 0 if regime_ok or args.dry_run else 1
    active, reason = _short_conditions()
    if not active:
        notify(
            build_evening_closed_msg(date_str, dec, extra=f"Шорт-ветка: {reason}."),
            dry_run=args.dry_run,
            no_telegram=args.no_telegram,
        )
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

    short_path = run_skill_script(
        [SHORT_SCREEN_SCRIPT, "--min-grade", "B", "--top", "10"],
        label="swing-short-screener (grade B+)",
        dry_run=args.dry_run,
        timeout=args.timeout,
        output_glob=(SCREENERS_DIR, "swing_short_screener_*.json"),
    )
    shorts = ((_read_json(short_path) or {}).get("candidates") or []) if short_path else []
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
    msg = build_evening_short_msg(date_str, dec, _rel(wl_path), wl.get("candidates") or [], reason)
    val_note = _validation_note(wl, validation)
    if val_note:
        msg += f"\n\n🔎 Валидация: {val_note}"
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

    if dec["decision"] != "allow":
        return _evening_short_branch(date_str, dec, args, regime_ok=ok)

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
        now_t = _now_time()
        if not (INTRADAY_START <= now_t < INTRADAY_END):
            log(
                f"intraday: {now_t:%H:%M} вне окна "
                f"{INTRADAY_START:%H:%M}–{INTRADAY_END:%H:%M} CET — пропуск"
            )
            return 0

    wl_path = latest_watchlist()
    wl = _read_json(wl_path) if wl_path else None
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

    signals = tsig.evaluate_signals(wl, heat, quotes, dec["decision"], set(state.get("sent") or {}))
    if not signals:
        log(f"intraday: {len(tickers)} тикеров проверено — новых сигналов нет")
        return 0

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
