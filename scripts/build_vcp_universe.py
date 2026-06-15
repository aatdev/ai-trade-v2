#!/usr/bin/env python3
"""Build the liquid NASDAQ+NYSE ticker universe for the VCP screener.

The default VCP-screener universe is the S&P 500 (503 large-caps) — a poor pond
for Minervini VCP setups, which cluster in faster-moving mid/small-caps. This
generator widens the pool to the top-N most liquid US common stocks (by market
cap), applying price / volume / market-cap floors so only tradeable names land
in the list.

It reuses the tradingview-screener skill's payload builder + scanner client, but
issues a single `range: [0, N]` request directly so it can exceed that CLI's
500-row cap (the public scanner.tradingview.com endpoint has no such limit).

Output: one bare ticker per line (exchange prefix stripped), `#` comments
ignored by consumers. The scheduler's evening-prep slot feeds this file to
`screen_vcp.py --universe …` when present, else falls back to the S&P 500.

Regenerate periodically (constituents/liquidity drift):

    python3 scripts/build_vcp_universe.py                       # default floors (~1194 names)
    python3 scripts/build_vcp_universe.py --min-avg-volume 600K # looser, ~1500 names
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TV_SCREENER_DIR = PROJECT_ROOT / "skills" / "tradingview-screener" / "scripts"
sys.path.insert(0, str(TV_SCREENER_DIR))

import run_tv_screener as tv  # noqa: E402

DEFAULT_OUTPUT = PROJECT_ROOT / "scripts" / "lib" / "data" / "vcp_universe.txt"


def build_universe(
    *,
    limit: int,
    exchanges: list[str],
    min_price: float,
    min_avg_volume: str,
    min_market_cap: str,
    timeout: int = 30,
) -> tuple[list[str], int]:
    """Return (bare tickers sorted by market cap desc, total matches)."""
    filters = [
        f"close>{min_price}",
        f"avg_volume_30d>{min_avg_volume}",
        f"market_cap>{min_market_cap}",
    ]
    payload = tv.build_payload(
        filters,
        exchanges=exchanges,
        columns="overview",
        sort="-market_cap_basic",
        limit=limit,
        market="america",
        universe="common",
    )
    response = tv.run_scan(payload, "america", timeout=timeout)
    total = int(response.get("totalCount", 0))

    seen: set[str] = set()
    tickers: list[str] = []
    for item in response.get("data", []):
        raw = item.get("s", "")  # e.g. "NASDAQ:AAPL"
        ticker = raw.split(":", 1)[1] if ":" in raw else raw
        if ticker and ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1500, help="Max tickers (default: 1500)")
    parser.add_argument(
        "--exchanges",
        default="NASDAQ,NYSE",
        help="Comma-separated exchanges (default: NASDAQ,NYSE)",
    )
    parser.add_argument(
        "--min-price", type=float, default=10.0, help="Min close price (default: 10)"
    )
    parser.add_argument("--min-avg-volume", default="1M", help="Min 30d avg volume (default: 1M)")
    parser.add_argument("--min-market-cap", default="1B", help="Min market cap (default: 1B)")
    parser.add_argument(
        "--output", default=str(DEFAULT_OUTPUT), help=f"Output file (default: {DEFAULT_OUTPUT})"
    )
    args = parser.parse_args()

    exchanges = [e.strip() for e in args.exchanges.split(",") if e.strip()]
    print(
        f"Fetching top {args.limit} {'+'.join(exchanges)} common stocks "
        f"(close>{args.min_price}, avg_vol>{args.min_avg_volume}, mktcap>{args.min_market_cap})…",
        flush=True,
    )
    try:
        tickers, total = build_universe(
            limit=args.limit,
            exchanges=exchanges,
            min_price=args.min_price,
            min_avg_volume=args.min_avg_volume,
            min_market_cap=args.min_market_cap,
        )
    except tv.ScanError as exc:
        print(f"Error: scanner request failed: {exc}", file=sys.stderr)
        return 1

    if not tickers:
        print(
            "Error: scanner returned 0 tickers — refusing to write an empty universe.",
            file=sys.stderr,
        )
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = [
        "# VCP screener universe — liquid NASDAQ+NYSE common stocks, by market cap desc.",
        f"# Generated: {generated}",
        f"# Filters: exchanges={'+'.join(exchanges)} close>{args.min_price} "
        f"avg_volume_30d>{args.min_avg_volume} market_cap>{args.min_market_cap}",
        f"# Tickers: {len(tickers)} (of {total} total matches)",
        "# Regenerate: python3 scripts/build_vcp_universe.py",
    ]
    out_path.write_text("\n".join(header + tickers) + "\n", encoding="utf-8")

    print(f"Wrote {len(tickers)} tickers (of {total} matches) → {out_path}")
    if total > len(tickers):
        print(f"  Note: {total} stocks matched; capped to top {len(tickers)} by market cap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
