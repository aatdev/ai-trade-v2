"""Alpaca order template builder for breakout trade planner.

Generates stop-limit bracket templates (pre_place) and limit bracket
templates (post_confirm) for Pre-breakout candidates. Breakout candidates
get revalidation advisories only (no order template).
"""

from __future__ import annotations


def build_pre_place_template(
    symbol: str,
    qty: int,
    signal_entry: float,
    worst_entry: float,
    stop_loss: float,
    take_profit: float,
    time_in_force: str = "day",
) -> dict:
    """Build a stop-limit bracket order template for pre-placement.

    This template is placed on the market and auto-triggers when price
    reaches signal_entry (buy stop). Limit at worst_entry prevents chasing.

    Raises:
        ValueError: On invalid inputs.
    """
    _validate_order_params(qty, signal_entry, worst_entry, stop_loss, take_profit)

    return {
        "execution_mode": "pre_place",
        "requires_monitor_confirmation": False,
        "symbol": symbol,
        "qty": qty,
        "side": "buy",
        "type": "stop_limit",
        "stop_price": signal_entry,
        "limit_price": worst_entry,
        "time_in_force": time_in_force,
        "order_class": "bracket",
        "take_profit": {"limit_price": take_profit},
        "stop_loss": {"stop_price": stop_loss},
    }


def build_post_confirm_template(
    symbol: str,
    qty: int,
    worst_entry: float,
    stop_loss: float,
    take_profit: float,
    entry_condition: dict,
    time_in_force: str = "day",
) -> dict:
    """Build a limit bracket order template for post-confirmation mode.

    This template is sent after the breakout-monitor confirms 5-min candle
    conditions (close > pivot, close_loc >= 0.60, RVOL >= 1.5).

    Raises:
        ValueError: On invalid inputs.
    """
    _validate_post_confirm_params(qty, worst_entry, stop_loss, take_profit)

    return {
        "execution_mode": "post_confirm",
        "requires_monitor_confirmation": True,
        "entry_condition": entry_condition,
        "symbol": symbol,
        "qty": qty,
        "side": "buy",
        "type": "limit",
        "limit_price": worst_entry,
        "time_in_force": time_in_force,
        "order_class": "bracket",
        "take_profit": {"limit_price": take_profit},
        "stop_loss": {"stop_price": stop_loss},
    }


def build_revalidation_advisory(
    symbol: str,
    pivot: float,
    current_price: float,
    worst_entry: float,
    stop_loss: float | None = None,
    target_price: float | None = None,
) -> dict:
    """Build an advisory for Breakout-state candidates (no order template).

    These candidates already crossed the pivot and need live revalidation
    before any order can be placed. stop/target are included so the watchlist
    never carries an entry trigger without a stop (the intraday monitor would
    otherwise fire OPEN signals with no protective level).
    """
    return {
        "symbol": symbol,
        "plan_type": "late_breakout_revalidation",
        "next_action": "revalidate live price/5min confirmation before any order",
        "pivot": pivot,
        "current_price": current_price,
        "max_entry_price": worst_entry,
        "stop_loss_price": stop_loss,
        "target_price": target_price,
    }


def build_entry_condition(
    pivot: float,
    close_loc_min: float = 0.60,
    rvol_threshold: float = 1.5,
    max_chase_pct: float = 2.0,
) -> dict:
    """Build a machine-readable entry condition for the post_confirm template."""
    return {
        "bar_interval": "5min",
        "trigger": {"field": "close", "op": ">", "value": pivot},
        "checks": [
            {"field": "close_loc", "op": ">=", "value": close_loc_min},
            {"field": "tod_rvol", "op": ">=", "value": rvol_threshold},
            {"field": "price_vs_pivot_pct", "op": "<=", "value": max_chase_pct},
        ],
    }


def _validate_order_params(
    qty: int,
    signal_entry: float,
    worst_entry: float,
    stop_loss: float,
    take_profit: float,
) -> None:
    """Shared validation for stop-entry (pre_place) order templates."""
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if (signal_entry - stop_loss) < 0.01:
        raise ValueError(
            f"stop_loss ({stop_loss}) must be >= $0.01 below signal_entry ({signal_entry})"
        )
    if take_profit <= worst_entry:
        raise ValueError(f"take_profit ({take_profit}) must be above worst_entry ({worst_entry})")


def _validate_post_confirm_params(
    qty: int,
    worst_entry: float,
    stop_loss: float,
    take_profit: float,
) -> None:
    """Shared validation for limit-entry (post_confirm) order templates."""
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if (worst_entry - stop_loss) < 0.01:
        raise ValueError(
            f"stop_loss ({stop_loss}) must be >= $0.01 below worst_entry ({worst_entry})"
        )
    if take_profit <= worst_entry:
        raise ValueError(f"take_profit ({take_profit}) must be above worst_entry ({worst_entry})")


