#!/usr/bin/env python3
"""Breakout Trade Planner — generate Minervini-style trade plans from VCP screener output.

Reads VCP screener JSON, applies a strict Minervini Gate, calculates position
sizes using worst-case entry prices, and outputs actionable trade plans with
Alpaca order templates (pre_place and post_confirm modes).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add scripts dir to path for sibling imports
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from earnings_gate import (
    GATE_BLOCKED,
    EarningsFetchError,
    build_gate_fields,
    fetch_earnings_map,
)
from order_builder import (
    build_entry_condition,
    build_post_confirm_template,
    build_pre_place_template,
    build_revalidation_advisory,
)
from risk_calculator import (
    calculate_position_size,
    calculate_r_multiples,
    calculate_risks,
    derive_trade_prices,
    get_rating_band,
    get_sizing_multiplier,
    round_price,
)

ACCEPTED_INPUT_VERSIONS = {"1.0"}
MAX_RISK_PCT = 8.0
# Screener ratings that may become actionable orders; anything else (e.g.
# "Developing VCP" from a state cap / wide-and-loose cap) is watch-only.
BUYABLE_RATINGS = {"Textbook VCP", "Strong VCP", "Good VCP"}

# Parameter-profile keys shared across the trading scripts. Keys outside this
# union trigger a warning (typo guard); keys inside it that a given script does
# not use are silently skipped (one profile file can serve planner, sizer and
# heat ledger alike).
KNOWN_PROFILE_KEYS = {
    "account_size",
    "risk_pct",
    "max_position_pct",
    "max_sector_pct",
    "max_portfolio_heat_pct",
    "max_positions",
    "target_r_multiple",
    "stop_buffer_pct",
    "max_chase_pct",
    "pivot_buffer_pct",
    "earnings_gate_days",
    "time_stop_trading_days",
    "atr_multiplier",
}

PLANNER_PROFILE_KEYS = {
    "account_size",
    "risk_pct",
    "max_position_pct",
    "max_sector_pct",
    "max_portfolio_heat_pct",
    "target_r_multiple",
    "stop_buffer_pct",
    "max_chase_pct",
    "pivot_buffer_pct",
    "earnings_gate_days",
    "time_stop_trading_days",
}


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


def _default_profile():
    """Profile default: $TRADING_PROFILE, else $TRADING_DATE_DIR/trading_profile.json."""
    import os

    prof = os.environ.get("TRADING_PROFILE")
    if prof:
        return prof
    base = _trading_data_dir()
    if base is not None and (base / "trading_profile.json").is_file():
        return str(base / "trading_profile.json")
    return None


def load_profile(path: str, applied_keys: set[str]) -> dict:
    """Load a JSON parameter profile and return the keys this script applies.

    Numeric values only; unknown keys warn to stderr (typo guard) while keys
    belonging to sibling scripts are skipped silently.
    """
    with open(path) as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("profile JSON must be an object of parameter values")

    unknown = sorted(set(raw) - KNOWN_PROFILE_KEYS)
    if unknown:
        print(
            f"Warning: ignoring unknown profile keys: {', '.join(unknown)}",
            file=sys.stderr,
        )

    applied: dict[str, float] = {}
    for key in sorted(applied_keys & set(raw)):
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"profile key '{key}' must be a number, got {value!r}")
        applied[key] = value
    return applied


def load_input(path: str) -> dict:
    """Load and validate VCP screener JSON."""
    with open(path) as f:
        data = json.load(f)

    version = data.get("schema_version")
    if version is None:
        raise ValueError(
            f"Input JSON missing 'schema_version' (expected one of {ACCEPTED_INPUT_VERSIONS})"
        )
    if version not in ACCEPTED_INPUT_VERSIONS:
        raise ValueError(
            f"Unsupported schema_version '{version}' (expected {ACCEPTED_INPUT_VERSIONS})"
        )

    if "results" not in data or not isinstance(data["results"], list):
        raise ValueError("Input JSON missing or empty 'results' array")

    return data


def load_exposure(path: str | None) -> dict:
    """Load current portfolio exposure or return defaults."""
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"sector_exposure": {}, "open_risk_pct": 0.0}


REQUIRED_FIELDS = ["symbol", "sector", "price", "composite_score", "execution_state", "valid_vcp"]
BREAKOUT_EXTRA_FIELDS = [
    "volume_pattern.breakout_volume_detected",
    "pivot_proximity.distance_from_pivot_pct",
]


def _get_nested(d: dict, key: str):
    """Get a possibly nested field like 'vcp_pattern.pivot_price'."""
    parts = key.split(".")
    val = d
    for p in parts:
        if not isinstance(val, dict):
            return None
        val = val.get(p)
    return val


def validate_result(result: dict) -> tuple[bool, list[str]]:
    """Validate a single VCP result has required fields.

    Returns (is_valid, list_of_warnings).
    """
    warnings = []
    for field in REQUIRED_FIELDS:
        if _get_nested(result, field) is None:
            warnings.append(f"missing required field: {field}")

    pivot = _get_nested(result, "vcp_pattern.pivot_price")
    if pivot is None:
        warnings.append("missing vcp_pattern.pivot_price")

    contractions = _get_nested(result, "vcp_pattern.contractions")
    if not contractions or not isinstance(contractions, list) or len(contractions) == 0:
        warnings.append("missing or empty vcp_pattern.contractions")
    elif _get_nested(contractions[-1], "low_price") is None:
        warnings.append("missing contractions[-1].low_price")

    # Warn (not fail) for Breakout-specific fields missing
    state = _get_nested(result, "execution_state")
    if state == "Breakout":
        for field in BREAKOUT_EXTRA_FIELDS:
            if _get_nested(result, field) is None:
                warnings.append(f"missing Breakout field: {field}")

    return len(warnings) == 0, warnings


def process_candidate(
    result: dict,
    args: argparse.Namespace,
    cumulative_risk_pct: float,
    sector_tracker: dict[str, float],
    exposure: dict,
) -> dict:
    """Process a single VCP candidate through the Minervini Gate.

    Returns a classified result dict with plan_type, trade_plan, etc.
    """
    symbol = result["symbol"]
    current_price = result["price"]
    composite_score = result["composite_score"]
    execution_state = result["execution_state"]
    valid_vcp = result.get("valid_vcp", False)
    sector = result.get("sector", "Unknown")

    rating_band = get_rating_band(composite_score)

    # Derive trade prices
    pivot = result["vcp_pattern"]["pivot_price"]
    contractions = result["vcp_pattern"]["contractions"]
    last_low = contractions[-1]["low_price"]

    try:
        signal_entry, worst_entry, stop_loss = derive_trade_prices(
            pivot,
            last_low,
            pivot_buffer_pct=args.pivot_buffer_pct,
            max_chase_pct=args.max_chase_pct,
            stop_buffer_pct=args.stop_buffer_pct,
        )
    except ValueError as e:
        return _reject(symbol, f"Trade price derivation failed: {e}")

    risk_pct_signal, risk_pct_worst = calculate_risks(signal_entry, worst_entry, stop_loss)

    # Take profit (worst-entry based)
    tp_worst = round_price(worst_entry + args.target_r_multiple * (worst_entry - stop_loss))

    base_output = {
        "symbol": symbol,
        "company_name": result.get("company_name", ""),
        "sector": sector,
        "composite_score": composite_score,
        "rating_band": rating_band,
        "execution_state": execution_state,
    }

    # Respect the screener's caps: state cap and wide-and-loose live only in
    # the rating STRING — the numeric composite ignores them, so re-deriving
    # the band from the number silently promotes "do not buy" patterns to
    # fully sized actionable orders. Absent rating (older outputs) = no gate.
    rating = result.get("rating")
    if rating is not None and rating not in BUYABLE_RATINGS:
        if execution_state == "Pre-breakout" and valid_vcp:
            return _watchlist(base_output, pivot, stop_loss)
        return _reject(symbol, f"screener rating '{rating}' is capped below buyable")

    # --- Pre-breakout path ---
    if execution_state == "Pre-breakout":
        plan_eligible = (
            valid_vcp
            and rating_band in ("textbook", "strong", "good")
            and risk_pct_worst <= MAX_RISK_PCT
        )

        if not plan_eligible:
            # Check watchlist eligibility
            if valid_vcp and 60 <= composite_score < 70:
                return _watchlist(base_output, pivot, stop_loss)
            reasons = []
            if not valid_vcp:
                reasons.append("valid_vcp=False")
            if rating_band not in ("textbook", "strong", "good"):
                reasons.append(f"rating_band={rating_band}")
            if risk_pct_worst > MAX_RISK_PCT:
                reasons.append(f"risk_pct_worst={risk_pct_worst}%>{MAX_RISK_PCT}%")
            return _reject(symbol, "; ".join(reasons))

        return _build_actionable(
            base_output,
            args,
            signal_entry,
            worst_entry,
            stop_loss,
            risk_pct_signal,
            risk_pct_worst,
            tp_worst,
            pivot,
            cumulative_risk_pct,
            sector_tracker,
            exposure,
        )

    # --- Breakout path ---
    if execution_state == "Breakout":
        breakout_volume = _get_nested(result, "volume_pattern.breakout_volume_detected") or False
        distance = _get_nested(result, "pivot_proximity.distance_from_pivot_pct")
        if distance is None:
            return _reject(symbol, "missing distance_from_pivot_pct for Breakout")

        plan_eligible = (
            valid_vcp
            and rating_band in ("textbook", "strong", "good")
            and risk_pct_worst <= MAX_RISK_PCT
            and breakout_volume
            and distance <= args.max_chase_pct
            and current_price <= worst_entry
        )

        if plan_eligible:
            advisory = build_revalidation_advisory(
                symbol, pivot, current_price, worst_entry,
                stop_loss=stop_loss, target_price=tp_worst,
            )
            advisory.update(base_output)
            advisory["decision_code"] = "REVALIDATION_BREAKOUT"
            advisory["risk_pct_worst"] = risk_pct_worst
            return {"classification": "revalidation", "data": advisory}

        # Breakout candidates do not go to watchlist — they already crossed pivot
        reasons = []
        if not valid_vcp:
            reasons.append("valid_vcp=False")
        if not breakout_volume:
            reasons.append("no breakout volume")
        if distance is not None and distance > args.max_chase_pct:
            reasons.append(f"distance={distance}%>{args.max_chase_pct}%")
        if current_price > worst_entry:
            reasons.append(f"price={current_price}>worst_entry={worst_entry}")
        if risk_pct_worst > MAX_RISK_PCT:
            reasons.append(f"risk_pct_worst={risk_pct_worst}%>{MAX_RISK_PCT}%")
        return _reject(symbol, "; ".join(reasons) if reasons else "ineligible Breakout")

    # --- Watchlist path ---
    if (
        valid_vcp
        and execution_state in ("Pre-breakout", "Early-post-breakout")
        and 60 <= composite_score < 70
    ):
        return _watchlist(base_output, pivot, stop_loss)

    # --- Reject ---
    return _reject(symbol, f"state={execution_state}, score={composite_score}")


def _build_actionable(
    base: dict,
    args,
    signal_entry,
    worst_entry,
    stop_loss,
    risk_pct_signal,
    risk_pct_worst,
    tp_worst,
    pivot,
    cumulative_risk_pct,
    sector_tracker,
    exposure,
):
    """Build an actionable order with trade plan and order templates."""
    sector = base["sector"]
    rating_band = base["rating_band"]
    multiplier = get_sizing_multiplier(rating_band)

    current_sector_exp = exposure.get("sector_exposure", {}).get(sector, 0.0)
    current_sector_exp += sector_tracker.get(sector, 0.0)

    sizing = calculate_position_size(
        worst_entry=worst_entry,
        stop_loss=stop_loss,
        account_size=args.account_size,
        base_risk_pct=args.risk_pct,
        sizing_multiplier=multiplier,
        max_position_pct=args.max_position_pct,
        max_sector_pct=args.max_sector_pct,
        current_sector_exposure=current_sector_exp,
    )

    if sizing["shares"] == 0:
        constraint = sizing.get("binding_constraint", "unknown")
        return {
            "classification": "constrained",
            "data": {"symbol": base["symbol"], "reason": f"0 shares: {constraint}"},
        }

    risk_dollars = sizing["risk_dollars"]
    risk_pct_of_account = risk_dollars / args.account_size * 100
    new_cumulative = cumulative_risk_pct + risk_pct_of_account

    if new_cumulative > args.max_portfolio_heat_pct:
        return {
            "classification": "deferred",
            "data": {
                "symbol": base["symbol"],
                "reason": f"Portfolio heat ceiling: {new_cumulative:.2f}% > {args.max_portfolio_heat_pct}%",
            },
        }

    # Build entry condition and order templates
    entry_cond = build_entry_condition(
        pivot=pivot,
        max_chase_pct=args.max_chase_pct,
    )

    pre_place = build_pre_place_template(
        symbol=base["symbol"],
        qty=sizing["shares"],
        signal_entry=signal_entry,
        worst_entry=worst_entry,
        stop_loss=stop_loss,
        take_profit=tp_worst,
    )

    post_confirm = build_post_confirm_template(
        symbol=base["symbol"],
        qty=sizing["shares"],
        worst_entry=worst_entry,
        stop_loss=stop_loss,
        take_profit=tp_worst,
        entry_condition=entry_cond,
    )

    # Valid for today if market is open (weekday), otherwise next trading day
    today = datetime.now().date()
    if today.weekday() < 5:  # Monday-Friday: valid today
        valid_date = today
    else:  # Weekend: next Monday
        valid_date = today + timedelta(days=7 - today.weekday())

    result = {
        **base,
        "plan_type": "pending_breakout",
        "decision_code": "ACTIONABLE_PREBREAKOUT",
        "decision_reason": (
            f"valid_vcp && state=Pre-breakout && risk_worst={risk_pct_worst}% <= {MAX_RISK_PCT}%"
        ),
        "plan_valid_for_session": str(valid_date),
        "trade_plan": {
            "signal_entry": signal_entry,
            "worst_entry": worst_entry,
            "stop_loss_price": stop_loss,
            "risk_per_share": round(worst_entry - stop_loss, 2),
            "risk_pct_signal": risk_pct_signal,
            "risk_pct_worst": risk_pct_worst,
            "r_multiples_signal": calculate_r_multiples(signal_entry, stop_loss),
            "r_multiples_worst": calculate_r_multiples(worst_entry, stop_loss),
            "target_price": tp_worst,
            "reward_risk_ratio": args.target_r_multiple,
            "sizing_multiplier": multiplier,
            "effective_risk_pct": sizing["effective_risk_pct"],
            "shares": sizing["shares"],
            "position_value": sizing["position_value"],
            "risk_dollars": risk_dollars,
            "cumulative_risk_pct": round(new_cumulative, 2),
            "binding_constraint": sizing["binding_constraint"],
        },
        "order_templates": {
            "pre_place": pre_place,
            "post_confirm": post_confirm,
        },
    }

    time_stop_days = int(getattr(args, "time_stop_trading_days", 0) or 0)
    if time_stop_days > 0:
        result["trade_plan"]["time_stop_trading_days"] = time_stop_days
        result["trade_plan"]["time_stop_rule"] = (
            f"Exit if the position has not reached +1R within "
            f"{time_stop_days} trading days of entry"
        )

    return {"classification": "actionable", "data": result, "risk_pct": risk_pct_of_account}


def _watchlist(base: dict, pivot: float, stop_loss: float) -> dict:
    return {
        "classification": "watchlist",
        "data": {
            **base,
            "plan_type": "watchlist",
            "pivot_price": pivot,
            "stop_loss_price": stop_loss,
            "alert_trigger": f"Price crosses above ${pivot:.2f} on 1.5x RVOL",
        },
    }


def _reject(symbol: str, reason: str) -> dict:
    return {
        "classification": "rejected",
        "data": {"symbol": symbol, "reason": reason},
    }


def generate_plans(
    data: dict,
    args: argparse.Namespace,
    earnings_map: dict[str, str] | None = None,
    earnings_fetch_failed: bool = False,
) -> dict:
    """Main pipeline: filter, score, size, classify all candidates.

    When ``args.earnings_gate_days`` > 0, actionable/revalidation plans whose
    next earnings report falls within that many trading days are moved to
    ``blocked_earnings`` (and never consume portfolio heat); watchlist entries
    are annotated only.
    """
    exposure = load_exposure(args.current_exposure_json)
    results = data["results"]

    gate_days = int(getattr(args, "earnings_gate_days", 0) or 0)
    gate_enabled = gate_days > 0
    today = datetime.now().date()

    # Sort by composite_score descending (highest priority first)
    results_sorted = sorted(results, key=lambda r: r.get("composite_score", 0), reverse=True)

    actionable = []
    revalidation = []
    watchlist = []
    rejected = []
    deferred = []
    constrained = []
    blocked_earnings = []
    warnings = []

    if gate_enabled and earnings_fetch_failed:
        warnings.append(
            {
                "symbol": "*",
                "code": "EARNINGS_GATE_DEGRADED",
                "message": (
                    "earnings calendar fetch failed; earnings_gate='unknown' for all "
                    "plans — verify earnings dates manually before entry"
                ),
            }
        )

    cumulative_risk_pct = exposure.get("open_risk_pct", 0.0)
    sector_tracker: dict[str, float] = {}

    for result in results_sorted:
        is_valid, warns = validate_result(result)
        if not is_valid:
            symbol = result.get("symbol", "UNKNOWN")
            for w in warns:
                warnings.append({"symbol": symbol, "code": "MISSING_FIELD", "message": w})
            rejected.append({"symbol": symbol, "reason": f"validation: {'; '.join(warns)}"})
            continue

        classified = process_candidate(result, args, cumulative_risk_pct, sector_tracker, exposure)
        cls = classified["classification"]

        if gate_enabled and cls in ("actionable", "revalidation", "watchlist"):
            gate_fields = build_gate_fields(
                classified["data"]["symbol"],
                earnings_map or {},
                gate_days,
                today,
                fetch_failed=earnings_fetch_failed,
            )
            classified["data"].update(gate_fields)
            if (
                cls in ("actionable", "revalidation")
                and gate_fields["earnings_gate"] == GATE_BLOCKED
            ):
                blocked_earnings.append(
                    {
                        **classified["data"],
                        "blocked_reason": (
                            f"earnings in {gate_fields['days_to_earnings']} trading days "
                            f"(gate: {gate_days})"
                        ),
                    }
                )
                continue

        if cls == "actionable":
            actionable.append(classified["data"])
            cumulative_risk_pct += classified["risk_pct"]
            sector = classified["data"]["sector"]
            pos_pct = classified["data"]["trade_plan"]["position_value"] / args.account_size * 100
            sector_tracker[sector] = sector_tracker.get(sector, 0.0) + pos_pct
        elif cls == "revalidation":
            revalidation.append(classified["data"])
        elif cls == "watchlist":
            watchlist.append(classified["data"])
        elif cls == "deferred":
            deferred.append(classified["data"])
        elif cls == "constrained":
            constrained.append(classified["data"])
        else:
            rejected.append(classified["data"])

    total_risk_dollars = sum(a["trade_plan"]["risk_dollars"] for a in actionable)
    total_risk_pct = total_risk_dollars / args.account_size * 100 if args.account_size > 0 else 0
    total_position = sum(a["trade_plan"]["position_value"] for a in actionable)

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "parameters": {
            "account_size": args.account_size,
            "base_risk_pct": args.risk_pct,
            "max_position_pct": args.max_position_pct,
            "max_sector_pct": args.max_sector_pct,
            "max_portfolio_heat_pct": args.max_portfolio_heat_pct,
            "target_r_multiple": args.target_r_multiple,
            "stop_buffer_pct": args.stop_buffer_pct,
            "max_chase_pct": args.max_chase_pct,
            "pivot_buffer_pct": args.pivot_buffer_pct,
            "earnings_gate_days": gate_days,
            "time_stop_trading_days": int(getattr(args, "time_stop_trading_days", 0) or 0),
            "current_exposure": exposure,
        },
        "input_metadata": {
            "source_file": args.input,
            "screener_generated_at": _get_nested(data, "metadata.generated_at"),
            "candidates_in_file": len(data["results"]),
            "screener_total_candidates": _get_nested(data, "summary.total"),
            "input_scope": "top_n_only",
        },
        "summary": {
            "actionable_count": len(actionable),
            "revalidation_count": len(revalidation),
            "watchlist_count": len(watchlist),
            "rejected_count": len(rejected),
            "deferred_count": len(deferred),
            "constrained_count": len(constrained),
            "blocked_earnings_count": len(blocked_earnings),
            "total_risk_dollars": round(total_risk_dollars, 2),
            "total_risk_pct": round(total_risk_pct, 2),
            "total_position_value": round(total_position, 2),
        },
        "actionable_orders": actionable,
        "revalidation": revalidation,
        "watchlist": watchlist,
        "rejected": rejected,
        "deferred": deferred,
        "constrained": constrained,
        "blocked_earnings": blocked_earnings,
        "warnings": warnings,
    }


def generate_markdown(plans: dict) -> str:
    """Generate human-readable markdown from plans."""
    lines = [
        "# Breakout Trade Plan",
        f"**Generated:** {plans['generated_at']}",
        f"**Account Size:** ${plans['parameters']['account_size']:,.0f} | "
        f"**Base Risk:** {plans['parameters']['base_risk_pct']}%",
        "",
        "## Summary",
        f"- Actionable: {plans['summary']['actionable_count']}",
        f"- Revalidation: {plans['summary']['revalidation_count']}",
        f"- Watchlist: {plans['summary']['watchlist_count']}",
        f"- Rejected: {plans['summary']['rejected_count']}",
        f"- Blocked (earnings gate): {plans['summary'].get('blocked_earnings_count', 0)}",
        f"- Total Risk: ${plans['summary']['total_risk_dollars']:,.2f} "
        f"({plans['summary']['total_risk_pct']:.2f}%)",
        "",
    ]

    if plans["actionable_orders"]:
        lines.append("## Actionable Orders\n")
        for i, order in enumerate(plans["actionable_orders"], 1):
            tp = order["trade_plan"]
            lines.extend(
                [
                    f"### {i}. {order['symbol']} — {order.get('company_name', '')}",
                    f"**Rating:** {order['rating_band']} ({order['composite_score']}) | "
                    f"**State:** {order['execution_state']}",
                    "",
                    "| Parameter | Value |",
                    "|-----------|-------|",
                    f"| Signal Entry | ${tp['signal_entry']:.2f} |",
                    f"| Worst Entry | ${tp['worst_entry']:.2f} |",
                    f"| Stop Loss | ${tp['stop_loss_price']:.2f} |",
                    f"| Risk (worst) | {tp['risk_pct_worst']:.1f}% |",
                    f"| Target ({tp['reward_risk_ratio']}R) | ${tp['target_price']:.2f} |",
                    f"| Shares | {tp['shares']} |",
                    f"| Position Value | ${tp['position_value']:,.2f} |",
                    f"| Risk $ | ${tp['risk_dollars']:,.2f} |",
                ]
            )
            if tp.get("time_stop_trading_days"):
                lines.append(f"| Time Stop | {tp['time_stop_rule']} |")
            if order.get("earnings_gate"):
                if order.get("earnings_date"):
                    earnings_note = (
                        f"{order['earnings_date']} "
                        f"({order['days_to_earnings']} trading days, "
                        f"{order['earnings_gate']})"
                    )
                else:
                    earnings_note = f"none within window ({order['earnings_gate']})"
                lines.append(f"| Next Earnings | {earnings_note} |")
            lines.append("")

    if plans["revalidation"]:
        lines.append("## Revalidation (Breakout — needs live confirmation)\n")
        for r in plans["revalidation"]:
            lines.append(
                f"- **{r['symbol']}** — pivot ${r['pivot']:.2f}, "
                f"current ${r['current_price']:.2f}\n"
            )

    if plans["watchlist"]:
        lines.append("## Watchlist\n")
        lines.append("| Symbol | Score | Alert |")
        lines.append("|--------|-------|-------|")
        for w in plans["watchlist"]:
            lines.append(
                f"| {w['symbol']} | {w['composite_score']} | {w.get('alert_trigger', '')} |"
            )
        lines.append("")

    if plans.get("blocked_earnings"):
        gate_days = plans["parameters"].get("earnings_gate_days", 0)
        lines.append(f"## Blocked by Earnings Gate (≤ {gate_days} trading days)\n")
        lines.append("| Symbol | Plan Type | Earnings Date | Trading Days Away |")
        lines.append("|--------|-----------|---------------|-------------------|")
        for b in plans["blocked_earnings"]:
            lines.append(
                f"| {b['symbol']} | {b.get('plan_type', '')} | "
                f"{b.get('earnings_date', '?')} | {b.get('days_to_earnings', '?')} |"
            )
        lines.append("")
        lines.append(
            "*Re-screen these names after their reports — a post-earnings base "
            "is a fresh setup, not a missed one.*"
        )
        lines.append("")

    lines.append("\n---\n*Disclaimer: Not investment advice.*\n")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate breakout trade plans from VCP screener output"
    )
    parser.add_argument("--input", required=True, help="VCP screener JSON path")
    parser.add_argument(
        "--profile",
        default=_default_profile(),
        help=(
            "JSON parameter profile (account_size, risk_pct, ...). Explicit CLI "
            "flags override profile values. Default: $TRADING_PROFILE."
        ),
    )
    parser.add_argument(
        "--account-size",
        type=float,
        default=None,
        help="Account equity ($); required unless provided via --profile",
    )
    parser.add_argument("--risk-pct", type=float, default=0.5, help="Base risk %% per trade")
    parser.add_argument("--max-position-pct", type=float, default=10.0)
    parser.add_argument("--max-sector-pct", type=float, default=30.0)
    parser.add_argument("--max-portfolio-heat-pct", type=float, default=6.0)
    parser.add_argument("--target-r-multiple", type=float, default=2.0)
    parser.add_argument("--stop-buffer-pct", type=float, default=1.0)
    parser.add_argument("--max-chase-pct", type=float, default=2.0)
    parser.add_argument("--pivot-buffer-pct", type=float, default=0.1)
    parser.add_argument(
        "--earnings-gate-days",
        type=int,
        default=0,
        help=(
            "Block actionable/revalidation plans whose next earnings report is "
            "within N trading days (inclusive). 0 = disabled. Earnings dates "
            "come from the public TradingView scanner — no API key required."
        ),
    )
    parser.add_argument(
        "--time-stop-trading-days",
        type=int,
        default=0,
        help=(
            "Annotate each plan with a time-stop rule: exit if < +1R after N "
            "trading days from entry. 0 = disabled."
        ),
    )
    parser.add_argument("--current-exposure-json", default=None)
    parser.add_argument("--output-dir", default=_default_output_dir("plans"))
    return parser


def main(argv: list[str] | None = None):
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--profile", default=_default_profile())
    pre_args, _ = pre.parse_known_args(argv)

    parser = build_arg_parser()
    if pre_args.profile:
        try:
            parser.set_defaults(**load_profile(pre_args.profile, PLANNER_PROFILE_KEYS))
        except (OSError, ValueError) as exc:
            print(f"Error: cannot load profile '{pre_args.profile}': {exc}", file=sys.stderr)
            sys.exit(1)

    args = parser.parse_args(argv)
    if args.account_size is None:
        parser.error("--account-size is required (pass it directly or via --profile)")

    data = load_input(args.input)

    earnings_map: dict[str, str] = {}
    earnings_fetch_failed = False
    if int(args.earnings_gate_days or 0) > 0:
        symbols = [
            r.get("symbol") for r in data["results"] if isinstance(r, dict) and r.get("symbol")
        ]
        try:
            earnings_map = fetch_earnings_map(symbols)
        except EarningsFetchError as exc:
            earnings_fetch_failed = True
            print(
                f"Warning: earnings gate degraded to 'unknown': {exc}",
                file=sys.stderr,
            )

    plans = generate_plans(
        data, args, earnings_map=earnings_map, earnings_fetch_failed=earnings_fetch_failed
    )

    os.makedirs(args.output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    json_file = os.path.join(args.output_dir, f"breakout_trade_plan_{ts}.json")
    with open(json_file, "w") as f:
        json.dump(plans, f, indent=2, default=str)
    print(f"JSON plan saved to: {json_file}")

    md_file = os.path.join(args.output_dir, f"breakout_trade_plan_{ts}.md")
    with open(md_file, "w") as f:
        f.write(generate_markdown(plans))
    print(f"Markdown plan saved to: {md_file}")

    print(
        f"\nActionable: {plans['summary']['actionable_count']} | "
        f"Revalidation: {plans['summary']['revalidation_count']} | "
        f"Watchlist: {plans['summary']['watchlist_count']} | "
        f"Blocked (earnings): {plans['summary']['blocked_earnings_count']}"
    )


if __name__ == "__main__":
    main()
