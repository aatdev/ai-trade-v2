#!/usr/bin/env python3
"""Build (and optionally register) a watchlist from a staged VCP screener + plan.

Backs the UI "Screener" tab Save buttons. The UI runs the VCP screener and the
breakout-trade-planner into ``<trading-data>/ui-staging/`` and registers NOTHING
in the canonical ``screeners/`` / ``plans/`` / ``schedule/`` dirs until the user
clicks Save. This script performs that registration, reusing the canonical
``trading_signals.build_watchlist`` so the watchlist shape never drifts from the
scheduler's evening long-branch (``run_trading_schedule._evening_long_branch``).

Modes (flags):
  --promote        copy the staged screener (+ plan) into screeners/ and plans/
                   so ``source_plan`` resolves and the rest of the pipeline (the
                   intraday monitor, thesis-ingest) sees the same artifacts.
  --ingest-theses  register watchlist candidates as theses (trader-memory-core)
                   and inject ``thesis_id`` back into the watchlist. Best-effort.
  --sync-alerts    sync TradingView ``[WL]`` watchlist alerts (best-effort; needs
                   TradingView Desktop with CDP on :9222).

Exit code 0 once the watchlist file is written; theses/alerts failures are logged
as warnings and do NOT fail the run (the watchlist write is the durable result).
Non-zero only when reading inputs or writing the watchlist itself fails.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

import trading_signals as tsig  # noqa: E402

TRADER_MEMORY_CLI = REPO_ROOT / "skills" / "trader-memory-core" / "scripts" / "trader_memory_cli.py"
ALERTS_STATE_REL = ("logs", "watchlist_alerts_state.json")


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr, flush=True)


def _atomic_write_json(path: Path, data: dict) -> None:
    """Crash/torn-read-safe JSON write (tmp + rename). Copied verbatim from
    run_trading_schedule._atomic_write_json — that module has heavy import-time
    side effects, so we duplicate the 8 lines instead of importing it. The UI and
    the intraday monitor read these files while we rewrite them."""
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


def _resolve_data_dir(arg: str | None) -> Path:
    """Trading-data root: --data-dir, else $TRADING_DATE_DIR (env or repo .env),
    else ``trading-data`` under the repo. Mirrors plan_breakout_trades."""
    base = arg or os.environ.get("TRADING_DATE_DIR")
    if not base:
        try:
            for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
                line = line.strip().removeprefix("export ").lstrip()
                if line.startswith("TRADING_DATE_DIR="):
                    base = line.partition("=")[2].strip().strip("'\"")
                    break
        except OSError:
            pass
    if not base:
        base = "trading-data"
    p = Path(base).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


def _rel(p: Path) -> str:
    """Repo-relative path string (absolute if outside). Matches the scheduler's
    _rel so ``source_plan`` reads identically to scheduler-built watchlists."""
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def _read_gate_decision(schedule_dir: Path, date_str: str) -> str:
    """Real exposure gate for the day.

    Fails safe to 'restrict' when the decision file is missing, unreadable, or
    carries no decision — the same direction the scheduler uses, so a day whose
    regime/gate step never ran does not silently arm fresh long risk.
    """
    f = schedule_dir / f"exposure_decision_{date_str}.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "restrict"
    return str(data.get("decision", "")).strip().lower() or "restrict"


def _promote(staged: Path, dest_dir: Path, prefix: str, ts: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{prefix}_{ts}.json"
    shutil.copyfile(staged, dest)
    return dest


def _ingest_theses(
    canonical_vcp: Path,
    canonical_plan: Path | None,
    wl: dict,
    wl_path: Path,
    schedule_dir: Path,
    date_str: str,
) -> None:
    """Register candidates as theses + inject thesis_id back into the watchlist.
    Best-effort: a failure logs a warning and leaves the watchlist intact.
    Mirrors run_trading_schedule._ingest_theses."""
    ids_output = schedule_dir / f"thesis_ids_{date_str}.json"
    cmd = [
        sys.executable,
        str(TRADER_MEMORY_CLI),
        "ingest",
        "--source",
        "vcp-screener",
        "--input",
        str(canonical_vcp),
        "--watchlist-filter",
        str(wl_path),
        "--ids-output",
        str(ids_output),
    ]
    if canonical_plan:
        cmd += ["--plan-input", str(canonical_plan)]
    try:
        subprocess.run(cmd, check=True, cwd=str(REPO_ROOT))
    except (subprocess.CalledProcessError, OSError) as exc:
        warn(f"thesis-ingest failed: {exc}")
        return
    try:
        ticker_to_tid = json.loads(ids_output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        ticker_to_tid = {}
    if ticker_to_tid:
        for cand in wl.get("candidates") or []:
            tid = ticker_to_tid.get(str(cand.get("ticker", "")).upper())
            if tid:
                cand["thesis_id"] = tid
        _atomic_write_json(wl_path, wl)
        log(f"thesis-ingest: thesis_id injected into {len(ticker_to_tid)} candidate(s)")


def _sync_alerts(wl: dict, data_dir: Path) -> None:
    """Sync TradingView [WL] watchlist alerts. Best-effort: needs TradingView
    Desktop; on any failure logs a warning and the watchlist stays written."""
    try:
        import tv_alerts as talerts
    except ImportError as exc:
        warn(f"tv_alerts import failed: {exc}")
        return
    if not talerts.tv_available():
        warn("TradingView Desktop not reachable (CDP :9222) — alerts skipped")
        return
    state_path = data_dir.joinpath(*ALERTS_STATE_REL)
    try:
        res = talerts.sync_watchlist_alerts(wl, state_path, project_root=REPO_ROOT)
        log(f"watchlist-alerts: {json.dumps(res, ensure_ascii=False)}")
    except Exception as exc:  # best-effort; never fail the save over alerts
        warn(f"watchlist-alerts sync failed: {exc}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a watchlist from a staged VCP screener + plan.")
    ap.add_argument("--staged-vcp", required=True, help="Path to the staged VCP screener JSON")
    ap.add_argument("--staged-plan", help="Path to the staged breakout plan JSON (optional)")
    ap.add_argument("--date", required=True, help="Watchlist date (YYYY-MM-DD)")
    ap.add_argument(
        "--promote",
        action="store_true",
        help="Copy staged screener+plan into screeners/ and plans/",
    )
    ap.add_argument(
        "--ingest-theses",
        action="store_true",
        help="Register theses + inject thesis_id (best-effort)",
    )
    ap.add_argument(
        "--sync-alerts", action="store_true", help="Sync TradingView [WL] alerts (best-effort)"
    )
    ap.add_argument("--data-dir", help="Override trading-data dir (else $TRADING_DATE_DIR)")
    args = ap.parse_args(argv)

    try:
        dt.date.fromisoformat(args.date)
    except ValueError:
        ap.error(f"--date must be YYYY-MM-DD, got {args.date!r}")

    data_dir = _resolve_data_dir(args.data_dir)
    schedule_dir = data_dir / "schedule"

    staged_vcp = Path(args.staged_vcp).expanduser()
    if not staged_vcp.is_file():
        log(f"ERROR: staged VCP not found: {staged_vcp}")
        return 1
    staged_plan = Path(args.staged_plan).expanduser() if args.staged_plan else None
    if staged_plan and not staged_plan.is_file():
        log(f"ERROR: staged plan not found: {staged_plan}")
        return 1

    plan = None
    if staged_plan:
        try:
            plan = json.loads(staged_plan.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log(f"ERROR: cannot read staged plan: {exc}")
            return 1

    gate_decision = _read_gate_decision(schedule_dir, args.date)
    log(f"gate decision: {gate_decision}")

    canonical_vcp = staged_vcp
    canonical_plan = staged_plan
    source_plan = None
    if args.promote:
        ts = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        canonical_vcp = _promote(staged_vcp, data_dir / "screeners", "vcp_screener", ts)
        log(f"promoted screener → {_rel(canonical_vcp)}")
        if staged_plan:
            canonical_plan = _promote(staged_plan, data_dir / "plans", "breakout_trade_plan", ts)
            source_plan = _rel(canonical_plan)
            log(f"promoted plan → {source_plan}")

    wl = tsig.build_watchlist(
        args.date,
        gate_decision,
        plan,
        None,
        None,
        source_plan=source_plan,
    )
    wl_path = schedule_dir / f"watchlist_{args.date}.json"
    try:
        _atomic_write_json(wl_path, wl)
    except OSError as exc:
        log(f"ERROR: failed to write watchlist: {exc}")
        return 1
    n = len(wl.get("candidates") or [])
    log(f"watchlist written → {_rel(wl_path)} ({n} candidate(s))")

    if args.ingest_theses:
        _ingest_theses(canonical_vcp, canonical_plan, wl, wl_path, schedule_dir, args.date)
    if args.sync_alerts:
        _sync_alerts(wl, data_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
