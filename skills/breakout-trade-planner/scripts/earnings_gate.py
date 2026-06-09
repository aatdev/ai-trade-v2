"""Earnings-date gate for the breakout trade planner.

Maps each candidate symbol to its next confirmed earnings date and
annotates/blocks plans whose entry would sit within N trading days of the
report — medium-term swing positions should not be opened right in front of
earnings.

Data source: the public TradingView scanner (``scanner.tradingview.com``, the
same endpoint the tradingview-screener skill uses) — one POST for the whole
batch, **no API key required**. The scanner stores a single
``earnings_release_next_date`` per symbol, so unlike a calendar-window query
there is no lookahead limit: the next report is visible however far out it is.

Stdlib-only (urllib). Release timestamps are converted to the US-Eastern
calendar date so an after-market-close report keeps its actual trading date.
Trading-day distance counts weekdays and ignores exchange holidays; near a
holiday this overestimates by one day, which errs on the blocking side.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SCANNER_URL = "https://scanner.tradingview.com/america/scan"
_US_EASTERN = ZoneInfo("America/New_York")

GATE_PASS = "pass"
GATE_BLOCKED = "blocked"
GATE_UNKNOWN = "unknown"


class EarningsFetchError(Exception):
    """Raised when the earnings data cannot be fetched or parsed."""


def _default_fetcher(url: str, payload: dict) -> object:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (claude-trading-skills earnings-gate)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise EarningsFetchError(f"TradingView scanner request failed: {exc}") from exc


def build_scan_payload(symbols: Iterable[str]) -> dict:
    """Scanner payload: next-earnings timestamps for an explicit ticker list."""
    wanted = sorted({s.upper() for s in symbols if s})
    return {
        "filter": [
            {"left": "name", "operation": "in_range", "right": wanted},
            {"left": "is_primary", "operation": "equal", "right": True},
        ],
        "columns": ["name", "earnings_release_next_date"],
        "markets": ["america"],
        "range": [0, max(len(wanted) * 2, 10)],
    }


def fetch_earnings_map(
    symbols: Iterable[str],
    *,
    today: date | None = None,
    fetcher: Callable[[str, dict], object] | None = None,
) -> dict[str, str]:
    """Map each requested symbol to its next earnings date.

    Returns ``{SYMBOL: "YYYY-MM-DD"}`` (US-Eastern calendar date). Symbols with
    no upcoming report known to TradingView are absent from the map.

    Raises:
        EarningsFetchError: On network failure or an unexpected scanner
            response shape.
    """
    today = today or date.today()
    wanted = {s.upper() for s in symbols if s}
    if not wanted:
        return {}

    payload = build_scan_payload(wanted)
    response = (fetcher or _default_fetcher)(SCANNER_URL, payload)

    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise EarningsFetchError(
            f"Unexpected TradingView scanner response shape: {type(response).__name__}"
        )

    earnings: dict[str, str] = {}
    for row in response["data"]:
        if not isinstance(row, dict):
            continue
        values = row.get("d") or []
        if len(values) < 2:
            continue
        symbol = str(values[0] or "").upper()
        ts = values[1]
        if symbol not in wanted or not ts:
            continue
        try:
            event = (
                datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone(_US_EASTERN).date()
            )
        except (TypeError, ValueError, OSError):
            continue
        if event < today:
            continue
        iso = event.isoformat()
        current = earnings.get(symbol)
        if current is None or iso < current:
            earnings[symbol] = iso
    return earnings


def trading_days_until(target: str, today: date) -> int:
    """Count weekdays in ``(today, target]``; same-day or past dates -> 0."""
    event = datetime.strptime(target, "%Y-%m-%d").date()
    days = 0
    cur = today
    while cur < event:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def build_gate_fields(
    symbol: str,
    earnings_map: dict[str, str],
    gate_days: int,
    today: date,
    *,
    fetch_failed: bool = False,
) -> dict:
    """Earnings annotation for one plan.

    ``blocked`` when the next report is within ``gate_days`` trading days
    (inclusive); ``pass`` when it is further out or unknown to the scanner;
    ``unknown`` when the scanner could not be reached (plan stays live but
    flagged — verify the date manually before entry).
    """
    if fetch_failed:
        return {"earnings_date": None, "days_to_earnings": None, "earnings_gate": GATE_UNKNOWN}

    earnings_date = earnings_map.get(symbol.upper())
    if earnings_date is None:
        return {"earnings_date": None, "days_to_earnings": None, "earnings_gate": GATE_PASS}

    days = trading_days_until(earnings_date, today)
    gate = GATE_BLOCKED if days <= gate_days else GATE_PASS
    return {"earnings_date": earnings_date, "days_to_earnings": days, "earnings_gate": gate}
