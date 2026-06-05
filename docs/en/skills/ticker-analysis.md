---
layout: default
title: "Ticker Analysis"
grand_parent: English
parent: Skill Guides
nav_order: 55
lang_peer: /ja/skills/ticker-analysis/
permalink: /en/skills/ticker-analysis/
generated: true
---

# Ticker Analysis
{: .no_toc }

Полный комплексный анализ одной акции по тикеру — новостной фон, фундаментал и технический анализ через TradingView MCP — с итоговым сводным отчётом и конкретными точками входа на покупку/продажу. Use whenever the user asks to analyze a single ticker ("проанализируй AAPL", "сделай аналитику по TSLA", "дай полный разбор NVDA", "analyze MSFT", "что делать с PLTR", "стоит ли покупать BSX", "разбор тикера X"). Координирует уже установленные скилы market-news-analyst, us-stock-analysis (fundamental), technical-analyst, chart-analysis и (опционально, только по явной просьбе) signals-alerts, читает живой график через mcp__tradingview__* и сохраняет четыре markdown-файла плюс daily/weekly скриншоты в `results/analysis/TICKER/YYYY-MM-DD/`. Алерты в TradingView **не создаются по умолчанию** — только если пользователь явно попросил («создай алерты», «настрой триггеры», «sync alerts», «+alerts»).
{: .fs-6 .fw-300 }

<span class="badge badge-free">No API</span>

[View Source on GitHub](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/ticker-analysis){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>Table of Contents</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. Overview

# Ticker Analysis — Сводный анализ одной акции

---

## 2. Prerequisites

- Live chart reading via TradingView Desktop MCP (CDP); coordinates other installed skills; News background via WebSearch / WebFetch
- Python 3.9+ recommended

---

## 3. Quick Start

Invoke this skill by describing your analysis needs to Claude.

---

## 4. Workflow

See the skill's SKILL.md for the complete workflow.

---

## 5. Resources

This skill uses built-in Claude capabilities without external scripts or references.
