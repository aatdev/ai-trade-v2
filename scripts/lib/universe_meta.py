"""Sidecar metadata for the liquid VCP/short universe (sector, name, market cap).

``scripts/build_vcp_universe.py`` writes ``vcp_universe.txt`` (bare tickers,
consumed by the scheduler / UI) plus ``vcp_universe_meta.json`` next to it.
Screeners running on a custom ``--universe`` have no constituents feed, so
without this sidecar every candidate's sector was "Unknown" and the sector-RS
gate never fired. Sector names follow the TradingView scanner taxonomy
(mapped to SPDR ETFs in ``sector_strength.SECTOR_ETF``).
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_META_FILE = Path(__file__).resolve().parent / "data" / "vcp_universe_meta.json"


def load_universe_meta(path: Path | str | None = None) -> dict[str, dict]:
    """{TICKER: {"sector", "name", "market_cap"}}; {} when absent/unreadable."""
    p = Path(path) if path else DEFAULT_META_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    tickers = data.get("tickers") if isinstance(data, dict) else None
    if not isinstance(tickers, dict):
        return {}
    return {str(k).upper(): v for k, v in tickers.items() if isinstance(v, dict)}


def sector_for(ticker: str, meta: dict[str, dict]) -> str | None:
    sector = (meta.get(str(ticker).upper()) or {}).get("sector")
    return sector or None


def name_for(ticker: str, meta: dict[str, dict]) -> str | None:
    name = (meta.get(str(ticker).upper()) or {}).get("name")
    return name or None


def maps_for_universe(
    symbols: list[str], meta: dict[str, dict]
) -> tuple[dict[str, str], dict[str, str], dict[str, float]]:
    """(sector_map, name_map, market_cap_map) for the symbols the sidecar knows."""
    sectors: dict[str, str] = {}
    names: dict[str, str] = {}
    caps: dict[str, float] = {}
    for sym in symbols:
        m = meta.get(str(sym).upper()) or {}
        if m.get("sector"):
            sectors[sym] = m["sector"]
        if m.get("name"):
            names[sym] = m["name"]
        if isinstance(m.get("market_cap"), (int, float)):
            caps[sym] = float(m["market_cap"])
    return sectors, names, caps
