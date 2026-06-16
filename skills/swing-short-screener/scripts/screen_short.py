#!/usr/bin/env python3
"""
Swing Short Screener - Main Entry Point

Scan a universe (S&P 500 or a custom list) for Stage 4 downtrend weakness and
emit a graded short-side watchlist (JSON + Markdown). Detection-only: this
script never sends orders.

Pipeline:
  1. Resolve the index return (SPY proxy) for the RS lookback.
  2. For each symbol: fetch EOD history → compute weakness metrics.
  3. Hard-invalidate non-Stage-4 / illiquid / sub-$5 names.
  4. Score survivors on the 5-factor weakness model, assign A/B/C/D.
  5. Rank, filter by --min-grade, write reports.

Offline / test mode: pass --fixture <json> to bypass the network entirely.

Examples:
  # Custom universe (free FMP tier is fine)
  python3 skills/swing-short-screener/scripts/screen_short.py \
    --universe TSLA NFLX PYPL --output-dir reports/

  # Full S&P 500 (paid FMP tier recommended)
  python3 skills/swing-short-screener/scripts/screen_short.py \
    --full-sp500 --output-dir reports/

  # Offline replay from a fixture
  python3 skills/swing-short-screener/scripts/screen_short.py \
    --fixture skills/swing-short-screener/scripts/tests/fixtures/sample.json \
    --as-of 2026-04-30 --output-dir reports/
"""

import argparse
import json
import sys
from datetime import datetime

from report_generator import (
    build_result_record,
    generate_json_report,
    generate_markdown_report,
)
from scorer import score_candidate
from weakness_metrics import compute_metrics, pct_return

GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}

DEFAULT_MIN_PRICE = 5.0
DEFAULT_MIN_DOLLAR_VOL = 3_000_000.0
# Stop-distance sanity bounds (% of entry). Below the floor the stop sits in
# daily noise (a 0.4% stop is a guaranteed stop-out); above the ceiling the
# geometry is post-crash junk — the 2R target can't be reached inside the
# 10-trading-day short time-stop and risk-per-share dwarfs the position cap.
DEFAULT_MIN_STOP_PCT = 2.0
DEFAULT_MAX_STOP_PCT = 10.0


def _trading_data_dir():
    """Personal trading artifacts root: $TRADING_DATE_DIR (env or repo .env)."""
    import os
    from pathlib import Path

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


def _default_output_dir(bucket, fallback="reports/"):
    """Default dir: $TRADING_DATE_DIR/<bucket> when configured, else fallback."""
    base = _trading_data_dir()
    return str(base / bucket) if base else fallback


def passes_short_filter(
    metrics: dict,
    min_price: float = DEFAULT_MIN_PRICE,
    min_dollar_vol: float = DEFAULT_MIN_DOLLAR_VOL,
) -> tuple[bool, str]:
    """Hard invalidation. A name must be a tradable, liquid Stage 4 candidate.

    Returns (passed, reason). Reason is empty when passed.
    """
    if metrics is None:
        return False, "insufficient_history"
    if not metrics["below_ma200"]:
        return False, "above_ma200_not_stage4"
    if metrics["price"] < min_price:
        return False, "price_below_min"
    if metrics["avg_dollar_vol"] < min_dollar_vol:
        return False, "illiquid_squeeze_risk"
    return True, ""


def stop_geometry_reason(
    stop_pct: float,
    min_stop_pct: float = DEFAULT_MIN_STOP_PCT,
    max_stop_pct: float = DEFAULT_MAX_STOP_PCT,
) -> str:
    """Empty string when the stop distance is tradable; rejection reason otherwise."""
    if stop_pct < min_stop_pct:
        return "stop_too_tight_noise"
    if stop_pct > max_stop_pct:
        return "stop_too_wide_post_crash"
    return ""


