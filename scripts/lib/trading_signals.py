#!/usr/bin/env python3
"""Signal engine for the trading-schedule auto mode (stdlib only).

Pure logic shared by ``scripts/run_trading_schedule.py``:

  * ``fetch_quotes``       — headless last-price snapshot for a ticker list via
                             the public scanner.tradingview.com endpoint (the
                             same one tradingview-screener uses). No API key, no
                             TradingView Desktop. Prices can lag ~15 minutes on
                             delayed feeds — fine for a swing horizon where the
                             protective bracket order at the broker is the
                             actual safety net.
  * ``build_watchlist``    — merge breakout-trade-planner output (long side),
                             swing-short-screener candidates (short side) and
                             optional chart-validation verdicts into the
                             ``schedule/watchlist_<date>.json`` gate file.
  * ``size_short``         — step-6.3 sizing: half risk (1%), 25% position cap.
  * ``evaluate_signals``   — turn (watchlist, open positions, quotes, gate)
                             into concrete OPEN / CLOSE-type signals for the
                             trader. Detection only — never places orders.
  * signals state helpers  — once-per-day dedup so a 15-minute monitoring loop
                             does not repeat the same Telegram signal.

Everything here is deterministic and unit-testable; Telegram formatting and
subprocess orchestration live in run_trading_schedule.py.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SCAN_URL = "https://scanner.tradingview.com/america/scan"
USER_AGENT = "claude-trading-skills/auto-mode (+https://github.com)"
US_EXCHANGES = ["AMEX", "NASDAQ", "NYSE"]

# Entry chase band used when a candidate has no explicit worst_entry:
# do not chase price more than this % past the pivot/entry level.
DEFAULT_CHASE_PCT = 2.0
# "Approaching stop" early-warning band (percent of the stop level).
NEAR_STOP_BAND_PCT = 1.0
# Short side risks half of the long per-trade risk (trading plan step 6.3).
SHORT_RISK_PCT = 1.0
SHORT_MAX_POSITION_PCT = 25.0

# Signal types
OPEN_LONG = "OPEN_LONG"
OPEN_SHORT = "OPEN_SHORT"
MISSED = "MISSED"
SKIPPED_CAPACITY = "SKIPPED_CAPACITY"
STOP_HIT = "STOP_HIT"
NEAR_STOP = "NEAR_STOP"
TWO_R = "TWO_R"


class QuotesError(Exception):
    """Fatal quote-fetch failure (validation error or retries exhausted)."""


class TransientQuotesError(QuotesError):
    """Retryable failure (HTTP 429/5xx, network timeouts)."""


# --------------------------------------------------------------------------- #
# Quotes (public TradingView scanner; stdlib urllib)
# --------------------------------------------------------------------------- #
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
        if exc.code == 429 or exc.code >= 500:
            raise TransientQuotesError(f"HTTP {exc.code}") from exc
        raise QuotesError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise TransientQuotesError(f"network error: {exc.reason}") from exc


def fetch_quotes(
    tickers: list[str],
    *,
    timeout: int = 30,
    max_retries: int = 3,
    retry_base_delay: float = 1.5,
) -> dict[str, dict]:
    """Last prices for ``tickers`` -> {ticker: {price, volume, symbol_full}}.

    Missing/unknown tickers are silently absent from the result; callers must
    treat a missing quote as "no signal this round". Raises QuotesError when
    the scanner is unreachable after retries.
    """
    wanted = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    if not wanted:
        return {}
    payload = {
        "filter": [
            {"left": "name", "operation": "in_range", "right": wanted},
            {"left": "exchange", "operation": "in_range", "right": US_EXCHANGES},
        ],
        "columns": ["name", "close", "volume"],
        "range": [0, max(50, 4 * len(wanted))],
    }

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = _http_post_json(SCAN_URL, payload, timeout)
            break
        except TransientQuotesError as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(retry_base_delay * (2**attempt))
    else:
        raise QuotesError(f"quote fetch failed after {max_retries} attempts: {last_error}")

    quotes: dict[str, dict] = {}
    for row in response.get("data") or []:
        values = row.get("d") or []
        if len(values) < 2:
            continue
        name, close = values[0], values[1]
        if name not in set(wanted) or name in quotes or close is None:
            continue
        quotes[name] = {
            "price": float(close),
            "volume": values[2] if len(values) > 2 else None,
            "symbol_full": row.get("s"),
        }
    return quotes


# --------------------------------------------------------------------------- #
# Watchlist building
# --------------------------------------------------------------------------- #
def size_short(
    account_size: float,
    entry: float,
    stop: float,
    risk_pct: float = SHORT_RISK_PCT,
    max_position_pct: float = SHORT_MAX_POSITION_PCT,
) -> int:
    """Step-6.3 short sizing: shares = (account * risk%) / (stop - entry),
    capped so the position never exceeds max_position_pct of the account.
    Returns 0 when the stop is not above the entry (invalid short geometry)."""
    risk_per_share = stop - entry
    if risk_per_share <= 0 or entry <= 0 or account_size <= 0:
        return 0
    shares = int(account_size * risk_pct / 100 / risk_per_share)
    cap = int(account_size * max_position_pct / 100 / entry)
    return min(shares, cap)


def _validation_index(validation: dict | None) -> dict[str, dict]:
    verdicts = (validation or {}).get("verdicts") or []
    return {
        str(v.get("ticker", "")).upper(): v
        for v in verdicts
        if isinstance(v, dict) and v.get("ticker")
    }


def _apply_validation(candidate: dict, verdicts: dict[str, dict]) -> dict | None:
    """Mark the candidate validated/rejected. Returns None when rejected."""
    v = verdicts.get(candidate["ticker"])
    if not v:
        candidate["validated"] = None
        return candidate
    verdict = str(v.get("verdict", "")).lower()
    candidate["validation_note"] = v.get("note", "")
    if verdict == "reject":
        candidate["validated"] = False
        return None
    candidate["validated"] = verdict == "pass" or None
    return candidate


def build_watchlist(
    date_str: str,
    gate_decision: str,
    plan: dict | None,
    short_candidates: list | None,
    validation: dict | None,
    *,
    account_size: float | None = None,
    short_risk_pct: float = SHORT_RISK_PCT,
    notes: str = "",
    source_plan: str | None = None,
) -> dict:
    """Merge planner output + short screen + validation verdicts into the
    watchlist gate file consumed by the premarket digest and the intraday
    monitor. Long candidates keep the planner's exact numbers; short candidates
    get half-risk sizing. Validation-rejected names are moved aside (not
    silently dropped)."""
    verdicts = _validation_index(validation)
    candidates: list[dict] = []
    rejected: list[dict] = []

    def add(candidate: dict) -> None:
        kept = _apply_validation(candidate, verdicts)
        if kept is None:
            rejected.append(candidate)
        else:
            candidates.append(kept)

    for order in (plan or {}).get("actionable_orders") or []:
        tp = order.get("trade_plan") or {}
        add(
            {
                "ticker": str(order.get("symbol", "")).upper(),
                "side": "long",
                "setup": f"VCP {order.get('execution_state', '')}".strip(),
                "pivot": tp.get("signal_entry"),
                "worst_entry": tp.get("worst_entry"),
                "stop": tp.get("stop_loss_price"),
                "target": tp.get("target_price"),
                "shares": tp.get("shares"),
                "risk_dollars": tp.get("risk_dollars"),
                "score": order.get("composite_score"),
                "plan_type": order.get("plan_type"),
            }
        )

    for adv in (plan or {}).get("revalidation") or []:
        add(
            {
                "ticker": str(adv.get("symbol", "")).upper(),
                "side": "long",
                "setup": "VCP Breakout (revalidate)",
                "pivot": adv.get("pivot"),
                "worst_entry": adv.get("max_entry_price"),
                "stop": adv.get("stop_loss_price"),
                "target": adv.get("target_price"),
                "shares": None,
                "risk_dollars": None,
                "score": adv.get("composite_score"),
                "plan_type": adv.get("plan_type", "late_breakout_revalidation"),
            }
        )

    for cand in short_candidates or []:
        levels = cand.get("trade_levels") or {}
        entry, stop = levels.get("entry"), levels.get("stop")
        shares = None
        risk_dollars = None
        if account_size and entry and stop:
            shares = size_short(account_size, entry, stop, risk_pct=short_risk_pct) or None
            if shares:
                risk_dollars = round(shares * (stop - entry), 2)
        add(
            {
                "ticker": str(cand.get("symbol", "")).upper(),
                "side": "short",
                "setup": f"Stage 4 (grade {cand.get('grade', '?')})",
                "pivot": entry,
                "worst_entry": round(entry * (1 - DEFAULT_CHASE_PCT / 100), 2) if entry else None,
                "stop": stop,
                "target": levels.get("target_2r"),
                "shares": shares,
                "risk_dollars": risk_dollars,
                "score": cand.get("composite_score"),
                "plan_type": "stage4_breakdown",
            }
        )

    return {
        "workflow": "swing-opportunity-daily",
        "date": date_str,
        "exposure_decision": gate_decision,
        "candidates": candidates,
        "rejected_by_validation": rejected,
        "notes": notes,
        "source_plan": source_plan,
    }


# --------------------------------------------------------------------------- #
# Signal evaluation
# --------------------------------------------------------------------------- #
def _position_side(position: dict) -> str:
    stop = position.get("stop_loss") or 0
    entry = position.get("entry_price") or 0
    return "short" if stop > entry else "long"


def _signal(sig_type: str, ticker: str, side: str, price: float, **extra) -> dict:
    return {
        "key": f"{ticker}:{sig_type}",
        "type": sig_type,
        "ticker": ticker,
        "side": side,
        "price": price,
        **extra,
    }


def _candidate_open_signal(candidate: dict, price: float, gate_decision: str) -> dict | None:
    """OPEN/MISSED decision for one watchlist candidate at the current price."""
    side = candidate.get("side", "long")
    pivot = candidate.get("pivot")
    if pivot in (None, 0):
        return None

    if side == "long":
        if gate_decision != "allow":
            return None  # no new long risk under restrict / cash-priority
        worst = candidate.get("worst_entry") or round(pivot * (1 + DEFAULT_CHASE_PCT / 100), 2)
        if pivot <= price <= worst:
            return _signal(OPEN_LONG, candidate["ticker"], side, price, candidate=candidate)
        if price > worst:
            return _signal(MISSED, candidate["ticker"], side, price, candidate=candidate)
        return None

    # short side: only when the regime gate forbids new longs (plan rule 6.4 —
    # never short while the gate is `allow`)
    if gate_decision not in ("restrict", "cash-priority"):
        return None
    worst = candidate.get("worst_entry") or round(pivot * (1 - DEFAULT_CHASE_PCT / 100), 2)
    if worst <= price <= pivot:
        return _signal(OPEN_SHORT, candidate["ticker"], side, price, candidate=candidate)
    if price < worst:
        return _signal(MISSED, candidate["ticker"], side, price, candidate=candidate)
    return None


def _position_manage_signal(position: dict, price: float) -> dict | None:
    """STOP_HIT / NEAR_STOP / TWO_R decision for one open position."""
    ticker = str(position.get("ticker", "")).upper()
    stop = position.get("stop_loss")
    entry = position.get("entry_price")
    if not ticker or stop in (None, 0) or entry in (None, 0):
        return None
    side = _position_side(position)
    band = NEAR_STOP_BAND_PCT / 100

    if side == "long":
        risk = entry - stop
        if price <= stop:
            return _signal(STOP_HIT, ticker, side, price, position=position)
        if price <= stop * (1 + band):
            return _signal(NEAR_STOP, ticker, side, price, position=position)
        if risk > 0 and price >= entry + 2 * risk:
            return _signal(TWO_R, ticker, side, price, position=position)
        return None

    risk = stop - entry
    if price >= stop:
        return _signal(STOP_HIT, ticker, side, price, position=position)
    if price >= stop * (1 - band):
        return _signal(NEAR_STOP, ticker, side, price, position=position)
    if risk > 0 and price <= entry - 2 * risk:
        return _signal(TWO_R, ticker, side, price, position=position)
    return None


def evaluate_signals(
    watchlist: dict | None,
    heat: dict | None,
    quotes: dict[str, dict],
    gate_decision: str,
    sent: set[str],
) -> list[dict]:
    """Compute the actionable signals for this monitoring round.

    Inputs are read-only; dedup against ``sent`` (keys "TICKER:TYPE" already
    notified today). OPEN signals additionally respect the portfolio limits in
    the latest heat report: open slots and remaining heat budget, consumed in
    composite-score order (best candidates claim capacity first).
    """
    signals: list[dict] = []
    positions = (heat or {}).get("positions") or []
    open_tickers = {str(p.get("ticker", "")).upper() for p in positions}

    # --- manage open positions (always, regardless of gate) -----------------
    for position in positions:
        quote = quotes.get(str(position.get("ticker", "")).upper())
        if not quote:
            continue
        signal = _position_manage_signal(position, quote["price"])
        if signal and signal["key"] not in sent:
            signals.append(signal)

    # --- opening signals from the watchlist ---------------------------------
    slots_left = (heat or {}).get("remaining_position_slots")
    heat_left = (heat or {}).get("remaining_heat_dollars")

    candidates = (watchlist or {}).get("candidates") or []
    ranked = sorted(candidates, key=lambda c: c.get("score") or 0, reverse=True)
    for candidate in ranked:
        ticker = str(candidate.get("ticker", "")).upper()
        if not ticker or ticker in open_tickers:
            continue
        quote = quotes.get(ticker)
        if not quote:
            continue
        signal = _candidate_open_signal(
            {**candidate, "ticker": ticker}, quote["price"], gate_decision
        )
        if not signal or signal["key"] in sent:
            continue
        if signal["type"] in (OPEN_LONG, OPEN_SHORT):
            risk = candidate.get("risk_dollars") or 0
            no_slot = slots_left is not None and slots_left <= 0
            no_heat = heat_left is not None and risk > heat_left
            if no_slot or no_heat:
                signal = _signal(
                    SKIPPED_CAPACITY,
                    ticker,
                    signal["side"],
                    signal["price"],
                    candidate=candidate,
                    reason="нет свободных слотов" if no_slot else "не хватает heat-бюджета",
                )
                if signal["key"] in sent:
                    continue
            else:
                if slots_left is not None:
                    slots_left -= 1
                if heat_left is not None:
                    heat_left -= risk
        signals.append(signal)

    return signals


# --------------------------------------------------------------------------- #
# Dedup state (once per day per TICKER:TYPE)
# --------------------------------------------------------------------------- #
def load_signals_state(path: Path | str, date_str: str) -> dict:
    """Read the dedup state; roll over to a fresh one on a new date."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("date") == date_str:
            data.setdefault("sent", {})
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"date": date_str, "sent": {}}


def save_signals_state(path: Path | str, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".signals_state.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def mark_sent(state: dict, keys: list[str], now_iso: str) -> None:
    for key in keys:
        state.setdefault("sent", {})[key] = now_iso
