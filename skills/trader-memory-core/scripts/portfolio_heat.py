#!/usr/bin/env python3
"""Portfolio heat ledger — aggregate live open risk from the thesis store.

Reads ACTIVE / PARTIALLY_CLOSED theses from ``state/theses/`` and computes the
portfolio's *live* open risk ("heat"):

    risk per position = max(0, entry_price - current stop) x shares remaining

(long convention; a stop raised to/above entry contributes zero heat). Output
is a JSON + Markdown report whose top level is directly consumable by
breakout-trade-planner ``--current-exposure-json``::

    {"open_risk_pct": <float>, "sector_exposure": {"<sector>": <pct>}, ...}

Position value (and therefore sector exposure) is approximated at entry price —
heat itself does not depend on the current quote. Stops are read from
``exit.stop_loss``; keep them updated as you trail (``store ... close`` /
``update``) or the ledger reports stale risk. When a stop is missing the
position-sizer's recorded ``position.risk_dollars`` is used as a fallback;
with neither, the position is flagged and ``heat_complete`` turns false.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Sibling import (thesis_store lives in the same scripts/ directory)
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import thesis_store  # noqa: E402

OPEN_STATUSES = ("ACTIVE", "PARTIALLY_CLOSED")

# Parameter-profile keys shared across the trading scripts (planner, sizer,
# heat ledger). Keys outside this union trigger a warning (typo guard); keys
# inside it that this script does not use are skipped silently.
KNOWN_PROFILE_KEYS = {
    "account_size",
    "risk_pct",
    "max_position_pct",
    "max_sector_pct",
    "max_portfolio_heat_pct",
    "max_positions",
    "target_r_multiple",
    "stop_buffer_pct",
    "max_chase_pct",
    "pivot_buffer_pct",
    "earnings_gate_days",
    "time_stop_trading_days",
    "atr_multiplier",
}

HEAT_PROFILE_KEYS = {"account_size", "max_portfolio_heat_pct", "max_positions"}


def load_profile(path: str, applied_keys: set[str]) -> dict:
    """Load a JSON parameter profile and return the keys this script applies."""
    with open(path) as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("profile JSON must be an object of parameter values")

    unknown = sorted(set(raw) - KNOWN_PROFILE_KEYS)
    if unknown:
        print(
            f"Warning: ignoring unknown profile keys: {', '.join(unknown)}",
            file=sys.stderr,
        )

    applied: dict[str, float] = {}
    for key in sorted(applied_keys & set(raw)):
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"profile key '{key}' must be a number, got {value!r}")
        applied[key] = value
    return applied


def _extract_position(thesis: dict, warnings: list[dict]) -> dict | None:
    """Build one ledger row from a thesis; append warnings for gaps."""
    tid = thesis["thesis_id"]
    ticker = thesis["ticker"]

    pos = thesis.get("position") or {}
    shares = pos.get("shares_remaining")
    if shares is None:
        shares = pos.get("shares")
    entry_price = (thesis.get("entry") or {}).get("actual_price")

    if not shares or not entry_price:
        warnings.append(
            {
                "thesis_id": tid,
                "ticker": ticker,
                "code": "MISSING_POSITION_DATA",
                "message": "no shares and/or entry.actual_price recorded — excluded from ledger",
            }
        )
        return None

    stop = (thesis.get("exit") or {}).get("stop_loss")
    sector = (((thesis.get("origin") or {}).get("raw_provenance") or {}).get("sector")) or "Unknown"

    if stop is not None:
        risk_dollars = round(max(0.0, float(entry_price) - float(stop)) * float(shares), 2)
        risk_basis = "entry_minus_stop"
    elif pos.get("risk_dollars") is not None:
        risk_dollars = round(float(pos["risk_dollars"]), 2)
        risk_basis = "sizer_risk_dollars"
        warnings.append(
            {
                "thesis_id": tid,
                "ticker": ticker,
                "code": "STOP_MISSING_USED_SIZER_RISK",
                "message": (
                    "exit.stop_loss not set — using position.risk_dollars from the "
                    "sizing report (may be stale if the stop has moved)"
                ),
            }
        )
    else:
        risk_dollars = None
        risk_basis = "unknown"
        warnings.append(
            {
                "thesis_id": tid,
                "ticker": ticker,
                "code": "STOP_MISSING",
                "message": (
                    "exit.stop_loss not set and no recorded risk_dollars — "
                    "risk unknown, heat is understated"
                ),
            }
        )

    return {
        "thesis_id": tid,
        "ticker": ticker,
        "status": thesis["status"],
        "shares": shares,
        "entry_price": entry_price,
        "stop_loss": stop,
        "sector": sector,
        "position_value": round(float(entry_price) * float(shares), 2),
        "risk_dollars": risk_dollars,
        "risk_basis": risk_basis,
    }


def collect_positions(state_dir: Path) -> tuple[list[dict], list[dict]]:
    """Collect open positions (ACTIVE / PARTIALLY_CLOSED) from the thesis store."""
    positions: list[dict] = []
    warnings: list[dict] = []

    entries: list[dict] = []
    for status in OPEN_STATUSES:
        entries.extend(thesis_store.query(state_dir, status=status))

    for entry in sorted(entries, key=lambda e: e["thesis_id"]):
        thesis = thesis_store.get(state_dir, entry["thesis_id"])
        row = _extract_position(thesis, warnings)
        if row is not None:
            positions.append(row)
    return positions, warnings


def build_report(
    positions: list[dict],
    warnings: list[dict],
    *,
    account_size: float,
    max_heat_pct: float,
    max_positions: int | None,
) -> dict:
    """Aggregate ledger rows into the heat report (planner-compatible top level)."""
    known = [p for p in positions if p["risk_dollars"] is not None]
    open_risk_dollars = round(sum(p["risk_dollars"] for p in known), 2)
    open_risk_pct = round(open_risk_dollars / account_size * 100, 2) if account_size else 0.0

    sector_exposure: dict[str, float] = {}
    for p in positions:
        pct = p["position_value"] / account_size * 100 if account_size else 0.0
        sector_exposure[p["sector"]] = round(sector_exposure.get(p["sector"], 0.0) + pct, 2)

    enriched = []
    for p in positions:
        risk_pct = (
            round(p["risk_dollars"] / account_size * 100, 3)
            if (p["risk_dollars"] is not None and account_size)
            else None
        )
        enriched.append({**p, "risk_pct_of_account": risk_pct})

    remaining_heat_pct = round(max(0.0, max_heat_pct - open_risk_pct), 2)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "account_size": account_size,
        # Planner-compatible keys (breakout-trade-planner --current-exposure-json)
        "open_risk_pct": open_risk_pct,
        "sector_exposure": sector_exposure,
        # Ledger detail
        "open_risk_dollars": open_risk_dollars,
        "positions_count": len(positions),
        "heat_complete": len(known) == len(positions),
        "max_portfolio_heat_pct": max_heat_pct,
        "remaining_heat_pct": remaining_heat_pct,
        "remaining_heat_dollars": round(remaining_heat_pct / 100 * account_size, 2),
        "max_positions": max_positions,
        "remaining_position_slots": (
            max(0, int(max_positions) - len(positions)) if max_positions is not None else None
        ),
        "positions": enriched,
        "warnings": warnings,
    }


def generate_markdown(report: dict) -> str:
    """Human-readable summary of the heat report."""
    lines = [
        "# Portfolio Heat Ledger",
        f"**Generated:** {report['generated_at']}",
        f"**Account Size:** ${report['account_size']:,.0f}",
        "",
        "## Summary",
        f"- Open risk: ${report['open_risk_dollars']:,.2f} "
        f"({report['open_risk_pct']:.2f}% of account)",
        f"- Heat budget: {report['max_portfolio_heat_pct']:.2f}% — remaining "
        f"{report['remaining_heat_pct']:.2f}% (${report['remaining_heat_dollars']:,.2f})",
        f"- Positions: {report['positions_count']}"
        + (
            f" / {report['max_positions']} ({report['remaining_position_slots']} slots free)"
            if report["max_positions"] is not None
            else ""
        ),
        f"- Heat complete: {'yes' if report['heat_complete'] else 'NO — see warnings'}",
        "",
    ]

    if report["positions"]:
        lines.append("## Open Positions\n")
        lines.append("| Ticker | Status | Shares | Entry | Stop | Risk $ | Risk % | Sector |")
        lines.append("|--------|--------|--------|-------|------|--------|--------|--------|")
        for p in report["positions"]:
            risk_d = f"${p['risk_dollars']:,.2f}" if p["risk_dollars"] is not None else "?"
            risk_p = f"{p['risk_pct_of_account']}%" if p["risk_pct_of_account"] is not None else "?"
            stop = f"${p['stop_loss']:.2f}" if p["stop_loss"] is not None else "—"
            lines.append(
                f"| {p['ticker']} | {p['status']} | {p['shares']} | "
                f"${p['entry_price']:.2f} | {stop} | {risk_d} | {risk_p} | {p['sector']} |"
            )
        lines.append("")

    if report["sector_exposure"]:
        lines.append("## Sector Exposure (% of account, at entry prices)\n")
        for sector, pct in sorted(report["sector_exposure"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {sector}: {pct:.2f}%")
        lines.append("")

    if report["warnings"]:
        lines.append("## Warnings\n")
        for w in report["warnings"]:
            lines.append(f"- **{w.get('ticker', '?')}** [{w['code']}]: {w['message']}")
        lines.append("")

    lines.append("\n---\n*Risk = max(0, entry − stop) × shares; not investment advice.*\n")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else list(argv)

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--profile", default=os.environ.get("TRADING_PROFILE"))
    pre_args, _ = pre.parse_known_args(raw_argv)

    parser = argparse.ArgumentParser(
        description=(
            "Compute live portfolio heat (open risk) from trader-memory-core theses. "
            "JSON output feeds breakout-trade-planner --current-exposure-json."
        )
    )
    parser.add_argument("--state-dir", required=True, help="Path to state/theses/")
    parser.add_argument(
        "--account-size",
        type=float,
        default=None,
        help="Account equity ($); required unless provided via --profile",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("TRADING_PROFILE"),
        help=(
            "JSON parameter profile (account_size, max_portfolio_heat_pct, "
            "max_positions). Explicit CLI flags override profile values. "
            "Default: $TRADING_PROFILE."
        ),
    )
    parser.add_argument(
        "--max-portfolio-heat-pct",
        type=float,
        default=6.0,
        help="Heat budget ceiling in %% of account (default: 6.0)",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=None,
        help="Max concurrent positions (reports remaining slots when set)",
    )
    parser.add_argument("--output-dir", default="reports/")
    parser.add_argument(
        "--json-only", action="store_true", help="Write only the JSON report (skip markdown)"
    )

    if pre_args.profile:
        try:
            parser.set_defaults(**load_profile(pre_args.profile, HEAT_PROFILE_KEYS))
        except (OSError, ValueError) as exc:
            print(f"Error: cannot load profile '{pre_args.profile}': {exc}", file=sys.stderr)
            return 1

    args = parser.parse_args(raw_argv)
    if args.account_size is None:
        parser.error("--account-size is required (pass it directly or via --profile)")

    state_dir = Path(args.state_dir)
    if not state_dir.is_dir():
        print(f"Error: state dir not found: {state_dir}", file=sys.stderr)
        return 1

    positions, warnings = collect_positions(state_dir)
    report = build_report(
        positions,
        warnings,
        account_size=float(args.account_size),
        max_heat_pct=float(args.max_portfolio_heat_pct),
        max_positions=(int(args.max_positions) if args.max_positions is not None else None),
    )

    os.makedirs(args.output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    json_path = os.path.join(args.output_dir, f"portfolio_heat_{ts}.json")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"JSON report: {json_path}")

    if not args.json_only:
        md_path = os.path.join(args.output_dir, f"portfolio_heat_{ts}.md")
        with open(md_path, "w") as f:
            f.write(generate_markdown(report))
        print(f"Markdown report: {md_path}")

    print(
        f"\nOpen risk: ${report['open_risk_dollars']:,.2f} ({report['open_risk_pct']:.2f}%) "
        f"across {report['positions_count']} position(s); "
        f"remaining heat budget: {report['remaining_heat_pct']:.2f}%"
        + ("" if report["heat_complete"] else "  [INCOMPLETE — see warnings]")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
