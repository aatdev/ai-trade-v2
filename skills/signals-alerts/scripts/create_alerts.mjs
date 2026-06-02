#!/usr/bin/env node
/**
 * create_alerts.mjs — массовое создание алертов по плану из parse_signals.mjs.
 *
 * Один Node-процесс держит CDP-соединение и проходит план последовательно,
 * без оверхеда tool-call'ов. На каждый сигнал: setSymbol → setTimeframe(D)
 * → дедупликация (alert.list) → 5 раз alert.create.
 *
 * CLI:
 *   node parse_signals.mjs [--tickers ...] | node create_alerts.mjs
 *   node create_alerts.mjs --file plan.json
 *   node create_alerts.mjs --no-dedupe          # для sync — после delete
 *
 * stdout: JSON-отчёт { results: [{ ticker, created: [...], skipped: [...], errors: [...] }], summary }
 */
import fs from 'node:fs';
import * as alerts from '../../../vendor/tradingview-mcp/src/core/alerts.js';
import * as chart from '../../../vendor/tradingview-mcp/src/core/chart.js';
import * as health from '../../../vendor/tradingview-mcp/src/core/health.js';
import { reconcileMarker } from '../../../vendor/tradingview-mcp/src/core/alert_markers.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const out = { file: null, dedupe: true };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--file' || a === '-f') out.file = argv[++i];
    else if (a === '--no-dedupe') out.dedupe = false;
  }
  return out;
}

function readPlan(filePath) {
  const raw = filePath ? fs.readFileSync(filePath, 'utf8') : fs.readFileSync(0, 'utf8');
  return JSON.parse(raw);
}

async function listExistingForTicker(ticker) {
  const r = await alerts.list();
  return (r?.alerts || []).filter((a) => (a?.message || '').startsWith(`${ticker}:`));
}

async function createOneAlert(spec, attempt = 1) {
  try {
    const params = {
      price: spec.price,
      price_condition: spec.price_condition,
      message: spec.message,
      direction: spec.direction,
    };
    if (spec.volume != null) {
      params.volume = spec.volume;
      params.volume_condition = spec.volume_condition || 'Greater Than';
    }
    const res = await alerts.create(params);
    return { ok: !!res?.success, dialog_summary: res?.dialog_summary, created_alert: res?.created_alert };
  } catch (e) {
    if (attempt < 2) {
      await sleep(2000);
      return createOneAlert(spec, attempt + 1);
    }
    return { ok: false, error: String(e?.message || e) };
  }
}

async function processTicker(sig, { dedupe }) {
  const result = {
    ticker: sig.ticker,
    direction: sig.direction,
    created: [],
    skipped: [],
    errors: [],
    markers: { created: [], skipped: [], errors: [] },
  };

  try {
    await chart.setSymbol({ symbol: sig.ticker });
  } catch (e) {
    result.errors.push({ step: 'setSymbol', error: String(e?.message || e) });
    return result;
  }
  await sleep(500);

  try {
    await chart.setTimeframe({ timeframe: 'D' });
  } catch (e) {
    result.errors.push({ step: 'setTimeframe', error: String(e?.message || e) });
  }
  await sleep(400);

  let existingMessages = new Set();
  if (dedupe) {
    try {
      const existing = await listExistingForTicker(sig.ticker);
      existingMessages = new Set(existing.map((a) => a.message));
    } catch (e) {
      result.errors.push({ step: 'dedupe_list', error: String(e?.message || e) });
    }
  }

  for (const spec of sig.alerts) {
    if (dedupe && existingMessages.has(spec.message)) {
      result.skipped.push({ level: spec.level, reason: 'already exists' });
      continue;
    }
    const r = await createOneAlert({ ...spec, direction: sig.direction });
    if (r.ok) {
      const created = { level: spec.level, price: spec.price, message: spec.message };
      if (spec.volume != null) {
        created.volume = spec.volume;
        created.volume_condition = spec.volume_condition || 'Greater Than';
      }
      result.created.push(created);
    } else {
      result.errors.push({ level: spec.level, price: spec.price, error: r.error || 'create failed' });
    }
    await sleep(900);
  }

  // Reconcile chart markers for multi-condition Trigger alerts: dedupe by
  // message skips alerts that already exist in TradingView, so the marker
  // mechanism in alerts.create never fires for them. Re-check the chart and
  // draw any missing companion lines so the trigger price stays visible.
  const triggerSpecs = (sig.alerts || []).filter((a) => a.level === 'Trigger' && a.volume != null);
  for (const t of triggerSpecs) {
    const r = await reconcileMarker({
      ticker: sig.ticker,
      price: t.price,
      message: t.message,
      direction: sig.direction,
    });
    if (r.action === 'created') {
      result.markers.created.push({ price: t.price, text: r.text });
    } else if (r.action === 'skipped') {
      result.markers.skipped.push({ price: t.price, reason: r.reason });
    } else {
      result.markers.errors.push({ price: t.price, error: r.error });
    }
    await sleep(300);
  }

  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const plan = readPlan(args.file);

  const hc = await health.healthCheck().catch((e) => ({ ok: false, error: String(e?.message || e) }));
  if (!hc?.success && !hc?.ok) {
    process.stdout.write(JSON.stringify({ error: 'TradingView Desktop недоступен. Запусти `tv launch` или `./scripts/launch_tv_debug_mac.sh`.', health: hc }, null, 2) + '\n');
    process.exit(2);
  }

  const results = [];
  for (const sig of plan.signals || []) {
    const r = await processTicker(sig, { dedupe: args.dedupe });
    results.push(r);
  }

  const summary = {
    tickers: results.length,
    created: results.reduce((a, r) => a + r.created.length, 0),
    skipped: results.reduce((a, r) => a + r.skipped.length, 0),
    errors: results.reduce((a, r) => a + r.errors.length, 0),
    markers_created: results.reduce((a, r) => a + (r.markers?.created.length || 0), 0),
    markers_skipped: results.reduce((a, r) => a + (r.markers?.skipped.length || 0), 0),
    markers_errors: results.reduce((a, r) => a + (r.markers?.errors.length || 0), 0),
  };

  process.stdout.write(JSON.stringify({ results, summary }, null, 2) + '\n');
  process.exit(0);
}

main().catch((e) => {
  process.stderr.write(`Fatal: ${e?.stack || e}\n`);
  process.exit(1);
});