# ---------------------------------------------------------------------------
# Interactive Brokers templates
#
# The interactive-brokers MCP `place_order` tool exposes only single MKT/LMT/STP
# orders — there is no native bracket, OCA group, or stop-limit. An IB bracket is
# therefore emitted as an ordered leg sequence (entry → stop_loss → take_profit),
# each leg a standalone `place_order` payload. Operator guidance (place the entry
# first, attach exits as a manual OCO after the fill) ships in `notes`.
# ---------------------------------------------------------------------------

# tif values accepted by mcp__interactive-brokers__place_order.
_IB_TIF = frozenset({"DAY", "GTC", "IOC", "OPG"})

_IB_BRACKET_NOTES = (
    "interactive-brokers MCP place_order exposes single MKT/LMT/STP orders — "
    "no native bracket / OCA group / stop-limit.",
    "Place the 'entry' leg first; after it fills, place 'stop_loss' and "
    "'take_profit' as a manual OCO (cancel the sibling when one fills).",
    "Supply accountId at call time; clear any place_order replyId via confirm_order.",
)


def _normalize_ib_tif(time_in_force: str) -> str:
    """Uppercase + validate a time-in-force against the IB place_order enum."""
    tif = str(time_in_force).strip().upper()
    if tif not in _IB_TIF:
        raise ValueError(f"time_in_force must be one of {sorted(_IB_TIF)}, got {time_in_force!r}")
    return tif


def _ib_exit_legs(symbol: str, qty: int, stop_loss: float, take_profit: float) -> list[dict]:
    """Protective stop + profit-target SELL legs (GTC) shared by both IB modes."""
    return [
        {
            "role": "stop_loss",
            "symbol": symbol,
            "action": "SELL",
            "orderType": "STP",
            "stopPrice": stop_loss,
            "quantity": qty,
            "tif": "GTC",
        },
        {
            "role": "take_profit",
            "symbol": symbol,
            "action": "SELL",
            "orderType": "LMT",
            "price": take_profit,
            "quantity": qty,
            "tif": "GTC",
        },
    ]


def build_ib_pre_place_template(
    symbol: str,
    qty: int,
    signal_entry: float,
    worst_entry: float,
    stop_loss: float,
    take_profit: float,
    time_in_force: str = "DAY",
) -> dict:
    """Build an Interactive Brokers pre-placement bracket (leg sequence).

    The entry is a buy-stop (STP @ signal_entry). The MCP has no stop-limit, so
    `max_fill_price` (worst_entry) is advisory only — a gap can fill above it.

    Raises:
        ValueError: On invalid inputs or time_in_force.
    """
    _validate_order_params(qty, signal_entry, worst_entry, stop_loss, take_profit)
    tif = _normalize_ib_tif(time_in_force)
    entry = {
        "role": "entry",
        "symbol": symbol,
        "action": "BUY",
        "orderType": "STP",
        "stopPrice": signal_entry,
        "quantity": qty,
        "tif": tif,
    }
    return {
        "broker": "interactive_brokers",
        "mcp_tool": "place_order",
        "execution_mode": "pre_place",
        "requires_monitor_confirmation": False,
        "symbol": symbol,
        "qty": qty,
        "order_class": "bracket",
        "entry_order_type": "STP",
        "max_fill_price": worst_entry,
        "legs": [entry, *_ib_exit_legs(symbol, qty, stop_loss, take_profit)],
        "notes": [
            *_IB_BRACKET_NOTES,
            f"Entry is stop-market (STP @ {signal_entry}), not stop-limit: this MCP "
            f"has no STP LMT, so a gap can fill above max_fill_price ({worst_entry}). "
            "Review the projected fill before sending.",
        ],
    }


def build_ib_post_confirm_template(
    symbol: str,
    qty: int,
    worst_entry: float,
    stop_loss: float,
    take_profit: float,
    entry_condition: dict,
    time_in_force: str = "DAY",
) -> dict:
    """Build an Interactive Brokers post-confirmation bracket (leg sequence).

    The entry is a limit (LMT @ worst_entry), sent only after the breakout-monitor
    confirms `entry_condition`. This maps cleanly to IB with no stop-limit gap.

    Raises:
        ValueError: On invalid inputs or time_in_force.
    """
    _validate_post_confirm_params(qty, worst_entry, stop_loss, take_profit)
    tif = _normalize_ib_tif(time_in_force)
    entry = {
        "role": "entry",
        "symbol": symbol,
        "action": "BUY",
        "orderType": "LMT",
        "price": worst_entry,
        "quantity": qty,
        "tif": tif,
    }
    return {
        "broker": "interactive_brokers",
        "mcp_tool": "place_order",
        "execution_mode": "post_confirm",
        "requires_monitor_confirmation": True,
        "entry_condition": entry_condition,
        "symbol": symbol,
        "qty": qty,
        "order_class": "bracket",
        "entry_order_type": "LMT",
        "legs": [entry, *_ib_exit_legs(symbol, qty, stop_loss, take_profit)],
        "notes": list(_IB_BRACKET_NOTES),
    }
