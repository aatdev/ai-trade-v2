#!/usr/bin/env python3
"""
Bottom Flow Divergence Screener - Main Entry Point

Finds stocks whose PRICE is on the floor (near the 52-week low, deep below the
52-week high) while a FLOW signal refuses to confirm that floor — either the
fundamentals never broke (TTM revenue still growing + positive operating cash
flow, HOOD-type) or the tape is being accumulated (Chaikin Money Flow / Money
Flow Index, the contrarian "MRNA-type" layer).

Data: one POST to scanner.tradingview.com (the public "All Stocks" screener
endpoint — no API key, no auth, no TradingView Desktop). Self-contained so the
skill packages as a standalone .skill ZIP. Offline `--fixture` mode replays a
saved scanner response for testing.

Detection-only: emits JSON + Markdown a human reviews before any entry.

Examples:
    # Live scan of beaten-down liquid US common stocks
    python3 screen_bottom_flow.py --output-dir reports/

    # Only clean dual-divergence names that have started turning up
    python3 screen_bottom_flow.py --grades A --require-turn --require-survivable

    # Offline replay from the bundled fixture
    python3 screen_bottom_flow.py --fixture scripts/tests/fixtures/sample.json \
        --as-of 2026-06-17 --output-dir reports/
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from report_generator import build_result_record, write_reports  # noqa: E402
from scorer import (  # noqa: E402
    SCAN_COLUMNS,
    ScoreConfig,
    classify,
    extract_metrics,
    passes_bottom_gate,
)

SCAN_URL_TEMPLATE = "https://scanner.tradingview.com/{market}/scan"
USER_AGENT = "Mozilla/5.0 (claude-trading-skills bottom-flow-divergence-screener)"
MAX_RETRIES = 3

# All Stocks universe arms (mirrors tradingview-screener; common stocks only here).
_ARM_COMMON = {
    "operation": {
        "operator": "and",
        "operands": [
            {"expression": {"left": "type", "operation": "equal", "right": "stock"}},
            {"expression": {"left": "typespecs", "operation": "has", "right": ["common"]}},
        ],
    }
}
_ARM_PREFERRED = {
    "operation": {
        "operator": "and",
        "operands": [
            {"expression": {"left": "type", "operation": "equal", "right": "stock"}},
            {"expression": {"left": "typespecs", "operation": "has", "right": ["preferred"]}},
        ],
    }
}
_UNIVERSE_ARMS = {"common": [_ARM_COMMON], "all": [_ARM_COMMON, _ARM_PREFERRED]}


@dataclass
class ScreenConfig:
    """Run configuration: scan filters, universe, gates, and output."""

    # Server-side pre-filter (keep the fetch focused; the real gate is client-side)
    max_perf_1y: float = -10.0  # require Perf.Y below this (beaten down over the year)
    min_cap: float = 1_000_000_000.0
    min_avg_vol: float = 500_000.0
    min_price: float = 5.0
    limit: int = 500
    market: str = "america"
    universe: str = "common"
    # Output / selection
    grades: tuple = ("A", "B-accum", "B-fund")
    require_turn: bool = False
    require_survivable: bool = False
    top: int = 40
    score_cfg: ScoreConfig = field(default_factory=ScoreConfig)


# ---------------------------------------------------------------------------
# Scanner payload + HTTP (self-contained)
# ---------------------------------------------------------------------------


def build_payload(cfg: ScreenConfig) -> dict:
    """Build the scanner /scan payload for beaten-down liquid names."""
    arms = _UNIVERSE_ARMS.get(cfg.universe)
    if arms is None:
        raise ValueError(
            f"Unknown universe '{cfg.universe}'. Choose from: {sorted(_UNIVERSE_ARMS)}"
        )
    return {
        "columns": list(SCAN_COLUMNS),
        "filter": [
            {"left": "is_blacklisted", "operation": "equal", "right": False},
            {"left": "is_primary", "operation": "equal", "right": True},
            {"left": "market_cap_basic", "operation": "greater", "right": cfg.min_cap},
            {"left": "average_volume_30d_calc", "operation": "greater", "right": cfg.min_avg_vol},
            {"left": "close", "operation": "greater", "right": cfg.min_price},
            {"left": "Perf.Y", "operation": "less", "right": cfg.max_perf_1y},
        ],
        "filter2": {
            "operator": "and",
            "operands": [
                {"operation": {"operator": "or", "operands": copy.deepcopy(arms)}},
                {
                    "expression": {
                        "left": "typespecs",
                        "operation": "has_none_of",
                        "right": ["pre-ipo"],
                    }
                },
            ],
        },
        "ignore_unknown_fields": False,
        "options": {"lang": "en"},
        "markets": [cfg.market],
        "range": [0, cfg.limit],
        "sort": {"sortBy": "Perf.Y", "sortOrder": "asc"},
    }


class ScanError(Exception):
    """Fatal scan failure (validation error or retries exhausted)."""


def _http_post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:  # pragma: no cover - best effort
            pass
        if exc.code == 429 or exc.code >= 500:
            raise _TransientScanError(f"HTTP {exc.code}: {detail}") from exc
        raise ScanError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise _TransientScanError(f"Network error: {exc.reason}") from exc


class _TransientScanError(Exception):
    """Retryable failure (HTTP 429/5xx, network timeouts)."""


def run_scan(payload: dict, market: str, *, timeout: int = 30) -> dict:
    """POST the payload with exponential-backoff retries on transient failures."""
    url = SCAN_URL_TEMPLATE.format(market=market)
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return _http_post_json(url, payload, timeout)
        except _TransientScanError as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                delay = 1.5 * (2**attempt)
                print(
                    f"Warning: {exc} — retrying in {delay:.1f}s ({attempt + 1}/{MAX_RETRIES - 1})",
                    file=sys.stderr,
                )
                time.sleep(delay)
    raise ScanError(f"Scan failed after {MAX_RETRIES} attempts: {last_error}")


def rows_from_response(response: dict, columns: list[str]) -> list[dict]:
    """Map a raw scanner response into a list of {symbol, <field>: value} dicts."""
    rows = []
    for item in response.get("data", []):
        row = {"symbol": item.get("s", "")}
        for col, val in zip(columns, item.get("d", [])):
            row[col] = val
        rows.append(row)
    return rows


def fetch_rows(cfg: ScreenConfig) -> list[dict]:
    """Live path: build payload, POST, parse rows."""
    response = run_scan(build_payload(cfg), cfg.market)
    return rows_from_response(response, list(SCAN_COLUMNS))


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_from_rows(rows: list[dict], cfg: ScreenConfig) -> tuple[list[dict], dict]:
    """Score + grade every row; return (graded_records, stats)."""
    records: list[dict] = []
    stats = {"scanned": len(rows), "rejected_bottom": 0, "no_divergence": 0}
    for row in rows:
        m = extract_metrics(row)
        ok, reason = passes_bottom_gate(m, cfg.score_cfg)
        if not ok:
            stats["rejected_bottom"] += 1
            continue
        verdict = classify(m, cfg.score_cfg)
        if verdict["grade"] is None:
            stats["no_divergence"] += 1
            continue
        records.append(build_result_record(m, verdict))
    return records, stats


def filter_and_rank(records: list[dict], cfg: ScreenConfig) -> list[dict]:
    """Apply grade selection + optional hard gates, then rank and cap."""
    selected = [r for r in records if r["grade"] in cfg.grades]
    if cfg.require_turn:
        selected = [r for r in selected if r["turning"]]
    if cfg.require_survivable:
        selected = [r for r in selected if r["survivable"]]
    grade_order = {"A": 0, "B-accum": 1, "B-fund": 2}
    selected.sort(key=lambda r: (grade_order.get(r["grade"], 9), -r["score"]))
    return selected[: cfg.top] if cfg.top and cfg.top > 0 else selected


def _build_meta(cfg: ScreenConfig, stats: dict, as_of: str | None) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "file_stamp": (as_of or now.strftime("%Y-%m-%d")) + now.strftime("_%H%M%S"),
        "as_of": as_of or "live",
        "scanned": stats.get("scanned", 0),
        "rejected_bottom": stats.get("rejected_bottom", 0),
        "no_divergence": stats.get("no_divergence", 0),
        "near_low_pct": cfg.score_cfg.near_low_pct,
        "min_drawdown_pct": cfg.score_cfg.min_drawdown_pct,
        "require_turn": cfg.require_turn,
        "require_survivable": cfg.require_survivable,
        "grades": list(cfg.grades),
        "universe": cfg.universe,
        "market": cfg.market,
    }


def run_from_fixture(path: str, cfg: ScreenConfig, as_of: str | None) -> tuple:
    """Offline replay: load rows from a saved fixture and run the pipeline."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "data" in data:  # raw scanner response
        rows = rows_from_response(data, list(SCAN_COLUMNS))
    else:  # pre-parsed {"rows": [...]} or a bare list
        rows = data["rows"] if isinstance(data, dict) else data
    records, stats = run_from_rows(rows, cfg)
    return filter_and_rank(records, cfg), _build_meta(cfg, stats, as_of)


