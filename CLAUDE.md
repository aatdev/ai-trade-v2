# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

Claude Skills for equity investors and traders. Each skill packages domain-specific prompts, knowledge bases, and helper scripts for market analysis, technical charting, calendar monitoring, and trading strategy development. Skills work in both Claude's web app and Claude Code.

⚠️ Some skills require paid API subscriptions (FMP, FINVIZ Elite, Alpaca). See [API Key Management](#api-key-management).

## Skill Architecture

Each skill follows a standardized directory structure under `skills/`:

```
skills/<skill-name>/
├── SKILL.md              # Required: skill definition with YAML frontmatter
├── references/           # Knowledge bases Claude reads selectively (markdown)
├── scripts/              # Executable Python scripts (handle I/O; never auto-loaded)
└── assets/               # Templates and resources for output generation
```

**SKILL.md format:**
- YAML frontmatter with `name` (must match the directory name) and `description` (defines when the skill triggers)
- Body contains workflow instructions in imperative/infinitive form, written for Claude to execute

**Progressive loading:** frontmatter loads first (detection) → SKILL.md body on invocation → references conditionally → scripts execute on demand.

**Output generation:** skills save reports (markdown + JSON) to `reports/`, filename convention `<skill>_<analysis-type>_<date>.md`/`.json`, using templates from `assets/`. Scripts should default `--output-dir` to `reports/` (or be invoked with `--output-dir reports/`).

## Common Development Tasks

### Creating a New Skill

Use the skill-creator plugin (Claude Code): it asks clarification questions, creates the directory structure, generates the SKILL.md template, and packages the skill.

**MANDATORY: After creating or committing a new skill, complete ALL of the following:**

1. **Generate documentation pages** (auto-gen handles EN page + JA stub + index updates):
   ```bash
   python3 scripts/generate_skill_docs.py --skill <skill-name>
   ```
2. **Add to catalog category sections** in `docs/en/skill-catalog.md` and `docs/ja/skill-catalog.md`
3. **Add to API Requirements Matrix** in both catalog files
4. **Add to README** descriptions in `README.md` (English) and `README.ja.md` (Japanese)
5. If the skill requires API keys, add to the API Requirements table in `README.md` and the API要件 section in `README.ja.md`
6. If a new category is needed, create it in both READMEs and both catalogs

> **Pre-commit enforcement:** The `docs-completeness` hook blocks commits if any `skills/*/SKILL.md` exists without corresponding `docs/en/skills/<name>.md` and `docs/ja/skills/<name>.md`. Run the generate command above to fix.

### Creating Documentation Site Pages

```bash
# Generate 6-section EN page + JA stub for one skill (also updates both index tables)
python3 scripts/generate_skill_docs.py --skill <skill-name>

# Regenerate all auto-generated pages (ONLY pages marked `generated: true`)
python3 scripts/generate_skill_docs.py --overwrite
```

> **Skill doc ownership / drift gate:** Committed `docs/{en,ja}/skills/*.md` are source-of-truth. A page is generator-owned only if its frontmatter has `generated: true`; anything else is hand-maintained and **protected** — `--overwrite` refuses it (`--force` is the CI-forbidden escape hatch). The `skill-docs-drift` pre-commit hook + CI run `generate_skill_docs.py --check`, which compares **only** `generated: true` pages. See `docs/README.md` → "Skill Doc Ownership".

For key skills, write a hand-maintained 10-section ★ guide (Overview / Prerequisites / Quick Start / How It Works / Usage Examples / Understanding the Output / Tips / Combining with Other Skills / Troubleshooting / Reference) — template in `docs/README.md`.

**Auto-gen vs manual:**

| Task | Auto-gen | Manual |
|------|----------|--------|
| EN doc page (`docs/en/skills/<name>.md`) | ✅ | -- |
| JA doc stub (`docs/ja/skills/<name>.md`) | ✅ | -- |
| Index table (`docs/{en,ja}/skills/index.md`) | ✅ | -- |
| Catalog category section (`docs/{en,ja}/skill-catalog.md`) | -- | ✅ |
| Catalog API Requirements Matrix | -- | ✅ |
| README.md / README.ja.md | -- | ✅ |

### Testing Skills

Copy the skill folder to the Claude Code Skills directory, restart Claude Code, then trigger the skill with input matching its description. Verify: frontmatter loads, references load when needed, scripts run with proper error handling, output matches the expected format.

### Code Generation (TDD)

When generating or modifying code in this repository, use a TDD-first workflow:

1. Write or update tests first (expected to fail initially).
2. Implement the minimal code change needed to pass tests.
3. Refactor while keeping tests green.
4. Run the relevant test suite before finishing.

If no test exists for the changed behavior, add one whenever practical.

### Pre-commit Hooks

> **Maintainer operations:** for the full regenerate / drift-gate / scheduled-job runbook, see [`docs/dev/maintenance-runbook.md`](docs/dev/maintenance-runbook.md).

Install after cloning: `pre-commit install && pre-commit install --hook-type pre-push`

Standard hooks: trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-merge-conflict, check-added-large-files (500KB), ruff (lint + format), codespell, detect-secrets.

Local hooks (`scripts/hooks/`, config in `.pre-commit-config.yaml`):

| Hook | What it checks |
|------|----------------|
| no-absolute-paths | `/Users/username/` path leaks in public repo (suppress with `# noqa: absolute-path`) |
| skill-frontmatter | SKILL.md `name` matches directory, `description` exists |
| docs-completeness | Every `skills/*/SKILL.md` has EN + JA doc pages |

Pre-push: `pytest-pre-push` runs all skill-level tests via `scripts/run_all_tests.sh`.

### API Key Management

#### API Requirements by Skill

The table below is **auto-generated** from `skills-index.yaml` by `scripts/generate_catalog_from_index.py`. To update a row, edit the skill's `integrations[]` in the index and re-run the generator. The 3-column shape (FMP / FINVIZ / Alpaca) is preserved so existing setup instructions still apply; non-paid integrations (CSV, image, WebSearch, MCP, calculation-only, etc.) surface in the Notes column.

<!-- skills-index:start name="api-matrix" -->
<!-- This table is auto-generated from skills-index.yaml by scripts/generate_catalog_from_index.py. Do not edit by hand — edit the index and re-run the generator. -->

| Skill | FMP API | FINVIZ Elite | Alpaca | Notes |
|-------|---------|--------------|--------|-------|
| **Backtest Expert** | ❌ Not used | ❌ Not used | ❌ Not used | User provides strategy parameters |
| **Breadth Chart Analyst** | ❌ Not used | ❌ Not used | ❌ Not used | Chart screenshot input |
| **Breakout Trade Planner** | ❌ Not used | ❌ Not used | ❌ Not used | Consumes VCP screener output; pure calculation + Alpaca order templates; Optional earnings-date gate (--earnings-gate-days) via public scanner.tradingview.com; no API key |
| **CANSLIM Screener** | ✅ Required | ❌ Not used | ❌ Not used | US stock fundamentals via FMP |
| **Data Quality Checker** | ❌ Not used | ❌ Not used | ❌ Not used | Local markdown validation; works offline |
| **Dividend Growth Pullback Screener** | ❌ Not used | 🟡 Optional | ❌ Not used | FINVIZ Elite pre-screen widens universe beyond S&P 500 |
| **Downtrend Duration Analyzer** | ❌ Not used | ❌ Not used | ❌ Not used | Duration analysis from market data; pure calculation |
| **Dual Axis Skill Reviewer** | ❌ Not used | ❌ Not used | ❌ Not used | Deterministic scoring + optional LLM review |
| **Earnings Calendar** | ✅ Required | ❌ Not used | ❌ Not used | Financial Modeling Prep API |
| **Earnings Trade Analyzer** | ✅ Required | ❌ Not used | ❌ Not used | Financial Modeling Prep API |
| **Economic Calendar Fetcher** | ✅ Required | ❌ Not used | ❌ Not used | Financial Modeling Prep API |
| **Edge Candidate Agent** | 🟡 Optional | ❌ Not used | ❌ Not used | Optional OHLCV via FMP for edge ticket export |
| **Edge Concept Synthesizer** | ❌ Not used | ❌ Not used | ❌ Not used | Synthesizes detector tickets and hints into edge concepts |
| **Edge Hint Extractor** | ❌ Not used | ❌ Not used | ❌ Not used | Extracts hints from observations/news; pure calculation |
| **Edge Pipeline Orchestrator** | ❌ Not used | ❌ Not used | ❌ Not used | Orchestrates edge pipeline subskills via subprocess |
| **Edge Signal Aggregator** | ❌ Not used | ❌ Not used | ❌ Not used | Aggregates signals from edge-finding skills |
| **Edge Strategy Designer** | ❌ Not used | ❌ Not used | ❌ Not used | Converts edge concepts into strategy drafts |
| **Edge Strategy Reviewer** | ❌ Not used | ❌ Not used | ❌ Not used | Deterministic scoring on local YAML drafts |
| **Exposure Coach** | ❌ Not used | ❌ Not used | ❌ Not used | Synthesizes signals from other skills; pure calculation |
| **FTD Detector** | ❌ Not used | ❌ Not used | ❌ Not used | Daily S&P 500 / QQQ OHLCV via the shared TradingView data layer; no API key |
| **Finviz Screener** | ❌ Not used | 🟡 Optional | ❌ Not used | FINVIZ Elite API |
| **IBD Distribution Day Monitor** | ❌ Not used | ❌ Not used | ❌ Not used | Daily QQQ/SPY OHLCV via the shared TradingView data layer; no API key |
| **Institutional Flow Tracker** | ✅ Required | ❌ Not used | ❌ Not used | Financial Modeling Prep API |
| **Kanchi Dividend Review Monitor** | 🟡 Optional (Recommended) | ❌ Not used | ❌ Not used | Dividend / price monitoring via FMP |
| **Kanchi Dividend SOP** | 🟡 Optional (Recommended) | ❌ Not used | ❌ Not used | US dividend stock data via FMP |
| **Kanchi Dividend US Tax Accounting** | ❌ Not used | ❌ Not used | ❌ Not used | US tax workflow guidance; pure calculation |
| **Macro Regime Detector** | ❌ Not used | ❌ Not used | ❌ Not used | ETF ratios and treasury yields via the shared TradingView data layer; no API key |
| **Market Breadth Analyzer** | ❌ Not used | ❌ Not used | ❌ Not used | TraderMonty public CSV; no API key required |
| **Market Environment Analysis** | ❌ Not used | ❌ Not used | ❌ Not used | Global market data via WebSearch / WebFetch; Optional chart image inputs for technical interpretation |
| **Market News Analyst** | ❌ Not used | ❌ Not used | ❌ Not used | Web search / fetch; Optional — reads the live TradingView News Flow tab (headlines + full story bodies) via TradingView Desktop MCP (CDP); applies instrument/country news filters |
| **Market Top Detector** | ❌ Not used | ❌ Not used | ❌ Not used | Index/ETF/VIX quotes, history and VIX term structure via the shared TradingView data layer; no API key; TraderMonty 200DMA breadth CSV (auto-fetch); 50DMA breadth and put/call entered manually |
| **Options Strategy Advisor** | 🟡 Optional | ❌ Not used | ❌ Not used | Financial Modeling Prep API |
| **PEAD Screener** | ✅ Required | ❌ Not used | ❌ Not used | Financial Modeling Prep API |
| **Pair Trade Screener** | ✅ Required | ❌ Not used | ❌ Not used | Financial Modeling Prep API |
| **Parabolic Short Trade Planner** | ✅ Required | ❌ Not used | 🟡 Optional | Financial Modeling Prep API |
| **Portfolio Manager** | ❌ Not used | ❌ Not used | ✅ Required | Alpaca brokerage MCP/API |
| **Position Sizer** | ❌ Not used | ❌ Not used | ❌ Not used | Pure calculation; works offline |
| **Save Note** | ❌ Not used | ❌ Not used | ❌ Not used | Writes to the MyNotes base dir ($MYNOTES_DIR / .envrc, default ~/Documents/MyNotes) |
| **Scenario Analyzer** | ❌ Not used | ❌ Not used | ❌ Not used | Headline / news search via WebSearch |
| **Sector Analyst** | ❌ Not used | ❌ Not used | ❌ Not used | Chart screenshot input |
| **Send Telegram** | ❌ Not used | ❌ Not used | ❌ Not used | Telegram Bot API (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars) |
| **Signal Postmortem** | ❌ Not used | ❌ Not used | ❌ Not used | Postmortem framework; pure calculation |
| **Signals Alerts** | ❌ Not used | ❌ Not used | ❌ Not used | TradingView Desktop via CDP (vendored tv CLI); reads local results/analysis/signals.md |
| **Skill Designer** | ❌ Not used | ❌ Not used | ❌ Not used | Generates skill scaffolding from idea specs |
| **Skill Idea Miner** | ❌ Not used | ❌ Not used | ❌ Not used | Mines session logs for skill ideas |
| **Skill Integration Tester** | ❌ Not used | ❌ Not used | ❌ Not used | Validates multi-skill workflow contracts |
| **Stanley Druckenmiller Investment** | ❌ Not used | ❌ Not used | ❌ Not used | Synthesizes outputs from upstream skills; pure calculation |
| **Strategy Pivot Designer** | ❌ Not used | ❌ Not used | ❌ Not used | Pivot proposal generator; pure calculation |
| **Swing Short Screener** | ❌ Not used | ❌ Not used | ❌ Not used | S&P 500 / custom-universe OHLCV via the shared TradingView data layer; offline --fixture mode for testing |
| **Technical Analyst** | ❌ Not used | ❌ Not used | ❌ Not used | Chart screenshot input |
| **Theme Detector** | 🟡 Optional | 🟡 Optional (Recommended) | ❌ Not used | Financial Modeling Prep API |
| **Ticker Analysis** | ❌ Not used | ❌ Not used | ❌ Not used | Live chart reading via TradingView Desktop MCP (CDP); coordinates other installed skills; News background via WebSearch / WebFetch |
| **Trade Hypothesis Ideator** | ❌ Not used | ❌ Not used | ❌ Not used | Hypothesis generation from journal/data inputs; pure calculation |
| **Trade Performance Coach** | ❌ Not used | ❌ Not used | ❌ Not used | Works from local trader-memory / postmortem / journal records; no network or paid API required |
| **Trader Memory Core** | ❌ Not used | ❌ Not used | ❌ Not used | MAE/MFE postmortem prices via the shared TradingView data layer; no API key |
| **Trading Skills Navigator** | ❌ Not used | ❌ Not used | ❌ Not used | Reads local skills-index.yaml + workflows/*.yaml (or bundled snapshot); no network |
| **TradingView Screener** | ❌ Not used | ❌ Not used | ❌ Not used | Public scanner.tradingview.com endpoint (All Stocks tab); no API key or auth required |
| **US Market Bubble Detector** | ❌ Not used | ❌ Not used | ❌ Not used | User provides indicators |
| **US Stock Analysis** | ❌ Not used | ❌ Not used | ❌ Not used | User provides data |
| **Uptrend Analyzer** | ❌ Not used | ❌ Not used | ❌ Not used | Monty Uptrend Ratio Dashboard CSV; no API key required |
| **VCP Screener** | ❌ Not used | ❌ Not used | ❌ Not used | S&P 500 OHLCV via the shared TradingView data layer (vendored tv CLI / metrics cache); no API key |
| **Value Dividend Screener** | ❌ Not used | 🟡 Optional | ❌ Not used | FINVIZ Elite pre-screen widens universe beyond S&P 500 |
<!-- skills-index:end name="api-matrix" -->

> Note: a skill listed as `❌ Not used` for FMP / FINVIZ / Alpaca may still need WebSearch, public CSVs, chart screenshots, or other non-paid inputs. See each skill's full `integrations[]` entry in `skills-index.yaml` for the complete picture.

#### API Key Setup

```bash
export FMP_API_KEY=your_key_here          # Financial Modeling Prep
export FINVIZ_API_KEY=your_key_here       # FINVIZ Elite
export ALPACA_API_KEY="your_api_key_id"   # Alpaca (+ ALPACA_SECRET_KEY, ALPACA_PAPER="true"/"false")
```

Environment variables are preferred; every script also accepts `--api-key` / `--finviz-api-key` as a fallback. Alpaca additionally needs the Alpaca MCP Server configured in Claude Code settings — see `skills/portfolio-manager/references/alpaca-mcp-setup.md`.

#### API Pricing and Access

| Provider | Cost | Sign-up |
|----------|------|---------|
| FMP | Free tier 250 calls/day; Starter $29.99/mo (750/day); Professional $79.99/mo (2,000/day) | https://site.financialmodelingprep.com/developer/docs |
| FINVIZ Elite | $39.50/mo or $299.50/yr | https://elite.finviz.com/ |
| Alpaca | Free (paper trading recommended for testing; live account commission-free) | https://alpaca.markets/ |

**Recommendations:** dividend screening works free via the TradingView data layer (S&P 500 universe); FINVIZ Elite only widens the universe and speeds up screening. Portfolio management: Alpaca paper account for practice. Options education: theoretical pricing works without any key.

### Running Helper Scripts

Script entry points live at `skills/<name>/scripts/`. Full CLI reference (flags, modes, examples) for each skill is in `docs/en/skills/<name>.md` and the skill's `SKILL.md` — read those before invoking. Typical invocation:

```bash
python3 skills/<skill-name>/scripts/<entry_script>.py --output-dir reports/
```

Conventions: API keys via env vars (fallback `--api-key`); always pass or default `--output-dir reports/`; FMP-heavy screeners support `--max-api-calls`; several skills offer offline `--fixture` / `--dry-run` modes for testing.

### Automation Pipelines (scheduled self-maintenance)

Two launchd-scheduled pipelines maintain skill quality. Full architecture, design decisions, CLI, and state-file reference: [`docs/dev/automation-pipelines.md`](docs/dev/automation-pipelines.md); operational triage: [`docs/dev/maintenance-runbook.md`](docs/dev/maintenance-runbook.md) §5.

- **Skill Self-Improvement Loop** (daily 05:00): `scripts/run_skill_improvement_loop.py` — round-robin skill selection, deterministic scoring via dual-axis-skill-reviewer, Claude CLI improvement, quality gate with rollback, PR creation. Dry-run: `python3 scripts/run_skill_improvement_loop.py --dry-run [--all]`
- **Skill Auto-Generation Pipeline** (weekly mining Sat 06:00 + daily generation 07:00): `scripts/run_skill_generation_pipeline.py` — mines session logs into a scored backlog, then designs/reviews/PRs new skills. Dry-run: `python3 scripts/run_skill_generation_pipeline.py --mode weekly|daily --dry-run`

State and logs live under `logs/` (PID lock files, state JSON/YAML, rotated logs) and `reports/skill-{improvement,generation}-log/`.

### Running the Web UI (State Dashboard)

`ui/` is a self-contained **React + TypeScript (Vite) + Express (TypeScript)** app — an npm-workspaces monorepo (`ui/client`, `ui/server`, type-only contract in `ui/shared/types.d.ts`) — that visualizes the state the scheduler produces under `trading-data/` (exposure gate, watchlist, open positions/heat, screeners, market regime, theses, signals feed, autopilot status) and exposes buttons to run scheduler slots and sync TradingView alerts. Full reference: [`ui/README.md`](ui/README.md).

```bash
cd ui && npm install
npm run dev            # server :4000 + client :5173 (proxies /api); open http://localhost:5173
npm test               # vitest (server file-resolution, route mapping, action guards)
npm run build && npm start   # single-port prod on http://127.0.0.1:4000
```

Key conventions: the server binds to `127.0.0.1` only and resolves the data dir like the scheduler (`TRADING_DATE_DIR`, else `trading-data/`); actions are a strict whitelist (`run-slot` shells out to `scripts/run_trading_schedule.sh`, defaulting to `--dry-run`, and respects the scheduler's single-run lock — exit 75 = busy; `analyze-ticker` runs the `ticker-analysis` skill via headless `claude -p` on Opus 4.8 with `--mcp-config --strict-mcp-config` loading the vendored TradingView MCP server (needs TradingView Desktop CDP on :9222), `--output-format stream-json` for live progress, optional per-run toggles to also create TradingView alerts (`signals-alerts`) and save the report to Notes (`save-note`), and the Watchlist showing a per-ticker "analysis exists" flag) streamed to the UI over SSE. `ui/**/dist` and `ui/.env` are gitignored; `ui/` source is committed.

## Skill Interaction Patterns

- **Chart analysis skills** (sector-analyst, breadth-chart-analyst, technical-analyst): user provides chart screenshots → skill applies a framework from `references/` → structured markdown report with scenario probabilities, saved to `reports/`.
- **News analysis** (market-news-analyst): collects news via WebSearch/WebFetch (past 10 days), scores impact as (Price Impact × Breadth) × Forward Significance, ranks events. Key references: `trusted_news_sources.md`, `market_event_patterns.md`, `geopolitical_commodity_correlations.md`.
- **Calendar skills** (economic-calendar-fetcher, earnings-calendar): ⚠️ require FMP key. Scripts call FMP endpoints → chronological markdown reports with High/Medium/Low impact assessment. Free tier suffices.

## Multi-Skill Workflows

> **Canonical source:** `workflows/*.yaml` is the authoritative definition of multi-skill workflows for the Core + Satellite primary user. If anything below disagrees with a manifest in `workflows/`, the YAML is correct. See [`workflows/README.md`](workflows/README.md) for the manifest schema and `docs/dev/metadata-and-workflow-schema.md` for the full validator rules.

### Canonical workflows (PR2)

| Workflow | Cadence | Required skills |
|---|---|---|
| [`market-regime-daily`](workflows/market-regime-daily.yaml) | daily | market-breadth-analyzer, uptrend-analyzer, exposure-coach |
| [`core-portfolio-weekly`](workflows/core-portfolio-weekly.yaml) | weekly | portfolio-manager, trader-memory-core |
| [`swing-opportunity-daily`](workflows/swing-opportunity-daily.yaml) | daily | vcp-screener, technical-analyst, position-sizer, trader-memory-core |
| [`trade-memory-loop`](workflows/trade-memory-loop.yaml) | per closed trade | trader-memory-core, signal-postmortem |
| [`monthly-performance-review`](workflows/monthly-performance-review.yaml) | monthly | trader-memory-core, signal-postmortem |

### Quickstart sketches (NOT canonical)

Informal chains for skills not yet covered by a YAML manifest. Details (modes, flags, intermediate files) are in each skill's SKILL.md and doc page. Keep the `**Name:**` + numbered `Skill → action` format — `skill-integration-tester` parses these blocks.

**Daily Market Monitoring:**
1. Economic Calendar Fetcher → Today's events
2. Earnings Calendar → Reporting companies
3. Market News Analyst → Overnight developments
4. Breadth Chart Analyst → Market health

**Weekly Strategy Review:**
1. Sector Analyst → Rotation patterns
2. Technical Analyst → Trend confirmation
3. Market Environment Analysis → Macro briefing
4. US Market Bubble Detector → Risk assessment

**Individual Stock Research:**
1. US Stock Analysis → Fundamental/technical review
2. Earnings Calendar → Earnings dates
3. Market News Analyst → Recent news
4. Backtest Expert → Validate entry/exit strategy

**Options Strategy Development:**
1. Options Strategy Advisor → Simulate and compare strategies
2. Technical Analyst → Entry timing
3. Earnings Calendar → Earnings-based strategies
4. US Stock Analysis → Validate fundamental thesis

**Earnings Momentum Trading:**
1. Earnings Trade Analyzer → Score recent earnings reactions (5-factor)
2. PEAD Screener (Mode B) → Red candle pullback → breakout patterns
3. Technical Analyst → Confirm weekly setups on SIGNAL_READY/BREAKOUT candidates
4. Monitor BREAKOUT entries with stop-loss (red candle low) and 2R profit targets

**Statistical Arbitrage:**
1. Pair Trade Screener → Cointegrated pairs
2. Technical Analyst → Confirm both legs
3. Monitor z-score signals and spread convergence

**Income Portfolio Construction:**
1. Value Dividend Screener → High-yield opportunities
2. Dividend Growth Pullback Screener → Growth stocks at pullbacks
3. US Stock Analysis → Deep-dive analysis
4. Portfolio Manager → Monitor and rebalance

**Trade Execution Planning:**
1. Screener skills (VCP, CANSLIM, Dividend, Earnings) → Identify candidates
2. Position Sizer → Risk-based share count with portfolio constraints
3. Data Quality Checker → Validate analysis document
4. Portfolio Manager → Execute and monitor positions

**Kanchi Dividend Workflow (US stocks):**
1. kanchi-dividend-sop → Kanchi 5-step screening and pullback entry planning
2. kanchi-dividend-review-monitor → T1-T5 anomaly detection and review queueing
3. kanchi-dividend-us-tax-accounting → Validate qualified/ordinary assumptions and account location
4. Feed REVIEW findings back to kanchi-dividend-sop before any additional buys

**Edge Research Pipeline (end-to-end):**
1. edge-candidate-agent (--ohlcv) → market_summary.json + anomalies.json + tickets/
2. edge-hint-extractor (--market-summary, --anomalies) → hints.yaml
3. edge-concept-synthesizer (--tickets-dir, --hints) → edge_concepts.yaml
4. edge-strategy-designer (--concepts) → strategy_drafts/*.yaml
5. edge-strategy-reviewer (--drafts-dir) → review.yaml (PASS/REVISE/REJECT, max 2 revision cycles)
6. edge-candidate-agent (export, PASS + eligible only) → strategy.yaml + metadata.json
- **Orchestrated mode:** edge-pipeline-orchestrator runs all stages automatically with feedback loop

**Thesis-Driven Trading Pipeline:**
1. Screener skills (kanchi, earnings-trade-analyzer, vcp, pead, canslim) → Generate candidates
2. Trader Memory Core (ingest) → IDEA thesis from screener report
3. US Stock Analysis → Deep-dive validation (or Technical Analyst), link report
4. Trader Memory Core (transition) → IDEA → ENTRY_READY → ACTIVE
5. Position Sizer → Risk-based sizing, attach position
6. Portfolio Manager → Execute entry, record actual price/date
7. Trader Memory Core (review) → Periodic review-due checks
8. Trader Memory Core (close + postmortem) → Exit record + journal entry with MAE/MFE

**Parabolic Short Pipeline (Phase 1 + Phase 2 + Phase 3):**
1. parabolic-short-trade-planner (`screen_parabolic.py`, Phase 1) → daily watchlist JSON, 5-factor weighted score, A/B/C/D grades
2. Review the watchlist and promote A/B candidates
3. parabolic-short-trade-planner (`generate_pre_market_plan.py`, Phase 2) → three trigger plans per candidate (ORL break / first red 5-min / VWAP fail) with borrow/SSR/manual-confirmation gating
4. Trader clears `blocking_manual_reasons` at the broker (HTB locate, premarket levels)
5. parabolic-short-trade-planner (`monitor_intraday_trigger.py`, Phase 3) → one-shot intraday FSM over 5-min bars, replay-deterministic; wrap in `watch`/cron during market hours
6. trader-memory-core (optional ingest) → register theses for postmortem tracking

See `skills/parabolic-short-trade-planner/SKILL.md` for full phase semantics.

## Important Conventions

### SKILL.md Writing Style

- Use imperative/infinitive verb forms (e.g., "Analyze the chart", "Generate report")
- Write instructions for Claude to execute, not user instructions
- Avoid phrases like "You should..." or "Claude will..." - just state actions directly
- Structure: Overview → When to Use → Workflow → Output Format → Resources

### Reference Document Patterns

- Declarative statements of fact; historical examples and case studies
- Decision frameworks and checklists
- Hierarchical organization (H2 major sections, H3 subsections)

### Analysis Output Requirements

All analysis outputs must:
- Be saved to the `reports/` directory (create if it does not exist)
- Include date/time stamps
- Use English language
- Provide probability assessments where applicable
- Include specific trigger levels for actionable scenarios
- Cite references to knowledge base sources

### Script Requirements

- Check the API-key environment variable first, fall back to a command-line argument, and fail with a clear stderr message if missing (works across CLI / Desktop / Web)
- Validate date ranges and input parameters
- Return proper exit codes (0 success, 1 error)
- Handle rate limits gracefully with retry logic and exponential backoff

### No Personal Information in Committed Files

This is a **public repository**. Never hardcode personal information:
- **Absolute paths** containing usernames (e.g., `/Users/username/...`) — use relative paths or dynamic resolution like `Path(__file__).resolve().parents[N]`
- **API keys / secrets** — use environment variables (`$FMP_API_KEY`, `$FINVIZ_API_KEY`) or `.gitignore`-listed config files (`.mcp.json`, `.envrc`)
- **Usernames, email addresses, or other PII**

Files that contain secrets (`.mcp.json`, `.envrc`) must be listed in `.gitignore` and never committed.

## Language Considerations

- All SKILL.md files, reference docs, and analysis outputs are in English (some references include Japanese content; user interactions may be in Japanese)
- READMEs are bilingual: `README.md` (EN) and `README.ja.md` (JA)

## Distribution Workflow

Skills are packaged as ZIP (`.skill`) files so Claude web app users can use them without cloning the repository.

1. Test the skill thoroughly in Claude Code
2. Package with the repo packager (excludes tests and local build artifacts):
   ```bash
   python3 scripts/package_skills.py --skill <skill-name>
   ```
3. Confirm the generated `.skill` file is in `skill-packages/`; regenerate after any skill modification
4. Update `README.md` / `README.ja.md` — clearly mark required vs optional API subscriptions, with pricing and sign-up links
5. Document API requirements in the skill's SKILL.md itself (env var + CLI argument setup, registration links)
6. Commit with a descriptive message
