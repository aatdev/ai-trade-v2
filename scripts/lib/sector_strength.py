"""Sector relative strength (SPDR Select Sector ETF vs SPY) — no API key.

Maps a GICS sector name to its SPDR Select Sector ETF, fetches both the ETF and
SPY via the shared TradingView data layer, and returns the sector's trailing
return minus SPY's over a lookback. A long in a lagging sector or a short in a
leading sector is fighting the group — the screeners cap such candidates (the
sector-side mirror of the falling-knife / squeeze caps).

Pure except for the injected client (anything with ``get_historical_prices``),
so it tests offline with a fake client.
"""

from __future__ import annotations

# GICS sector name (as served by the constituents feed) → SPDR Select Sector ETF.
# Alternate labels (TradingView / Yahoo style) included so the map is robust to
# whichever feed supplies the `sector` string.
SECTOR_ETF = {
    "Information Technology": "XLK",
    "Technology": "XLK",
    "Health Care": "XLV",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Financial Services": "XLF",
    "Financial": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Cyclical": "XLY",
    "Consumer Staples": "XLP",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
    "Communications": "XLC",
}

# A sector ETF outperforming / underperforming SPY by this many percentage points
# over the lookback counts as leading / lagging.
SECTOR_RS_LEADING = 5.0
SECTOR_RS_LAGGING = -5.0

_LOOKBACK_FETCH_DAYS = 260  # enough history for the lookback plus a margin


def _closes(history: list[dict] | None) -> list[float]:
    """Most-recent-first close series from FMP-shaped bars."""
    out = []
    for b in history or []:
        c = b.get("close", b.get("adjClose"))
        if c is not None:
            out.append(float(c))
    return out


def _return_pct(closes: list[float], lookback: int) -> float | None:
    """Percent return over `lookback` sessions. Closes are most-recent-first."""
    if not closes or len(closes) <= lookback:
        return None
    latest, past = closes[0], closes[lookback]
    if not past:
        return None
    return (latest - past) / past * 100.0


def classify_leadership(sector_rs: float | None) -> str | None:
    """leading / lagging / inline, or None when sector_rs is unavailable."""
    if sector_rs is None:
        return None
    if sector_rs >= SECTOR_RS_LEADING:
        return "leading"
    if sector_rs <= SECTOR_RS_LAGGING:
        return "lagging"
    return "inline"


def compute_sector_rs(
    client,
    sectors,
    lookback: int = 63,
    spy_history: list[dict] | None = None,
) -> dict[str, dict]:
    """Map each sector name to its leadership vs SPY over `lookback` sessions.

    Returns ``{sector_name: {"etf", "sector_rs", "leadership"}}``. ``sector_rs``
    is the sector ETF return minus SPY return (percentage points); ``leadership``
    is leading / lagging / inline, or None when the ETF is unknown or data is
    missing (fail-open — no cap). Pass a reusable ``spy_history`` (most-recent-
    first bars) the caller already fetched to avoid a duplicate SPY request.
    """
    if spy_history is None:
        spy_data = client.get_historical_prices(
            "SPY", days=max(lookback + 10, _LOOKBACK_FETCH_DAYS)
        )
        spy_history = (spy_data or {}).get("historical") or []
    spy_ret = _return_pct(_closes(spy_history), lookback)

    out: dict[str, dict] = {}
    etf_return_cache: dict[str, float | None] = {}
    for sector in {s for s in sectors if s}:
        etf = SECTOR_ETF.get(sector)
        if etf is None or spy_ret is None:
            out[sector] = {"etf": etf, "sector_rs": None, "leadership": None}
            continue
        if etf not in etf_return_cache:
            data = client.get_historical_prices(etf, days=max(lookback + 10, _LOOKBACK_FETCH_DAYS))
            etf_return_cache[etf] = _return_pct(_closes((data or {}).get("historical")), lookback)
        sector_ret = etf_return_cache[etf]
        if sector_ret is None:
            out[sector] = {"etf": etf, "sector_rs": None, "leadership": None}
            continue
        rs = round(sector_ret - spy_ret, 2)
        out[sector] = {"etf": etf, "sector_rs": rs, "leadership": classify_leadership(rs)}
    return out