def run_live(cfg: ScreenConfig) -> tuple:
    records, stats = run_from_rows(fetch_rows(cfg), cfg)
    return filter_and_rank(records, cfg), _build_meta(cfg, stats, None)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _trading_data_dir():
    repo_root = Path(__file__).resolve().parents[3]
    base = os.environ.get("TRADING_DATE_DIR")
    if not base:
        try:
            for line in (repo_root / ".env").read_text(encoding="utf-8").splitlines():
                line = line.strip().removeprefix("export ").lstrip()
                if line.startswith("TRADING_DATE_DIR="):
                    base = line.partition("=")[2].strip().strip("'\"")
                    break
        except OSError:
            pass
    if not base:
        return None
    base_path = Path(base).expanduser()
    return base_path if base_path.is_absolute() else repo_root / base_path


def _default_output_dir() -> str:
    base = _trading_data_dir()
    return str(base / "screeners") if base else "reports/"


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Screen for bottom + flow divergence candidates.")
    p.add_argument("--fixture", help="Replay a saved scanner response / rows file (offline).")
    p.add_argument("--as-of", help="As-of date label for fixture replay (YYYY-MM-DD).")
    p.add_argument("--market", default="america", help="TV market endpoint (default america).")
    p.add_argument("--universe", default="common", choices=["common", "all"])
    p.add_argument(
        "--max-perf-1y",
        type=float,
        default=-10.0,
        help="Require 1y performance below this %% (default -10).",
    )
    p.add_argument("--min-cap", type=float, default=1_000_000_000.0, help="Min market cap (USD).")
    p.add_argument(
        "--min-avg-vol", type=float, default=500_000.0, help="Min 30d avg volume (shares)."
    )
    p.add_argument("--min-price", type=float, default=5.0, help="Min close price.")
    p.add_argument("--limit", type=int, default=500, help="Max rows to fetch (scan cap).")
    p.add_argument(
        "--near-low-pct",
        type=float,
        default=25.0,
        help="Max %% above 52w low to count as 'on the floor' (default 25).",
    )
    p.add_argument(
        "--min-drawdown-pct",
        type=float,
        default=35.0,
        help="Min %% below 52w high to count as 'beaten down' (default 35).",
    )
    p.add_argument(
        "--rev-ttm-min",
        type=float,
        default=0.0,
        help="TTM revenue growth floor for the fundamental layer (default 0).",
    )
    p.add_argument(
        "--mfi-min",
        type=float,
        default=50.0,
        help="Money Flow Index accumulation threshold (default 50).",
    )
    p.add_argument(
        "--grades",
        default="A,B-accum,B-fund",
        help="Comma list of grades to keep (A, B-accum, B-fund).",
    )
    p.add_argument(
        "--require-turn",
        action="store_true",
        help="Drop names still falling (keep only Perf.3M>=0 or close>SMA50).",
    )
    p.add_argument(
        "--require-survivable",
        action="store_true",
        help="Drop names failing the survivability check (unprofitable + weak balance sheet).",
    )
    p.add_argument("--top", type=int, default=40, help="Max candidates in the report (0 = all).")
    p.add_argument(
        "--output-dir", default=None, help="Output directory (default reports/ or trading-data)."
    )
    p.add_argument("--screen-name", default="bottom_flow_divergence", help="Report filename slug.")
    p.add_argument(
        "--dry-run", action="store_true", help="Print the scan payload as JSON; no network."
    )
    return p.parse_args(argv)


