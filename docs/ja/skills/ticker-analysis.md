---
layout: default
title: "Ticker Analysis"
grand_parent: 日本語
parent: スキルガイド
nav_order: 55
lang_peer: /en/skills/ticker-analysis/
permalink: /ja/skills/ticker-analysis/
generated: true
---

# Ticker Analysis
{: .no_toc }

Полный комплексный анализ одной акции по тикеру — новостной фон, фундаментал и технический анализ через TradingView MCP — с итоговым сводным отчётом и конкретными точками входа на покупку/продажу. Use whenever the user asks to analyze a single ticker ("проанализируй AAPL", "сделай аналитику по TSLA", "дай полный разбор NVDA", "analyze MSFT", "что делать с PLTR", "стоит ли покупать BSX", "разбор тикера X"). Координирует уже установленные скилы market-news-analyst, us-stock-analysis (fundamental), technical-analyst, chart-analysis и (опционально, только по явной просьбе) signals-alerts, читает живой график через mcp__tradingview__* и сохраняет четыре markdown-файла плюс daily/weekly скриншоты в `results/analysis/TICKER/YYYY-MM-DD/`. Алерты в TradingView **не создаются по умолчанию** — только если пользователь явно попросил («создай алерты», «настрой триггеры», «sync alerts», «+alerts»).
{: .fs-6 .fw-300 }

<span class="badge badge-free">API不要</span>

[GitHubでソースを見る](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/ticker-analysis){: .btn .fs-5 .mb-4 .mb-md-0 }

> **Note:** This page has not yet been translated into Japanese.
> Please refer to the [English version]({{ '/en/skills/ticker-analysis/' | relative_url }}) for the full guide.
{: .warning }

---

[English版ガイドを見る]({{ '/en/skills/ticker-analysis/' | relative_url }}){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
