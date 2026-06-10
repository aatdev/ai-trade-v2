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
  evening-prep  ~22:15      Post-US-close. Full regime on fresh EOD data, and
                            (only if allow) the swing-opportunity screen to build
                            tomorrow's watchlist.
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
SCHEDULE_DIR = PROJECT_ROOT / "reports" / "schedule"
LOG_FILE = PROJECT_ROOT / "logs" / "trading_schedule.log"
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
Save the usual report artifacts under reports/.

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
defensive posture the evidence supports. Do NOT place any trades. Keep narration short."""


def opportunity_prompt(date_str: str, gate_path: Path, watchlist_path: Path) -> str:
    return f"""Run the scheduled `swing-opportunity-daily` workflow for {date_str} (CET schedule, US equities).

The market-regime-daily gate at {gate_path} is `allow`, so new swing risk is
permitted. Read the manifest at workflows/swing-opportunity-daily.yaml and
execute its steps: run the screeners (vcp-screener, optionally canslim-screener
/ theme-detector), validate setups with technical-analyst, size with
position-sizer, and register each surviving idea as an IDEA thesis via
trader-memory-core. Save the usual report artifacts under reports/.

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
the month's closed theses, run the aggregate postmortem and performance-coach
review, revalidate hypotheses, and produce the monthly decision log plus rule
changes for next month. Save the usual report artifacts under reports/.

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


def build_evening_closed_msg(date_str: str, dec: dict) -> str:
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
        body = "Отчёт сформирован — см. reports/."
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
            f"✅ ДЕЙСТВИЕ\nПеренести правила в monthly/{date_str[:7]}.md. Полный отчёт в reports/."
        )
    return (
        f"📊 MONTHLY REVIEW · {date_str} (1-е вс месяца, ~11:00 CET)\n"
        "Workflow: monthly-performance-review · monthly_decision_log + rule_changes\n\n"
        f"{body}"
    )


# --------------------------------------------------------------------------- #
# Slot handlers
# --------------------------------------------------------------------------- #
def slot_premarket(date_str: str, args) -> int:
    gate = decision_path(date_str)
    ok = run_claude(
        regime_prompt(date_str, gate, quick=True),
        label="market-regime-daily (premarket re-check)",
        dry_run=args.dry_run,
        timeout=args.timeout,
    )
    dec = (
        {
            "decision": "restrict",
            "rationale": "regime workflow did not complete; fail-safe.",
            "degraded": True,
        }
        if not ok and not args.dry_run
        else read_decision(gate)
    )
    log(
        f"exposure decision: {dec['decision'].upper()}"
        + (" (degraded/fail-safe)" if dec.get("degraded") else ""),
        logging.WARNING if dec.get("degraded") else logging.INFO,
    )

    msg = build_premarket_msg(date_str, dec, latest_watchlist())
    notify(msg, dry_run=args.dry_run, no_telegram=args.no_telegram)
    return 0 if ok or args.dry_run else 1


def slot_evening_prep(date_str: str, args) -> int:
    gate = decision_path(date_str)
    ok = run_claude(
        regime_prompt(date_str, gate, quick=False),
        label="market-regime-daily (evening EOD)",
        dry_run=args.dry_run,
        timeout=args.timeout,
    )
    dec = (
        read_decision(gate)
        if (ok or args.dry_run)
        else {
            "decision": "restrict",
            "rationale": "regime workflow did not complete; fail-safe.",
            "degraded": True,
        }
    )
    log(
        f"exposure decision: {dec['decision'].upper()}"
        + (" (degraded/fail-safe)" if dec.get("degraded") else ""),
        logging.WARNING if dec.get("degraded") else logging.INFO,
    )

    if dec["decision"] != "allow":
        notify(
            build_evening_closed_msg(date_str, dec),
            dry_run=args.dry_run,
            no_telegram=args.no_telegram,
        )
        return 0 if ok or args.dry_run else 1

    # Gate is allow -> build tomorrow's watchlist
    watchlist = SCHEDULE_DIR / f"watchlist_{date_str}.json"
    ok2 = run_claude(
        opportunity_prompt(date_str, gate, watchlist),
        label="swing-opportunity-daily (evening screen)",
        dry_run=args.dry_run,
        timeout=args.timeout,
    )
    candidates = []
    if watchlist.exists():
        try:
            candidates = (
                json.loads(watchlist.read_text(encoding="utf-8")).get("candidates", []) or []
            )
        except (json.JSONDecodeError, OSError):
            candidates = []
    wl_rel = watchlist.relative_to(PROJECT_ROOT) if watchlist.exists() else "(файл не создан)"

    msg = build_evening_allow_msg(date_str, dec, wl_rel, candidates)
    notify(
        msg,
        file=str(watchlist) if watchlist.exists() else None,
        dry_run=args.dry_run,
        no_telegram=args.no_telegram,
    )
    return 0 if (ok and ok2) or args.dry_run else 1


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
    "monthly": slot_monthly,
}


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
        args.slot in ("premarket", "evening-prep")
        and not args.force
        and not is_us_trading_day(today)
    ):
        log(f"{date_str} is not a US trading day -- skipping slot {args.slot}.")
        return _finish(0)
    if args.slot == "monthly" and not args.force and not is_first_sunday(today):
        log(f"{date_str} is not the first Sunday of the month -- skipping monthly review.")
        return _finish(0)

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


if __name__ == "__main__":
    sys.exit(main())
