#!/usr/bin/env python3
"""
Bottom Flow Divergence Screener - Report Generation

Renders graded candidates into a structured JSON file and a human-readable
Markdown watchlist, grouped by grade (A / B-accum / B-fund). Detection-only
artifacts — no orders are sent. A human reviews these against a broker before
any entry.
"""

from __future__ import annotations

import json
import os

from scorer import GRADE_GUIDANCE, GRADE_ORDER


def build_result_record(metrics: dict, verdict: dict) -> dict:
    """Merge metrics + classification into one flat, JSON-serializable record."""
    return {
        "symbol": metrics["symbol"],
        "sector": metrics["sector"],
        "grade": verdict["grade"],
        "score": verdict["score"],
        "flow_profile": verdict["flow_profile"],
        "turning": verdict["turning"],
        "survivable": verdict["survivable"],
        "organic_warn": verdict["organic_warn"],
        "risk_flags": verdict["risk_flags"],
        "fundamental_ok": verdict["fundamental_ok"],
        "accumulation_ok": verdict["accumulation_ok"],
        "metrics": {
            "close": metrics["close"],
            "low_52w": metrics["low_52w"],
            "high_52w": metrics["high_52w"],
            "pct_off_low": _round(metrics["pct_off_low"]),
            "pct_off_high": _round(metrics["pct_off_high"]),
            "perf_y": _round(metrics["perf_y"]),
            "perf_6m": _round(metrics["perf_6m"]),
            "perf_3m": _round(metrics["perf_3m"]),
            "rsi": _round(metrics["rsi"]),
            "rev_ttm": _round(metrics["rev_ttm"]),
            "rev_qoq": _round(metrics["rev_qoq"]),
            "ocf": metrics["ocf"],
            "fcf": metrics["fcf"],
            "fcf_margin": _round(metrics["fcf_margin"]),
            "gross_margin": _round(metrics["gross_margin"]),
            "oper_margin": _round(metrics["oper_margin"]),
            "net_income": metrics["net_income"],
            "mfi": _round(metrics["mfi"]),
            "cmf": _round(metrics["cmf"], 3),
            "altman_z": _round(metrics["altman_z"]),
            "current_ratio": _round(metrics["current_ratio"]),
            "mkt_cap": metrics["mkt_cap"],
            "avg_vol": metrics["avg_vol"],
        },
    }


def _round(value, ndigits: int = 2):
    return round(value, ndigits) if isinstance(value, (int, float)) else value


def _pct(value) -> str:
    return "—" if value is None else f"{value:+.0f}%"


def _money(value) -> str:
    if value is None:
        return "—"
    a = abs(value)
    if a >= 1e9:
        return f"{value / 1e9:.2f}B"
    if a >= 1e6:
        return f"{value / 1e6:.0f}M"
    return f"{value:.0f}"


def _tags(rec: dict) -> str:
    parts = []
    parts.append("▲turning" if rec["turning"] else "▽falling")
    parts.extend(rec["flow_profile"])
    if rec["organic_warn"]:
        parts.append("⚠M&A?")
    parts.extend(rec["risk_flags"])
    return " ".join(parts)


def render_markdown(records: list[dict], meta: dict) -> str:
    lines = [
        "# Bottom Flow Divergence Screener",
        "",
        f"- **Generated:** {meta.get('generated_at', '')}",
        f"- **As of:** {meta.get('as_of', 'live')}",
        f"- **Universe scanned:** {meta.get('scanned', 0)} names "
        f"(near-low gate: ≤{meta.get('near_low_pct')}% above 52w low, "
        f"≥{meta.get('min_drawdown_pct')}% below 52w high)",
        f"- **Candidates:** {len(records)} "
        f"(A={_count(records, 'A')}, B-accum={_count(records, 'B-accum')}, "
        f"B-fund={_count(records, 'B-fund')})",
        f"- **Gates:** require-turn={meta.get('require_turn')}, "
        f"require-survivable={meta.get('require_survivable')}",
        "",
        "> Detection-only. Price is on the floor while a flow signal refuses to "
        "confirm it. Verify fundamentals (watch ⚠M&A? — growth may be inorganic) "
        "and chart structure before any entry.",
        "",
    ]
    for grade in ("A", "B-accum", "B-fund"):
        group = [r for r in records if r["grade"] == grade]
        if not group:
            continue
        lines.append(f"## {grade} — {GRADE_GUIDANCE[grade]}")
        lines.append("")
        lines.append(
            "| # | Ticker | Sector | Score | %off Lo | %off Hi | PerfY | P3m | revTTM | revQoQ "
            "| OCF | FCFm | CMF | MFI | mcap | Tags |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(group, 1):
            m = r["metrics"]
            lines.append(
                f"| {i} | **{r['symbol']}** | {r.get('sector') or '—'} | {r['score']:.0f} | "
                f"{_round(m['pct_off_low'], 0):.0f}% | {_round(m['pct_off_high'], 0):.0f}% | "
                f"{_pct(m['perf_y'])} | {_pct(m['perf_3m'])} | {_pct(m['rev_ttm'])} | "
                f"{_pct(m['rev_qoq'])} | {_money(m['ocf'])} | "
                f"{_round(m['fcf_margin'], 0) if m['fcf_margin'] is not None else '—'}% | "
                f"{m['cmf'] if m['cmf'] is not None else '—'} | "
                f"{_round(m['mfi'], 0) if m['mfi'] is not None else '—'} | "
                f"{_money(m['mkt_cap'])} | {_tags(r)} |"
            )
        lines.append("")
    lines.append(
        "*Source: TradingView Stock Screener (scanner.tradingview.com). "
        "Informational only — verify before trading.*"
    )
    return "\n".join(lines)


def _count(records: list[dict], grade: str) -> int:
    return sum(1 for r in records if r["grade"] == grade)


def write_reports(records: list[dict], meta: dict, output_dir: str, screen_name: str) -> tuple:
    os.makedirs(output_dir, exist_ok=True)
    stamp = meta.get("file_stamp", "")
    base = f"{screen_name}_{stamp}" if stamp else screen_name
    md_path = os.path.join(output_dir, f"{base}.md")
    json_path = os.path.join(output_dir, f"{base}.json")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(records, meta))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(
            {"meta": meta, "candidates": sorted(records, key=_sort_key)},
            fh,
            indent=2,
        )
    return md_path, json_path


def _sort_key(rec: dict):
    return (GRADE_ORDER.get(rec["grade"], 9), -rec["score"])
