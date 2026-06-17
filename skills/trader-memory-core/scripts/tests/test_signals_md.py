"""Tests for signals_md.py — parsing the analysis/signals.md journal."""

import signals_md

# A multi-block journal exercising long, short, HOLD, alt-line skipping and the
# report-link line — the real format the ticker-analysis skill writes.
JOURNAL = """# Trading Signals Journal

---

## 2026-06-11 — ALB — 🟢 BUY (oversold bounce)

- **Фаза:** глубокая коррекция
- **Trigger для Long:** close 1D > $165 при удержании выше MA200 ($144)
- **Entry (Long):** $155.00–$165.00
- **Stop:** $140.00
- **T1 / T2 / T3:** $168.00 / $182.00 / $200.00
- **Альтернатива (Short):** close < $140 → entry $138–142, stop $153, T1/T2/T3 $125/$110/$95
- **Полный отчёт:** [`trading-data/analysis/ALB/2026-06-11/report.md`](./ALB/2026-06-11/report.md)

---

## 2026-06-12 — ADBE — 🔴 SELL (Stage 4 breakdown)

- **Trigger для Short:** дневное закрытие < $210.00 (продолжение)
- **Entry (Short):** $208.00–$211.00
- **Stop:** $218.50
- **T1 / T2 / T3:** $200.00 / $190.00 / $180.00
- **Альтернатива (Long):** close > $233 → entry $233–236, stop $217.50
- **Полный отчёт:** [`trading-data/analysis/ADBE/2026-06-12/report.md`](./ADBE/2026-06-12/report.md)

---

## 2026-06-12 — ALLE — 🟡 HOLD (waiting for breakout)

- **Trigger для Long:** close 1D > $135.50
- **Stop:** $129.40
- **T1 / T2 / T3:** $138.50 / $144.50 / $148.80
"""


def test_parses_long_block():
    records = signals_md.parse_signals_md(JOURNAL, ticker="ALB")
    assert len(records) == 1
    r = records[0]
    assert r["ticker"] == "ALB"
    assert r["date"] == "2026-06-11"
    assert r["direction"] == "long"
    # First $-number on the Trigger line — not the MA200 ($144) reference.
    assert r["trigger"] == 165.0
    assert r["stop"] == 140.0
    assert (r["t1"], r["t2"], r["t3"]) == (168.0, 182.0, 200.0)
    assert r["entry_low"] == 155.0 and r["entry_high"] == 165.0
    assert r["report"] == "trading-data/analysis/ALB/2026-06-11/report.md"


def test_parses_short_block():
    records = signals_md.parse_signals_md(JOURNAL, ticker="ADBE")
    assert len(records) == 1
    r = records[0]
    assert r["direction"] == "short"
    assert r["trigger"] == 210.0
    assert r["stop"] == 218.5
    assert r["t1"] == 200.0


def test_hold_block_skipped():
    # ALLE is 🟡 HOLD -> no armable record.
    assert signals_md.parse_signals_md(JOURNAL, ticker="ALLE") == []


def test_alt_line_does_not_override_levels():
    # The «Альтернатива» line carries its own stop/targets; they must be ignored.
    r = signals_md.parse_signals_md(JOURNAL, ticker="ALB")[0]
    assert r["stop"] == 140.0  # not 153 from the alt line


def test_all_tickers_returned_without_filter():
    records = signals_md.parse_signals_md(JOURNAL)
    tickers = {r["ticker"] for r in records}
    assert tickers == {"ALB", "ADBE"}  # ALLE (HOLD) excluded


def test_latest_block_per_ticker_wins():
    journal = (
        JOURNAL
        + """
---

## 2026-06-16 — ALB — 🟢 BUY (re-analysis)

- **Trigger для Long:** close 1D > $170.00
- **Stop:** $150.00
- **T1 / T2 / T3:** $180.00 / $190.00 / $200.00
"""
    )
    r = signals_md.parse_signals_md(journal, ticker="ALB")[0]
    assert r["date"] == "2026-06-16"
    assert r["trigger"] == 170.0
    assert r["stop"] == 150.0


def test_missing_levels_returns_none():
    journal = """## 2026-06-11 — XYZ — 🟢 BUY

- **Trigger для Long:** close > $50.00
"""
    # No Stop / T1 -> unusable.
    assert signals_md.parse_signals_md(journal, ticker="XYZ") == []