def analyze_symbol(
    bars: list[dict],
    spy_return,
    name: str = "",
    sector: str = "",
    min_price: float = DEFAULT_MIN_PRICE,
    min_dollar_vol: float = DEFAULT_MIN_DOLLAR_VOL,
    rs_lookback: int = 63,
    min_stop_pct: float = DEFAULT_MIN_STOP_PCT,
    max_stop_pct: float = DEFAULT_MAX_STOP_PCT,
    sector_info: dict | None = None,
) -> tuple[dict, str]:
    """Run one symbol end-to-end. Returns (record_or_None, reject_reason)."""
    metrics = compute_metrics(bars, rs_lookback=rs_lookback)
    passed, reason = passes_short_filter(metrics, min_price, min_dollar_vol)
    if not passed:
        return None, reason
    score = score_candidate(metrics, spy_return, sector_info=sector_info)
    geometry = stop_geometry_reason(
        (score.get("trade_levels") or {}).get("stop_pct") or 0.0, min_stop_pct, max_stop_pct
    )
    if geometry:
        return None, geometry
    record = build_result_record(name or "", name, sector, metrics, score)
    return record, ""


def _index_return_from_bars(bars: list[dict], rs_lookback: int):
    closes = [float(b["close"]) for b in bars]
    return pct_return(closes, rs_lookback)


def run_from_fixture(fixture_path: str, rs_lookback: int, args) -> tuple[list[dict], dict]:
    """Offline path: read symbols + index history from a JSON fixture."""
    with open(fixture_path) as f:
        data = json.load(f)

    index_bars = data.get("index", [])
    spy_return = _index_return_from_bars(index_bars, rs_lookback) if index_bars else None

    results = []
    symbols = data.get("symbols", {})
    for symbol, payload in symbols.items():
        bars = payload.get("bars", [])
        record, reason = analyze_symbol(
            bars,
            spy_return,
            name=payload.get("name", symbol),
            sector=payload.get("sector", ""),
            min_price=args.min_price,
            min_dollar_vol=args.min_dollar_vol,
            rs_lookback=rs_lookback,
            min_stop_pct=args.min_stop_pct,
            max_stop_pct=args.max_stop_pct,
        )
        if record is not None:
            record["symbol"] = symbol
            results.append(record)

    meta = {
        "universe_size": len(symbols),
        "spy_return": spy_return,
        "rs_lookback": rs_lookback,
        "source": "fixture",
    }
    return results, meta


def run_live(args) -> tuple[list[dict], dict]:
    """Live path: fetch the index + universe via the shared TradingView data layer.

    Uses the vendored TradingView-backed ``FMPClient`` drop-in from
    ``scripts/lib/tv_client.py`` (same data layer as vcp-screener). The
    FMP-compatible interface is unchanged; ``--api-key`` is accepted for parity
    but the TradingView bridge does not require it.
    """
    import os as _os
    import sys as _sys

    _sys.path.insert(
        0,
        _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", "scripts", "lib"
        ),
    )  # shared TradingView data layer
    from tv_client import FMPClient

    client = FMPClient(api_key=args.api_key)

    # Index proxy: SPY history for the RS benchmark.
    spy_hist = client.get_historical_prices("SPY", days=max(260, args.rs_lookback + 10))
    spy_return = None
    if spy_hist and "historical" in spy_hist:
        spy_return = _index_return_from_bars(spy_hist["historical"], args.rs_lookback)

    # Resolve the universe.
    if args.universe:
        universe = [{"symbol": s, "name": s, "sector": ""} for s in args.universe]
    else:
        constituents = client.get_sp500_constituents() or []
        universe = [
            {
                "symbol": c["symbol"],
                "name": c.get("name", c["symbol"]),
                "sector": c.get("sector", ""),
            }
            for c in constituents
        ]
        if args.max_candidates:
            universe = universe[: args.max_candidates]

    # Sector relative strength (SPDR ETF vs SPY): a short into a leading sector
    # is fighting the group — score_candidate caps it at C.
    from sector_strength import compute_sector_rs

    sector_rs_map = compute_sector_rs(
        client,
        {e.get("sector") for e in universe if e.get("sector")},
        lookback=args.rs_lookback,
        spy_history=(spy_hist or {}).get("historical"),
    )

    results = []
    for entry in universe:
        if client.rate_limit_reached:
            print("WARNING: rate limit reached — stopping early.", file=sys.stderr)
            break
        hist = client.get_historical_prices(entry["symbol"], days=260)
        if not hist or "historical" not in hist:
            continue
        record, _ = analyze_symbol(
            hist["historical"],
            spy_return,
            name=entry["name"],
            sector=entry["sector"],
            min_price=args.min_price,
            min_dollar_vol=args.min_dollar_vol,
            rs_lookback=args.rs_lookback,
            min_stop_pct=args.min_stop_pct,
            max_stop_pct=args.max_stop_pct,
            sector_info=sector_rs_map.get(entry["sector"]),
        )
        if record is not None:
            record["symbol"] = entry["symbol"]
            results.append(record)

    meta = {
        "universe_size": len(universe),
        "spy_return": spy_return,
        "rs_lookback": args.rs_lookback,
        "source": "tradingview",
        "api_calls_made": client.api_calls_made,
    }
    return results, meta


