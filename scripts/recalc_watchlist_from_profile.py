#!/usr/bin/env python3
"""Recompute the watchlist + non-active theses after a trading_profile change.

Backs the UI "Профиль" tab "Пересчитать" button. When the user edits
``trading_profile.json`` (risk_pct, atr_multiplier, target_r_multiple, sizing
caps, gates, ...), the watchlist candidates and the IDEA / ENTRY_READY theses on
disk still carry the OLD sizing/levels. This re-runs the breakout-trade-planner
on the MOST RECENT canonical VCP screener snapshot — no re-screen, so it is fast,
deterministic and needs no network — and rebuilds the watchlist + re-ingests
theses through the same path the evening-prep slot uses.

Faithful by construction:
  * the planner reads the (new) profile, so every sizing / level / gate param is
    re-applied exactly as ``_evening_long_branch`` would;
  * ``build_watchlist_from_plan --ingest-theses`` refreshes ONLY IDEA /
    ENTRY_READY theses — ``thesis_ingest`` never touches an ACTIVE thesis (its
    stop is the live broker bracket).

What it does NOT reflect: ``sector_rs_gate`` / ``sector_rs_threshold`` are applied
at SCREEN time, so changing those needs a full re-screen (evening-prep slot), not
this re-plan. The screener universe is frozen to the latest snapshot.

Exit codes: 0 ok; 2 no canonical VCP screener to re-plan from; 1 any other error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PLAN_SCRIPT = (
    REPO_ROOT / "skills" / "breakout-trade-planner" / "scripts" / "plan_breakout_trades.py"
)
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_watchlist_from_plan.py"


def log(msg: str) -> None:
    print(msg, flush=True)


def _resolve_data_dir(arg: str | None) -> Path:
    """Trading-data root: --data-dir, else $TRADING_DATE_DIR (env or repo .env),
    else ``trading-data`` under the repo. Mirrors build_watchlist_from_plan."""
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


def _latest(dir_path: Path, prefix: str) -> Path | None:
    """Newest ``<prefix>_*.json`` in dir_path by filename (timestamps sort
    lexically), or None. Matches the scheduler/UI 'latest snapshot' selection."""
    try:
        files = sorted(dir_path.glob(f"{prefix}_*.json"))
    except OSError:
        return None
    return files[-1] if files else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-plan the latest VCP screener with the current profile and rebuild "
        "the watchlist + non-active theses."
    )
    ap.add_argument("--data-dir", help="Override trading-data dir (else $TRADING_DATE_DIR)")
    ap.add_argument("--date", help="Watchlist date (YYYY-MM-DD, default today)")
    ap.add_argument(
        "--profile",
        help="Path to trading_profile.json (default <data-dir>/trading_profile.json)",
    )
    ap.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter for the planner/builder subprocesses",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planner + builder commands without running anything",
    )
    args = ap.parse_args(argv)

    date_str = args.date or dt.date.today().isoformat()
    try:
        dt.date.fromisoformat(date_str)
    except ValueError:
        ap.error(f"--date must be YYYY-MM-DD, got {date_str!r}")

    data_dir = _resolve_data_dir(args.data_dir)

    vcp = _latest(data_dir / "screeners", "vcp_screener")
    if vcp is None:
        log(
            "ERROR: no canonical VCP screener found under "
            f"{data_dir / 'screeners'} — run the evening-prep slot (or the Screener "
            "tab) first; nothing to re-plan from."
        )
        return 2

    profile = Path(args.profile).expanduser() if args.profile else data_dir / "trading_profile.json"
    if not profile.is_file():
        log(f"ERROR: trading profile not found: {profile} (planner needs account_size)")
        return 1

    heat = _latest(data_dir / "journal", "portfolio_heat")

    planner_argv = [
        args.python,
        str(PLAN_SCRIPT),
        "--input",
        str(vcp),
        "--profile",
        str(profile),
    ]
    if heat is not None:
        planner_argv += ["--current-exposure-json", str(heat)]

    def builder_argv(plan_path: str) -> list[str]:
        return [
            args.python,
            str(BUILD_SCRIPT),
            "--staged-vcp",
            str(vcp),
            "--staged-plan",
            plan_path,
            "--date",
            date_str,
            "--data-dir",
            str(data_dir),
            "--promote",
            "--ingest-theses",
        ]

    log(f"[recalc] data-dir: {data_dir}")
    log(f"[recalc] screener: {vcp}")
    log(f"[recalc] heat:     {heat if heat is not None else '(none)'}")
    log(f"[recalc] profile:  {profile}")

    if args.dry_run:
        log(
            "[recalc] planner: "
            + " ".join(shlex.quote(a) for a in planner_argv + ["--output-dir", "<TMP>"])
        )
        log(
            "[recalc] build:   "
            + " ".join(shlex.quote(a) for a in builder_argv("<TMP>/breakout_trade_plan_*.json"))
        )
        log("[recalc] dry-run — nothing executed.")
        return 0

    # Run the planner into a FRESH temp dir so exactly one plan file lands there
    # (no ambiguity about which plan to feed the builder).
    staging = data_dir / "ui-staging"
    staging.mkdir(parents=True, exist_ok=True)
    plan_dir = Path(tempfile.mkdtemp(prefix="recalc_plan_", dir=str(staging)))
    child_env = {
        **os.environ,
        "TRADING_DATE_DIR": str(data_dir),
        "CLAUDE_TRADING_SKILLS_REPO": str(REPO_ROOT),
    }
    try:
        log("[recalc] running breakout-trade-planner …")
        try:
            subprocess.run(
                planner_argv + ["--output-dir", str(plan_dir)],
                check=True,
                cwd=str(REPO_ROOT),
                env=child_env,
            )
        except (subprocess.CalledProcessError, OSError) as exc:
            log(f"ERROR: planner failed: {exc}")
            return 1

        plan_path = _latest(plan_dir, "breakout_trade_plan")
        if plan_path is None:
            log(f"ERROR: planner produced no plan file in {plan_dir}")
            return 1

        log("[recalc] rebuilding watchlist + re-ingesting non-active theses …")
        try:
            rc = subprocess.run(
                builder_argv(str(plan_path)),
                check=False,
                cwd=str(REPO_ROOT),
                env=child_env,
            ).returncode
        except OSError as exc:
            log(f"ERROR: build_watchlist_from_plan failed to start: {exc}")
            return 1
        if rc != 0:
            log(f"ERROR: build_watchlist_from_plan exited {rc}")
            return 1
        log(
            "[recalc] done — watchlist rebuilt; IDEA/ENTRY_READY theses refreshed, ACTIVE untouched."
        )
        return 0
    finally:
        shutil.rmtree(plan_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
