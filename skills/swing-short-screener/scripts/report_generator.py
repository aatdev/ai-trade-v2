#!/usr/bin/env python3
"""
Swing Short Screener - Report Generation

Renders the screening results into a structured JSON file and a human-readable
Markdown watchlist. Both are detection-only artifacts — no orders are sent.
"""

import json
import os
from typing import Optional

from scorer import COMPONENT_LABELS

GRADE_GUIDANCE = {
    "A": "Clean Stage 4 weakness — prime swing-short candidate.",
    "B": "Strong weakness — tradable on a confirmed break.",
    "C": "Developing weakness — watchlist, wait for cleaner break.",
    "D": "Weak signal — skip.",
}


def build_result_record(symbol: str, name: str, sector: str, metrics: dict, score: dict) -> dict:
    """Merge metrics + score into one flat result record."""
    return {
        "symbol": symbol,
        "name": name,
        "sector": sector,
        "composite_score": score["composite_score"],
        "grade": score["grade"],
        "raw_grade": score["raw_grade"],
        "state_cap_applied": score["state_cap_applied"],
        "oversold_extended": score["oversold_extended"],
        "squeeze_risk": score.get("squeeze_risk", False),
        "squeeze_reason": score.get("squeeze_reason"),
        "sector_fight": score.get("sector_fight", False),
        "sector_etf": score.get("sector_etf"),
        "sector_rs": score.get("sector_rs"),
        "sector_leadership": score.get("sector_leadership"),
        "components": score["components"],
        "strongest_signal": score["strongest_signal"],
        "trade_levels": score["trade_levels"],
        "metrics": {
            "price": metrics["price"],
            "ma50": metrics["ma50"],
            "ma200": metrics["ma200"],
            "below_ma50": metrics["below_ma50"],
            "below_ma200": metrics["below_ma200"],
            "death_cross": metrics["death_cross"],
            "rsi14": metrics["rsi14"],
            "vol_ratio": metrics["vol_ratio"],
            "avg_dollar_vol": metrics["avg_dollar_vol"],
            "broke_support": metrics["broke_support"],
            "stock_return": metrics["stock_return"],
            "max_up_day_10": metrics.get("max_up_day_10"),
            "pct_above_low_20": metrics.get("pct_above_low_20"),
        },
    }


def generate_json_report(results: list[dict], meta: dict, output_dir: str, timestamp: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"swing_short_screener_{timestamp}.json")
    payload = {"meta": meta, "candidates": results}
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    return path


def generate_markdown_report(
    results: list[dict], meta: dict, output_dir: str, timestamp: str
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"swing_short_screener_{timestamp}.md")

    lines: list[str] = []
    lines.append("# Swing Short Screener — Stage 4 Weakness Watchlist")
    lines.append("")
    lines.append(f"**As of:** {meta.get('as_of', timestamp)}  ")
    lines.append(f"**Universe:** {meta.get('universe_size', 0)} symbols  ")
    lines.append(f"**Candidates (graded A-C):** {len(results)}  ")
    lines.append(f"**SPY {meta.get('rs_lookback', 63)}d return:** {_pct(meta.get('spy_return'))}")
    lines.append("")
    lines.append(
        "> Detection-only. Confirm borrow/locate and SSR (Rule 201) at "
        "the broker before any entry. Not a sell-short signal."
    )
    lines.append("")

    if not results:
        lines.append("_No candidates passed the weakness filter._")
        _write(path, lines)
        return path

    lines.append("## Ranked Candidates")
    lines.append("")
    lines.append(
        "| Rank | Symbol | Sector | Grade | Score | Price | Entry | Stop | Target (2R) | "
        "Strongest Signal |"
    )
    lines.append(
        "|------|--------|--------|-------|-------|-------|-------|------|-------------|"
        "------------------|"
    )
    for i, r in enumerate(results, 1):
        tl = r["trade_levels"]
        cap = " ★" if r["state_cap_applied"] else ""
        lines.append(
            f"| {i} | {r['symbol']} | {r.get('sector') or '—'} | {r['grade']}{cap} | "
            f"{r['composite_score']} | {r['metrics']['price']} | {tl['entry']} | {tl['stop']} | "
            f"{tl['target_2r']} | "
            f"{COMPONENT_LABELS.get(r['strongest_signal'], r['strongest_signal'])} |"
        )
    lines.append("")
    lines.append(
        "★ = grade capped at C (oversold/extended, squeeze, or counter-sector — bounce risk)."
    )
    lines.append("")

    lines.append("## Component Breakdown")
    lines.append("")
    for r in results:
        lines.append(f"### {r['symbol']} — {r['name']} ({r['sector']})")
        lines.append("")
        lines.append(
            f"- **Grade {r['grade']}** (composite {r['composite_score']}) — "
            f"{GRADE_GUIDANCE.get(r['grade'], '')}"
        )
        for key, label in COMPONENT_LABELS.items():
            lines.append(f"  - {label}: {r['components'][key]}")
        m = r["metrics"]
        lines.append(
            f"- Price {m['price']} | MA50 {m['ma50']} | MA200 {m['ma200']} | "
            f"RSI14 {m['rsi14']} | vol×{m['vol_ratio']} | RS {_pct(m['stock_return'])}"
        )
        if r["oversold_extended"]:
            lines.append(
                "- ⚠️ Oversold/extended — high mean-reversion bounce risk; prefer a "
                "lower-high retest entry over chasing the breakdown."
            )
        if r.get("squeeze_risk"):
            lines.append(
                f"- ⚠️ Squeeze risk — {r.get('squeeze_reason') or 'recent counter-trend pop'}; "
                "the short is being run in. Wait for a fresh lower high."
            )
        if r.get("sector_fight"):
            lines.append(
                f"- ⚠️ Counter-sector — {r.get('sector_etf')} leading SPY "
                f"({(r.get('sector_rs') or 0):+.0f}%); shorting into a strong group. "
                "Prefer shorts in lagging sectors."
            )
        lines.append("")

    _write(path, lines)
    return path


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.1f}%"


def _write(path: str, lines: list[str]) -> None:
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
