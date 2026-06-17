"""Trader Memory Core — skill output → thesis conversion (register only).

Each adapter transforms a skill's JSON output into a thesis_data dict
suitable for thesis_store.register().
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Allow imports from sibling modules
sys.path.insert(0, str(Path(__file__).resolve().parent))

import signals_md  # noqa: E402
import thesis_store  # noqa: E402

logger = logging.getLogger(__name__)

# -- Adapter registry ---------------------------------------------------------

_ADAPTERS: dict[str, callable] = {}


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


def _adapter(source_name: str):
    """Decorator to register an ingest adapter."""

    def wrapper(fn):
        _ADAPTERS[source_name] = fn
        return fn

    return wrapper


# -- Individual Adapters ------------------------------------------------------


@_adapter("kanchi-dividend-sop")
def ingest_kanchi(record: dict, input_file: str) -> dict:
    """Transform kanchi-dividend-sop output into thesis data."""
    ticker = record.get("ticker")
    if not ticker:
        raise ValueError("Missing required field 'ticker' in kanchi record")

    thesis_data = {
        "ticker": ticker,
        "thesis_type": "dividend_income",
        "thesis_statement": (f"{ticker} dividend income thesis from Kanchi screening"),
        "setup_type": record.get("setup_type", "kanchi_5step"),
        "_register_reason": "screened by kanchi-dividend-sop",
        "entry": {},
        "exit": {},
        "origin": {
            "skill": "kanchi-dividend-sop",
            "output_file": input_file,
            "screening_grade": record.get("grade"),
            "screening_score": record.get("score"),
            "raw_provenance": {k: v for k, v in record.items()},
        },
    }

    # Map buy_target_price → entry.target_price
    if "buy_target_price" in record:
        thesis_data["entry"]["target_price"] = record["buy_target_price"]

    # Map stop_loss if present
    if "stop_loss" in record:
        thesis_data["exit"]["stop_loss"] = record["stop_loss"]

    # Copy evidence fields
    if "evidence" in record:
        thesis_data["evidence"] = record["evidence"]

    if "kill_criteria" in record:
        thesis_data["kill_criteria"] = record["kill_criteria"]

    if "catalyst" in record:
        thesis_data["catalyst"] = record["catalyst"]

    return thesis_data


_VALID_THESIS_TYPES = (
    "dividend_income",
    "growth_momentum",
    "mean_reversion",
    "earnings_drift",
    "pivot_breakout",
)


def _manual_source_date(record: dict) -> str | None:
    """Date-only (YYYY-MM-DD) source date for a manual record.

    register() builds the IDEA history stamp as
    ``f"{_source_date}T00:00:00+00:00"``, so _source_date MUST be date-only —
    a full ISO entry_date would yield a broken double-suffixed timestamp.
    Mirrors _extract_source_date()'s ``[:10]`` slice; handles missing/typed
    values defensively.
    """
    for key in ("entry_date", "as_of"):
        val = record.get(key)
        if val and isinstance(val, str):
            return val[:10]  # "YYYY-MM-DD" or "YYYY-MM-DDTHH:..."
    return None


@_adapter("manual")
def ingest_manual(record: dict, input_file: str) -> dict:
    """Transform a hand-entered (free-form) position record into thesis data.

    For trades that did not come from a screener adapter (fractional-share
    brokers, manual journaling). Creates an IDEA thesis only — exactly like
    every other adapter. An already-open broker position reaches ACTIVE via
    the explicit CLI sequence: transition → open-position (both accept
    --event-date for backdating). No status mutation here.

    Required: ticker, thesis_statement, thesis_type.
    Optional: entry_price, entry_date, shares, stop_price/stop_loss,
    target_price/take_profit, setup_type, notes. entry_price/entry_date/shares
    are recorded in origin.raw_provenance only — the authoritative
    entry.actual_* / position.shares are set later by `open-position`.
    """
    ticker = record.get("ticker")
    if not ticker:
        raise ValueError("Missing required field 'ticker' in manual record")

    thesis_statement = record.get("thesis_statement")
    if not thesis_statement:
        raise ValueError(f"Missing required field 'thesis_statement' in manual record for {ticker}")

    thesis_type = record.get("thesis_type")
    if thesis_type not in _VALID_THESIS_TYPES:
        raise ValueError(
            f"Invalid or missing 'thesis_type' for {ticker}: {thesis_type!r}. "
            f"Expected one of {', '.join(_VALID_THESIS_TYPES)}"
        )

    thesis_data = {
        "ticker": ticker,
        "thesis_type": thesis_type,
        "thesis_statement": thesis_statement,
        "setup_type": record.get("setup_type"),
        "_register_reason": "manually entered",
        "entry": {},
        "exit": {},
        "origin": {
            "skill": "manual",
            "output_file": input_file,
            "raw_provenance": {k: v for k, v in record.items()},
        },
    }

    # Optional risk levels → existing schema fields only (entry/exit are
    # additionalProperties:false). entry_price/entry_date/shares intentionally
    # NOT mapped to entry.* — they live in origin.raw_provenance; the
    # authoritative values are set by `open-position`.
    stop = record.get("stop_loss", record.get("stop_price"))
    if stop is not None:
        thesis_data["exit"]["stop_loss"] = stop
    target = record.get("take_profit", record.get("target_price"))
    if target is not None:
        thesis_data["exit"]["take_profit"] = target

    source_date = _manual_source_date(record)
    if source_date:
        thesis_data["_source_date"] = source_date

    return thesis_data


@_adapter("earnings-trade-analyzer")
def ingest_earnings(record: dict, input_file: str) -> dict:
    """Transform earnings-trade-analyzer result into thesis data."""
    ticker = record.get("symbol")
    if not ticker:
        raise ValueError("Missing required field 'symbol' in earnings record")

    thesis_data = {
        "ticker": ticker,
        "thesis_type": "earnings_drift",
        "thesis_statement": (
            f"{ticker} earnings drift thesis — "
            f"grade {record.get('grade', '?')}, "
            f"gap {record.get('gap_pct', '?')}%"
        ),
        "_register_reason": "screened by earnings-trade-analyzer",
        "entry": {},
        "exit": {},
        "market_context": {},
        "origin": {
            "skill": "earnings-trade-analyzer",
            "output_file": input_file,
            "screening_grade": record.get("grade"),
            "screening_score": record.get("composite_score"),
            "raw_provenance": {k: v for k, v in record.items()},
        },
    }

    if "sector" in record:
        thesis_data["market_context"]["sector"] = record["sector"]

    return thesis_data


@_adapter("vcp-screener")
def ingest_vcp(record: dict, input_file: str) -> dict:
    """Transform vcp-screener result into thesis data."""
    ticker = record.get("symbol")
    if not ticker:
        raise ValueError("Missing required field 'symbol' in VCP record")

    thesis_data = {
        "ticker": ticker,
        "thesis_type": "pivot_breakout",
        "thesis_statement": (
            f"{ticker} VCP pivot breakout — "
            f"distance from pivot {record.get('distance_from_pivot_pct', '?')}%"
        ),
        "_register_reason": "screened by vcp-screener",
        "entry": {},
        "exit": {},
        "origin": {
            "skill": "vcp-screener",
            "output_file": input_file,
            "screening_grade": record.get("rating"),
            "screening_score": record.get("composite_score"),
            "raw_provenance": {k: v for k, v in record.items()},
        },
    }

    return thesis_data


@_adapter("swing-short-screener")
def ingest_swing_short(record: dict, input_file: str) -> dict:
    """Transform a swing-short-screener candidate into SHORT thesis data.

    Levels map straight from the screener: trade_levels.entry → entry
    target, .stop → stop loss, .target_2r → take profit. ``side: short`` is
    what makes the heat ledger count the risk as (stop − entry) × shares.
    """
    ticker = record.get("symbol")
    if not ticker:
        raise ValueError("Missing required field 'symbol' in swing-short record")

    levels = record.get("trade_levels") or {}
    thesis_data = {
        "ticker": ticker,
        "side": "short",
        "thesis_type": "pivot_breakout",
        "thesis_statement": (
            f"{ticker} Stage 4 breakdown SHORT — grade {record.get('grade', '?')}, "
            "stop above the last lower high"
        ),
        "_register_reason": "screened by swing-short-screener",
        "entry": {},
        "exit": {},
        "origin": {
            "skill": "swing-short-screener",
            "output_file": input_file,
            "screening_grade": record.get("grade"),
            "screening_score": record.get("composite_score"),
            "raw_provenance": {k: v for k, v in record.items()},
        },
    }
    if levels.get("entry") is not None:
        thesis_data["entry"]["target_price"] = levels["entry"]
    if levels.get("stop") is not None:
        thesis_data["exit"]["stop_loss"] = levels["stop"]
    if levels.get("target_2r") is not None:
        thesis_data["exit"]["take_profit"] = levels["target_2r"]

    return thesis_data


@_adapter("ticker-analysis")
def ingest_signal(record: dict, input_file: str) -> dict:
    """Transform a ticker-analysis signal into thesis data — the signal → thesis path.

    The record is a parsed ``signals.md`` block (see ``signals_md.parse_signals_md``)
    or an equivalent signal-JSON object: direction + Trigger/Stop/T1-T3. It maps to
    an ``IDEA`` thesis. ``exit.take_profit`` takes T1 (the first/conservative target,
    mirroring swing-short's ``target_2r`` mapping — the schema has a single
    ``take_profit``); T2/T3, the entry range and the report link stay in
    ``origin.raw_provenance``. ``thesis_type`` defaults to ``pivot_breakout`` (a
    trigger-cross entry is breakout-shaped); a valid explicit ``thesis_type`` wins.

    Like every adapter this creates an IDEA thesis only. When a non-terminal
    same-side thesis already exists for the ticker, ``ingest()`` reuses it and
    refreshes its levels from this fresher signal — so a re-analysis updates the
    live thesis instead of duplicating it.
    """
    ticker = record.get("ticker")
    if not ticker:
        raise ValueError("Missing required field 'ticker' in signal record")

    side = str(record.get("side") or record.get("direction") or "").lower()
    if side not in ("long", "short"):
        raise ValueError(f"Signal for {ticker} needs a long/short direction, got {side!r}")

    trigger = record.get("trigger")
    stop = record.get("stop")
    if trigger is None or stop is None:
        raise ValueError(
            f"Signal for {ticker} needs both a trigger and a stop "
            f"(got trigger={trigger!r}, stop={stop!r})"
        )

    thesis_type = record.get("thesis_type")
    if thesis_type not in _VALID_THESIS_TYPES:
        thesis_type = "pivot_breakout"

    t1 = record.get("t1")
    date = record.get("date")
    cmp_word = "above" if side == "long" else "below"
    statement = (
        f"{ticker} {side} from ticker-analysis signal"
        + (f" ({date})" if date else "")
        + f" — trigger ${trigger:g}, stop ${stop:g}"
        + (f", T1 ${t1:g}" if t1 is not None else "")
    )

    thesis_data = {
        "ticker": ticker,
        "side": side,
        "thesis_type": thesis_type,
        "thesis_statement": statement,
        "setup_type": record.get("setup_type") or "ticker_analysis_signal",
        "_register_reason": "from ticker-analysis signal",
        "entry": {
            "target_price": trigger,
            "conditions": [f"close {cmp_word} ${trigger:g}"],
        },
        "exit": {"stop_loss": stop},
        "origin": {
            "skill": "ticker-analysis",
            "output_file": input_file,
            # Full signal record (T2/T3, entry range, report link) preserved.
            "raw_provenance": {k: v for k, v in record.items()},
        },
    }
    if t1 is not None:
        thesis_data["exit"]["take_profit"] = t1
    if isinstance(date, str) and date:
        # Stamp the thesis_id / created_at / history at the signal date.
        thesis_data["_source_date"] = date[:10]

    return thesis_data


@_adapter("pead-screener")
def ingest_pead(record: dict, input_file: str) -> dict:
    """Transform pead-screener result into thesis data."""
    ticker = record.get("symbol")
    if not ticker:
        raise ValueError("Missing required field 'symbol' in PEAD record")

    thesis_data = {
        "ticker": ticker,
        "thesis_type": "earnings_drift",
        "thesis_statement": (f"{ticker} PEAD earnings drift — status {record.get('status', '?')}"),
        "_register_reason": "screened by pead-screener",
        "entry": {},
        "exit": {},
        "origin": {
            "skill": "pead-screener",
            "output_file": input_file,
            "screening_grade": record.get("grade"),
            "screening_score": record.get("composite_score"),
            "raw_provenance": {k: v for k, v in record.items()},
        },
    }

    if "entry_price" in record:
        thesis_data["entry"]["target_price"] = record["entry_price"]
    if "stop_loss" in record:
        thesis_data["exit"]["stop_loss"] = record["stop_loss"]

    return thesis_data


@_adapter("canslim-screener")
def ingest_canslim(record: dict, input_file: str) -> dict:
    """Transform canslim-screener result into thesis data."""
    ticker = record.get("symbol")
    if not ticker:
        raise ValueError("Missing required field 'symbol' in CANSLIM record")

    thesis_data = {
        "ticker": ticker,
        "thesis_type": "growth_momentum",
        "thesis_statement": (
            f"{ticker} CANSLIM growth momentum — rating {record.get('rating', '?')}"
        ),
        "_register_reason": "screened by canslim-screener",
        "entry": {},
        "exit": {},
        "origin": {
            "skill": "canslim-screener",
            "output_file": input_file,
            "screening_grade": record.get("rating"),
            "screening_score": record.get("composite_score"),
            "raw_provenance": {k: v for k, v in record.items()},
        },
    }

    return thesis_data


@_adapter("edge-candidate-agent")
def ingest_edge(record: dict, input_file: str) -> dict | None:
    """Transform edge-candidate-agent ticket into thesis data.

    Phase 1 constraints:
    - research_only=True tickets are skipped (returns None)
    - Tickets without a single ticker/symbol are skipped (returns None)
    """
    # Check research_only
    if record.get("research_only", False):
        logger.warning(
            "Skipping edge ticket %s: research_only=True",
            record.get("id", "unknown"),
        )
        return None

    # Extract ticker — check multiple possible fields
    ticker = record.get("ticker") or record.get("symbol")
    if not ticker:
        # Check if it's a market basket or multi-ticker
        universe = record.get("universe")
        if isinstance(universe, str) and universe.upper() == "MARKET_BASKET":
            logger.warning(
                "Skipping edge ticket %s: MARKET_BASKET (no single ticker)",
                record.get("id", "unknown"),
            )
            return None
        logger.warning(
            "Skipping edge ticket %s: no single ticker/symbol found",
            record.get("id", "unknown"),
        )
        return None

    # Determine thesis_type from hypothesis_type / entry_family
    entry_family = record.get("entry_family", "")
    hypothesis_type = record.get("hypothesis_type", "")

    if "breakout" in entry_family or "breakout" in hypothesis_type:
        thesis_type = "pivot_breakout"
    elif "gap" in entry_family or "drift" in hypothesis_type:
        thesis_type = "earnings_drift"
    elif "reversion" in hypothesis_type or "mean" in hypothesis_type:
        thesis_type = "mean_reversion"
    elif "momentum" in hypothesis_type or "growth" in hypothesis_type:
        thesis_type = "growth_momentum"
    else:
        thesis_type = "pivot_breakout"  # default for edge strategies

    thesis_data = {
        "ticker": ticker,
        "thesis_type": thesis_type,
        "thesis_statement": (
            f"{ticker} edge strategy — {record.get('name', hypothesis_type or 'unknown')}"
        ),
        "mechanism_tag": record.get("mechanism_tag"),
        "_register_reason": "screened by edge-candidate-agent",
        "entry": {},
        "exit": {},
        "origin": {
            "skill": "edge-candidate-agent",
            "output_file": input_file,
            "screening_grade": None,
            "screening_score": None,
            "raw_provenance": {k: v for k, v in record.items()},
        },
    }

    # Map entry/exit — ticket schema uses top-level entry/exit (not signals.entry)
    entry_data = record.get("entry", {})
    exit_data = record.get("exit", {})
    if isinstance(entry_data, dict):
        if "conditions" in entry_data:
            thesis_data["entry"]["conditions"] = entry_data["conditions"]
        if "target_price" in entry_data:
            thesis_data["entry"]["target_price"] = entry_data["target_price"]
    if isinstance(exit_data, dict):
        if "stop_loss" in exit_data:
            thesis_data["exit"]["stop_loss"] = exit_data["stop_loss"]
        if "stop_loss_pct" in exit_data:
            thesis_data["exit"]["stop_loss_pct"] = exit_data["stop_loss_pct"]
        if "take_profit_rr" in exit_data:
            thesis_data["exit"]["take_profit_rr"] = exit_data["take_profit_rr"]
        if "time_stop_days" in exit_data:
            thesis_data["exit"]["time_stop_days"] = exit_data["time_stop_days"]

    return thesis_data


# -- Plan enrichment helpers --------------------------------------------------


def _build_plan_index(plan_file: str) -> dict[str, dict]:
    """Return {TICKER: trade_plan_dict} from breakout_trade_plan JSON."""
    data = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    result = {}
    for order in (data or {}).get("actionable_orders") or []:
        symbol = str(order.get("symbol", "")).upper()
        tp = order.get("trade_plan") or {}
        if symbol and tp:
            result[symbol] = tp
    return result


def _watchlist_tickers(wl_file: str) -> set[str]:
    """Return set of uppercase tickers from watchlist.candidates[]."""
    data = json.loads(Path(wl_file).read_text(encoding="utf-8"))
    return {
        str(c.get("ticker", "")).upper() for c in (data.get("candidates") or []) if c.get("ticker")
    }


def _plan_fields(tp: dict) -> dict:
    """Plan levels as a thesis_store.update()-shaped fields dict.

    Maps signal_entry → entry.target_price, stop_loss_price → exit.stop_loss,
    target_price → exit.take_profit.
    """
    fields: dict = {}
    if tp.get("signal_entry") is not None:
        fields.setdefault("entry", {})["target_price"] = tp["signal_entry"]
    if tp.get("stop_loss_price") is not None:
        fields.setdefault("exit", {})["stop_loss"] = tp["stop_loss_price"]
    if tp.get("target_price") is not None:
        fields.setdefault("exit", {})["take_profit"] = tp["target_price"]
    return fields


def _level_fields(thesis_data: dict) -> dict:
    """Adapter-provided levels (entry target / stop / take-profit) as an
    update()-shaped fields dict — used to refresh a reused pre-entry thesis
    from the fresh screener record (e.g. swing-short trade_levels)."""
    fields: dict = {}
    target = (thesis_data.get("entry") or {}).get("target_price")
    if target is not None:
        fields.setdefault("entry", {})["target_price"] = target
    ex = thesis_data.get("exit") or {}
    if ex.get("stop_loss") is not None:
        fields.setdefault("exit", {})["stop_loss"] = ex["stop_loss"]
    if ex.get("take_profit") is not None:
        fields.setdefault("exit", {})["take_profit"] = ex["take_profit"]
    return fields


def _enrich_from_plan(thesis_data: dict, plan_index: dict[str, dict]) -> None:
    """Mutate thesis_data in-place with breakout-planner levels.

    Levels via _plan_fields(); shares/risk_dollars go into raw_provenance.
    """
    ticker = str(thesis_data.get("ticker", "")).upper()
    tp = plan_index.get(ticker)
    if not tp:
        return
    for key, sub in _plan_fields(tp).items():
        thesis_data.setdefault(key, {}).update(sub)
    prov = thesis_data.setdefault("origin", {}).setdefault("raw_provenance", {})
    for key, val in [
        ("plan_shares", tp.get("shares")),
        ("plan_risk_dollars", tp.get("risk_dollars")),
    ]:
        if val is not None:
            prov[key] = val


# -- Input loading ------------------------------------------------------------


def _default_signals_md() -> Path:
    """Default signals journal: $TRADING_DATE_DIR/analysis/signals.md."""
    base = _default_output_dir("analysis", "trading-data/analysis")
    return Path(base) / "signals.md"


def _load_records(
    source: str, input_file: str | None, ticker: str | None
) -> tuple[list[dict], str | None, str]:
    """Return (records, source_date, resolved_input) for the chosen source.

    The ``ticker-analysis`` source reads a ``signals.md`` journal (default
    $TRADING_DATE_DIR/analysis/signals.md when ``--input`` is omitted) or a
    signal-JSON file, optionally filtered to one ``ticker``; each record carries
    its own date so ``source_date`` is None (the adapter stamps ``_source_date``
    per record). Every other source keeps the existing JSON contract and
    requires ``input_file``.
    """
    if source == "ticker-analysis":
        path = Path(input_file).expanduser() if input_file else _default_signals_md()
        if not path.exists():
            raise FileNotFoundError(f"Signals input not found: {path}")
        if path.suffix.lower() == ".md":
            records = signals_md.parse_signals_md(path.read_text(encoding="utf-8"), ticker=ticker)
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
            records = data if isinstance(data, list) else [data]
            if ticker:
                want = ticker.upper()
                records = [r for r in records if str(r.get("ticker", "")).upper() == want]
        return records, None, str(path)

    if not input_file:
        raise ValueError(f"--input is required for source '{source}'")
    path = Path(input_file)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return _extract_records(data, source), _extract_source_date(data), str(path)


# -- Public API ---------------------------------------------------------------


def ingest(
    source: str,
    input_file: str | None,
    state_dir: str = "state/theses",
    *,
    plan_input: str | None = None,
    watchlist_filter: str | None = None,
    ids_output: str | None = None,
    ticker: str | None = None,
) -> list[str]:
    """Ingest skill output and register theses.

    Args:
        source: Source skill name (e.g., "kanchi-dividend-sop").
        input_file: Path to JSON file with skill output. For the
            "ticker-analysis" source this may be a signals.md journal or a
            signal-JSON file, and may be omitted to use the default journal.
        state_dir: Path to thesis state directory.
        plan_input: Optional path to breakout_trade_plan_*.json; enriches
            entry.target_price / exit.stop_loss / exit.take_profit from planner.
        watchlist_filter: Optional path to watchlist_*.json; only registers
            tickers present in candidates[] (rejects orphan screener entries).
        ids_output: Optional path to write {TICKER: thesis_id} JSON mapping.
        ticker: Optional symbol filter (signals source only) — register just
            this ticker's latest signal.

    Returns:
        List of registered thesis IDs.

    Raises:
        ValueError: If source is unknown or input is invalid.
        FileNotFoundError: If input file doesn't exist.
    """
    if source not in _ADAPTERS:
        raise ValueError(f"Unknown source: {source}. Available: {sorted(_ADAPTERS.keys())}")

    records, source_date, resolved_input = _load_records(source, input_file, ticker)

    adapter = _ADAPTERS[source]
    state_path = Path(state_dir)

    plan_index = _build_plan_index(plan_input) if plan_input else {}
    allowed_tickers = _watchlist_tickers(watchlist_filter) if watchlist_filter else None

    thesis_ids: list[str] = []
    ticker_id_map: dict[str, str] = {}
    for record in records:
        try:
            thesis_data = adapter(record, resolved_input)
        except ValueError as e:
            logger.error("Adapter error for %s: %s", source, e)
            continue
        if thesis_data is None:
            continue  # skipped (e.g., edge research_only)
        # Filter to watchlist-only tickers when requested
        if allowed_tickers is not None:
            cand_ticker = str(thesis_data.get("ticker", "")).upper()
            if cand_ticker not in allowed_tickers:
                logger.debug("Skipping %s: not in watchlist filter", cand_ticker)
                continue
        # Enrich with exact plan levels when available
        if plan_index:
            _enrich_from_plan(thesis_data, plan_index)
        # Inject source date so thesis_id and created_at reflect the report date
        if source_date and "_source_date" not in thesis_data:
            thesis_data["_source_date"] = source_date
        # Skip if a non-terminal SAME-SIDE thesis already exists for this
        # ticker — avoids duplicate IDEA theses when the same ticker reappears
        # in the watchlist on consecutive days. A long and a short thesis for
        # the same ticker are different trades and never reuse each other.
        t_ticker = str(thesis_data.get("ticker", "")).upper()
        t_side = str(thesis_data.get("side") or "long").lower()
        existing_active = [
            e
            for e in thesis_store.query(state_path, ticker=t_ticker)
            if e.get("status") not in ("CLOSED", "INVALIDATED")
            and str(e.get("side") or "long").lower() == t_side
        ]
        if existing_active:
            existing = existing_active[-1]
            tid = existing["thesis_id"]
            logger.info(
                "Reusing thesis %s for %s (status=%s, non-terminal)",
                tid,
                t_ticker,
                existing.get("status"),
            )
            # Refresh levels on PRE-ENTRY reuse: the fresh screener record /
            # plan carries new pivot/stop/target while the thesis still holds
            # day-1 numbers (the heat ledger reads exit.stop_loss from here).
            # Plan levels win over adapter levels. Never touch an ACTIVE
            # thesis — its stop is the live bracket at the broker.
            if existing.get("status") in ("IDEA", "ENTRY_READY"):
                fields = _level_fields(thesis_data)
                tp = plan_index.get(t_ticker) if plan_index else None
                if tp:
                    for key, sub in _plan_fields(tp).items():
                        fields.setdefault(key, {}).update(sub)
                if fields:
                    try:
                        thesis_store.update(state_path, tid, fields)
                        logger.info("Refreshed levels on reused thesis %s", tid)
                    except (ValueError, OSError) as e:
                        logger.warning("Could not refresh levels on %s: %s", tid, e)
            thesis_ids.append(tid)
            ticker_id_map[t_ticker] = tid
            continue
        try:
            tid = thesis_store.register(state_path, thesis_data)
            thesis_ids.append(tid)
            ticker_id_map[str(thesis_data.get("ticker", "")).upper()] = tid
        except ValueError as e:
            logger.error("Failed to register from %s: %s", source, e)

    if ids_output and ticker_id_map:
        Path(ids_output).write_text(
            json.dumps(ticker_id_map, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return thesis_ids


def _extract_source_date(data: dict | list) -> str | None:
    """Extract report date from top-level metadata.

    Checks as_of, generated_at, and date fields.
    Returns YYYY-MM-DD string or None.
    """
    if isinstance(data, list):
        return None
    # as_of is the canonical source date (kanchi, earnings-trade-analyzer)
    as_of = data.get("as_of")
    if as_of and isinstance(as_of, str):
        return as_of[:10]  # "YYYY-MM-DD" or "YYYY-MM-DDTHH:..."
    # generated_at as fallback
    gen = data.get("generated_at")
    if gen and isinstance(gen, str):
        return gen[:10]
    return None


def _extract_records(data: dict | list, source: str) -> list[dict]:
    """Extract individual records from various output formats."""
    if isinstance(data, list):
        return data

    # Manual entry is free-form: a single dict is always one record, so the
    # manual adapter (not _extract_records) owns required-field validation and
    # can emit a clear "Missing required field 'ticker'" message.
    if source == "manual" and isinstance(data, dict):
        return [data]

    # Common patterns: {results: [...]}, {candidates: [...]}, {rows: [...]}, ...
    for key in ("results", "candidates", "rows"):
        if key in data and isinstance(data[key], list):
            return data[key]

    # Single record (e.g., edge ticket)
    if "id" in data or "ticker" in data or "symbol" in data:
        return [data]

    raise ValueError(
        f"Cannot extract records from {source} output. "
        "Expected list, or dict with 'results'/'candidates' key."
    )


# -- CLI entry point ----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Ingest skill output into Trader Memory Core")
    parser.add_argument("--source", required=True, help="Source skill name")
    parser.add_argument(
        "--input",
        default=None,
        help=(
            "Path to JSON input file. For --source ticker-analysis this may be a "
            "signals.md journal or a signal-JSON file; omit it to use the default "
            "$TRADING_DATE_DIR/analysis/signals.md."
        ),
    )
    parser.add_argument(
        "--ticker",
        default=None,
        help="Symbol filter (ticker-analysis source): register only this ticker's latest signal",
    )
    parser.add_argument(
        "--state-dir",
        default=_default_output_dir("journal/theses", "state/theses"),
        help="Thesis state directory (default: $TRADING_DATE_DIR/journal/theses)",
    )
    parser.add_argument(
        "--plan-input",
        default=None,
        help="Path to breakout_trade_plan_*.json for enriching entry/exit levels",
    )
    parser.add_argument(
        "--watchlist-filter",
        default=None,
        help="Path to watchlist_*.json; only register tickers in candidates[]",
    )
    parser.add_argument(
        "--ids-output",
        default=None,
        help="Path to write {TICKER: thesis_id} JSON mapping after registration",
    )
    args = parser.parse_args(argv)

    ids = ingest(
        args.source,
        args.input,
        args.state_dir,
        plan_input=args.plan_input,
        watchlist_filter=args.watchlist_filter,
        ids_output=args.ids_output,
        ticker=args.ticker,
    )
    if ids:
        print(f"Registered {len(ids)} thesis(es): {', '.join(ids)}")
        return 0

    # 0 theses registered. An empty watchlist filter is a legitimate no-op —
    # no candidates passed screening that day, so there is simply nothing to
    # ingest. That is success, not failure. Reserve a non-zero exit for the
    # genuine mismatch: the watchlist DID carry candidates but none of them
    # matched a registerable record in the input.
    expected: set[str] | None = None
    if args.watchlist_filter:
        try:
            expected = _watchlist_tickers(args.watchlist_filter)
        except (OSError, ValueError):
            expected = None
    if expected:
        print("No theses registered: watchlist had candidates but none matched the input.")
        return 1
    print("No theses registered (no candidates to ingest).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