def filter_and_rank(results: list[dict], min_grade: str, top: int) -> list[dict]:
    threshold = GRADE_ORDER.get(min_grade, 2)
    kept = [r for r in results if GRADE_ORDER.get(r["grade"], 0) >= threshold]
    kept.sort(key=lambda r: r["composite_score"], reverse=True)
    return kept[:top] if top else kept


def parse_arguments(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Swing Short Screener — Stage 4 weakness watchlist")
    p.add_argument("--universe", nargs="+", help="Explicit ticker list (overrides S&P 500)")
    p.add_argument("--full-sp500", action="store_true", help="Screen the full S&P 500")
    p.add_argument("--max-candidates", type=int, default=100, help="Cap universe size (live mode)")
    p.add_argument("--api-key", help="FMP API key (else FMP_API_KEY env var)")
    p.add_argument("--fixture", help="Offline JSON fixture path (skips all network calls)")
    p.add_argument(
        "--rs-lookback", type=int, default=63, help="RS lookback in sessions (default 63)"
    )
    p.add_argument(
        "--min-grade", default="C", choices=["A", "B", "C", "D"], help="Minimum grade to keep"
    )
    p.add_argument("--top", type=int, default=25, help="Max candidates in the report (0 = all)")
    p.add_argument(
        "--min-price", type=float, default=DEFAULT_MIN_PRICE, help="Reject sub-price names"
    )
    p.add_argument(
        "--min-dollar-vol",
        type=float,
        default=DEFAULT_MIN_DOLLAR_VOL,
        help="Reject names below this avg daily dollar volume",
    )
    p.add_argument(
        "--min-stop-pct",
        type=float,
        default=DEFAULT_MIN_STOP_PCT,
        help="Reject candidates whose stop distance is below this %% of entry (noise stop)",
    )
    p.add_argument(
        "--max-stop-pct",
        type=float,
        default=DEFAULT_MAX_STOP_PCT,
        help="Reject candidates whose stop distance exceeds this %% of entry (post-crash)",
    )
    p.add_argument("--as-of", help="Date label for the report (YYYY-MM-DD); else today")
    p.add_argument(
        "--output-dir",
        default=_default_output_dir("screeners"),
        help="Output directory (default: $TRADING_DATE_DIR/screeners, else reports/)",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_arguments(argv)

    if args.full_sp500:
        args.max_candidates = 0  # no cap

    if args.fixture:
        results, meta = run_from_fixture(args.fixture, args.rs_lookback, args)
    else:
        try:
            results, meta = run_live(args)
        except ValueError as e:  # missing API key
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    ranked = filter_and_rank(results, args.min_grade, args.top)

    timestamp = args.as_of or datetime.now().strftime("%Y-%m-%d_%H%M%S")
    meta["as_of"] = args.as_of or timestamp

    json_path = generate_json_report(ranked, meta, args.output_dir, timestamp)
    md_path = generate_markdown_report(ranked, meta, args.output_dir, timestamp)

    print(
        f"Screened {meta['universe_size']} symbols → {len(ranked)} candidates "
        f"(min grade {args.min_grade})."
    )
    print(f"JSON: {json_path}")
    print(f"Markdown: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