def _cfg_from_args(args: argparse.Namespace) -> ScreenConfig:
    grades = tuple(g.strip() for g in args.grades.split(",") if g.strip())
    return ScreenConfig(
        max_perf_1y=args.max_perf_1y,
        min_cap=args.min_cap,
        min_avg_vol=args.min_avg_vol,
        min_price=args.min_price,
        limit=args.limit,
        market=args.market,
        universe=args.universe,
        grades=grades,
        require_turn=args.require_turn,
        require_survivable=args.require_survivable,
        top=args.top,
        score_cfg=ScoreConfig(
            near_low_pct=args.near_low_pct,
            min_drawdown_pct=args.min_drawdown_pct,
            rev_ttm_min=args.rev_ttm_min,
            mfi_min=args.mfi_min,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    cfg = _cfg_from_args(args)

    if args.dry_run:
        print(json.dumps(build_payload(cfg), indent=2))
        return 0

    try:
        if args.fixture:
            records, meta = run_from_fixture(args.fixture, cfg, args.as_of)
        else:
            records, meta = run_live(cfg)
    except (ScanError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output_dir = args.output_dir or _default_output_dir()
    md_path, json_path = write_reports(records, meta, output_dir, args.screen_name)
    print(
        f"Scanned {meta['scanned']} beaten-down names → {len(records)} candidates "
        f"(A={sum(1 for r in records if r['grade'] == 'A')}, "
        f"B-accum={sum(1 for r in records if r['grade'] == 'B-accum')}, "
        f"B-fund={sum(1 for r in records if r['grade'] == 'B-fund')})"
    )
    print(f"Markdown report: {md_path}")
    print(f"JSON report:     {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
