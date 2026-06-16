"""Fundamental quality-floor gate for the breakout trade planner.

A *soft* gate: it drops a long candidate only on clear fundamental
deterioration — the most recent quarter is unprofitable, or BOTH EPS and
revenue are shrinking year-over-year. Everything else is kept and annotated
with CANSLIM C/A context (quarterly EPS/revenue growth, annual EPS CAGR) so
the watchlist can use it as a tie-breaker. Ranking and sizing stay driven by
the VCP composite score — this gate never reshapes them.

Why a floor, not full CANSLIM: the universe is S&P 500 large-caps, many of
which are quality leaders growing EPS below O'Neil's 18%/25% momentum
thresholds. A hard CANSLIM gate would discard them; the floor only removes
names actively losing money or contracting on both top and bottom line.

Data source (no API key): quarterly/annual income statements come from the
shared TradingView data layer (``get_income_statement``); the growth math
reuses the canslim-screener's pure C/A calculators. Both are imported lazily
inside ``fetch_fundamentals_map`` so the planner stays importable — and the
gate simply degrades to 'unknown' (fail-open) — when those siblings are absent.
"""

from __future__ import annotations

from collections.abc import Iterable

GATE_PASS = "pass"
GATE_BLOCKED = "blocked"
GATE_UNKNOWN = "unknown"

_NONE_FIELDS = {
    "eps_growth_yoy": None,
    "revenue_growth_yoy": None,
    "latest_eps": None,
    "c_score": None,
    "a_score": None,
}


class FundamentalFetchError(Exception):
    """Raised when fundamentals cannot be fetched/computed for the batch."""


def classify_fundamentals(quarterly: dict | None, annual: dict | None) -> dict:
    """Soft quality-floor verdict from CANSLIM C/A calculator outputs.

    ``quarterly`` is a ``calculate_quarterly_growth`` result, ``annual`` a
    ``calculate_annual_growth`` result (either may be ``None``). Returns a flat
    annotation dict:

    - ``fundamental_gate``: ``blocked`` only on clear decay (latest-quarter EPS
      < 0, or EPS-YoY < 0 AND revenue-YoY < 0); ``unknown`` when quarterly
      growth could not be computed (missing/short data — fail-open); else
      ``pass``.
    - ``fundamental_reason``: why blocked, else ``None``.
    - ``eps_growth_yoy`` / ``revenue_growth_yoy`` / ``latest_eps``: raw figures.
    - ``c_score`` / ``a_score``: CANSLIM C/A component scores (for ranking).
    """
    if not quarterly or quarterly.get("error") or quarterly.get("latest_qtr_eps_growth") is None:
        return {
            "fundamental_gate": GATE_UNKNOWN,
            "fundamental_reason": "fundamentals unavailable",
            **_NONE_FIELDS,
        }

    eps_yoy = quarterly.get("latest_qtr_eps_growth")
    rev_yoy = quarterly.get("latest_qtr_revenue_growth")
    latest_eps = quarterly.get("latest_eps")
    fields = {
        "eps_growth_yoy": eps_yoy,
        "revenue_growth_yoy": rev_yoy,
        "latest_eps": latest_eps,
        "c_score": quarterly.get("score"),
        "a_score": (annual or {}).get("score"),
    }

    if latest_eps is not None and latest_eps < 0:
        return {
            "fundamental_gate": GATE_BLOCKED,
            "fundamental_reason": f"latest-quarter EPS negative ({latest_eps})",
            **fields,
        }
    if eps_yoy < 0 and rev_yoy is not None and rev_yoy < 0:
        return {
            "fundamental_gate": GATE_BLOCKED,
            "fundamental_reason": (
                f"EPS YoY {eps_yoy:+.1f}% and revenue YoY {rev_yoy:+.1f}% both negative"
            ),
            **fields,
        }
    return {"fundamental_gate": GATE_PASS, "fundamental_reason": None, **fields}


def build_fundamental_fields(
    symbol: str,
    fundamentals_map: dict[str, dict] | None,
    *,
    fetch_failed: bool = False,
) -> dict:
    """Fundamental annotation for one plan (mirrors earnings_gate.build_gate_fields).

    ``unknown`` (fail-open) when the batch fetch failed or the symbol is absent;
    otherwise the floor verdict from its computed C/A growth.
    """
    if fetch_failed:
        return {
            "fundamental_gate": GATE_UNKNOWN,
            "fundamental_reason": "fundamentals fetch failed",
            **_NONE_FIELDS,
        }
    fmap = fundamentals_map or {}
    entry = fmap.get(symbol.upper()) or fmap.get(symbol)
    if not entry:
        return classify_fundamentals(None, None)
    return classify_fundamentals(entry.get("quarterly"), entry.get("annual"))


def fetch_fundamentals_map(symbols: Iterable[str], *, client=None) -> dict[str, dict]:
    """Map each symbol to computed CANSLIM C/A growth from TradingView fundamentals.

    Returns ``{SYMBOL: {"quarterly": <C result>, "annual": <A result>}}``. Uses
    the shared TradingView data layer (no API key) and the canslim-screener's
    pure growth calculators, both imported lazily here. A symbol whose
    statements are missing maps to ``None`` results (-> 'unknown', fail-open).

    Raises:
        FundamentalFetchError: when the data layer / calculators cannot be
            loaded or a client cannot be built — the caller degrades the whole
            batch to 'unknown'.
    """
    wanted = sorted({s.upper() for s in symbols if s})
    if not wanted:
        return {}

    calc_quarterly, calc_annual = _load_calculators()
    client = client or _build_tv_client()

    out: dict[str, dict] = {}
    for sym in wanted:
        try:
            quarterly_stmts = client.get_income_statement(sym, "quarter", 8)
            annual_stmts = client.get_income_statement(sym, "annual", 5)
        except Exception:  # noqa: BLE001 - one bad symbol must not sink the batch
            out[sym] = {"quarterly": None, "annual": None}
            continue
        out[sym] = {
            "quarterly": calc_quarterly(quarterly_stmts) if quarterly_stmts else None,
            "annual": calc_annual(annual_stmts) if annual_stmts else None,
        }
    return out


def _load_calculators():
    """Lazily import the canslim-screener's pure growth calculators."""
    import sys
    from pathlib import Path

    canslim_scripts = Path(__file__).resolve().parents[2] / "canslim-screener" / "scripts"
    if str(canslim_scripts) not in sys.path:
        sys.path.insert(0, str(canslim_scripts))
    try:
        from calculators.earnings_calculator import calculate_quarterly_growth
        from calculators.growth_calculator import calculate_annual_growth
    except ImportError as exc:  # pragma: no cover - environment guard
        raise FundamentalFetchError(
            f"canslim-screener growth calculators unavailable: {exc}"
        ) from exc
    return calculate_quarterly_growth, calculate_annual_growth


def _build_tv_client():
    """Lazily build the shared TradingView data-layer client (no API key)."""
    import sys
    from pathlib import Path

    lib_dir = Path(__file__).resolve().parents[3] / "scripts" / "lib"
    if str(lib_dir) not in sys.path:
        sys.path.insert(0, str(lib_dir))
    try:
        from tv_client import FMPClient
    except ImportError as exc:  # pragma: no cover - environment guard
        raise FundamentalFetchError(f"shared TradingView data layer unavailable: {exc}") from exc
    return FMPClient(api_key=None)
