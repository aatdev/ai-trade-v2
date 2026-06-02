#!/usr/bin/env node
/**
 * delete_alerts.mjs — точечное удаление «наших» алертов через UI.
 *
 * Один Node-процесс, один CDP-сеанс. На вход — список тикеров. Из всех
 * алертов отбирает те, чей `message` начинается с `TICKER:`, и удаляет
 * их через DOM (alert-delete-button + confirm dialog).
 *
 * Жёсткое правило: `alerts.deleteAlerts({ delete_all: true })` НЕ
 * используется — он снесёт ВСЕ алерты пользователя, включая чужие.
 *
 * CLI:
 *   node delete_alerts.mjs --tickers BSX,LULU,ABT
 *   node delete_alerts.mjs --file plan.json    # читает signals[*].ticker
 *   echo '{"signals":[...]}' | node delete_alerts.mjs
 *
 *   # diff-режим для sync: удалить только устаревшие (которых нет в плане)
 *   node delete_alerts.mjs --keep-from-plan --file plan.json
 *
 * stdout: JSON { results: [{ ticker, deleted: [...], kept: [...], not_found, errors }], summary }
 */
import fs from 'node:fs';
import * as alerts from '../../../vendor/tradingview-mcp/src/core/alerts.js';
import * as ui from '../../../vendor/tradingview-mcp/src/core/ui.js';
import * as chart from '../../../vendor/tradingview-mcp/src/core/chart.js';
import * as drawing from '../../../vendor/tradingview-mcp/src/core/drawing.js';
import * as health from '../../../vendor/tradingview-mcp/src/core/health.js';
import { evaluate } from '../../../vendor/tradingview-mcp/src/connection.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const out = { tickers: null, file: null, keepFromPlan: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--tickers' || a === '-t') out.tickers = argv[++i].split(',').map((s) => s.trim().toUpperCase()).filter(Boolean);
    else if (a === '--file' || a === '-f') out.file = argv[++i];
    else if (a === '--keep-from-plan') out.keepFromPlan = true;
  }
  return out;
}

function readPlanRaw(args) {
  if (args.tickers && !args.file) return null;
  let raw;
  try {
    raw = args.file ? fs.readFileSync(args.file, 'utf8') : fs.readFileSync(0, 'utf8');
  } catch {
    return null;
  }
  if (!raw.trim()) return null;
  return JSON.parse(raw);
}

function tickersFromPlan(plan) {
  if (!plan) return [];
  const fromSignals = (plan.signals || []).map((s) => (s.ticker || '').toUpperCase());
  const fromSkipped = (plan.skipped || []).map((s) => (s.ticker || '').toUpperCase());
  return [...new Set([...fromSignals, ...fromSkipped])].filter(Boolean);
}

function plannedMessagesByTicker(plan) {
  const out = new Map();
  for (const s of plan?.signals || []) {
    const t = (s.ticker || '').toUpperCase();
    if (!t) continue;
    const set = out.get(t) || new Set();
    for (const a of s.alerts || []) {
      if (a?.message) set.add(a.message);
    }
    out.set(t, set);
  }
  return out;
}

const VISIBLE_FN = `
  function visible(el){
    if (!el) return false;
    if (el.offsetWidth === 0 && el.offsetHeight === 0) return false;
    var cs = window.getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden';
  }
`;

// Ensure the right-hand widget bar is showing the **Alerts** tab with its list
// rendered. The toolbar toggle is `[data-name="alerts"]` (aria-label "Alerts").
// If the panel is closed or on another tab (Watchlist/Details/etc.), no
// `[data-name="alert-item-description"]` rows exist and every delete falls
// through to `description_not_found`. Idempotent: returns immediately if rows
// are already present, otherwise clicks the toggle and re-checks.
async function rowCount() {
  const n = await evaluate(
    `document.querySelectorAll('[data-name="alert-item-description"]').length`
  );
  return Number(n) || 0;
}

async function ensureAlertsPanel() {
  if ((await rowCount()) > 0) return { ok: true, rows: await rowCount() };
  for (let attempt = 0; attempt < 3; attempt++) {
    await evaluate(`
      (function(){
        var b = document.querySelector('[data-name="alerts"]')
             || document.querySelector('[aria-label="Alerts"]');
        if (b) { b.click(); return true; }
        return false;
      })()
    `);
    await sleep(1200);
    if ((await rowCount()) > 0) return { ok: true, rows: await rowCount() };
  }
  return { ok: false, rows: 0 };
}

// The alerts list is **virtualized** — only ~30 of N rows are in the DOM at a
// time, so a target alert scrolled out of view is absent until we scroll its
// row into the viewport. Locate the scroll container (overflow-y auto/scroll
// ancestor of the rows) so we can walk it.
const SCROLLER_FN = `
  function alertsScroller(){
    var rows = document.querySelectorAll('[data-name="alert-item-description"]');
    if (!rows.length) return null;
    var p = rows[0];
    while (p && p !== document.body){
      var cs = window.getComputedStyle(p);
      if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') && p.scrollHeight > p.clientHeight + 5) return p;
      p = p.parentElement;
    }
    return null;
  }
`;

async function findAndClickDelete(target) {
  return await evaluate(`
    (function() {
      ${VISIBLE_FN}
      var target = ${JSON.stringify(target)};
      var descs = Array.from(document.querySelectorAll('[data-name="alert-item-description"]'))
        .filter(visible)
        .filter(function(el){ return (el.textContent || '').trim() === target; });
      if (!descs.length) return { ok: false, reason: 'description_not_found' };
      var p = descs[0];
      while (p && p !== document.body) {
        var btn = p.querySelector ? p.querySelector('[data-name="alert-delete-button"]') : null;
        if (btn) { btn.click(); return { ok: true }; }
        p = p.parentElement;
      }
      return { ok: false, reason: 'delete_button_not_found' };
    })()
  `);
}

async function clickDeleteForMessage(messageText) {
  // Reset the virtualized list to the top, then scroll-search a page at a time.
  await evaluate(`(function(){ ${SCROLLER_FN} var s = alertsScroller(); if (s) s.scrollTop = 0; return !!s; })()`);
  await sleep(300);

  let clicked = { ok: false, reason: 'description_not_found' };
  for (let step = 0; step < 50; step++) {
    clicked = await findAndClickDelete(messageText);
    if (clicked?.ok) break;
    if (clicked?.reason && clicked.reason !== 'description_not_found') return clicked;
    const scrolled = await evaluate(`
      (function(){
        ${SCROLLER_FN}
        var s = alertsScroller();
        if (!s) return { atBottom: true, noScroller: true };
        var before = s.scrollTop;
        s.scrollTop = Math.min(s.scrollTop + Math.floor(s.clientHeight * 0.85), s.scrollHeight);
        return { atBottom: (s.scrollTop <= before + 1) || (s.scrollTop + s.clientHeight >= s.scrollHeight - 1) };
      })()
    `);
    await sleep(350); // let the virtual list render the newly visible rows
    if (scrolled?.atBottom) {
      clicked = await findAndClickDelete(messageText);
      break;
    }
  }
  if (!clicked?.ok) return clicked;

  await sleep(500);

  const confirmed = await evaluate(`
    (function() {
      ${VISIBLE_FN}
      var dlgs = Array.from(document.querySelectorAll('[data-name="confirmation-dialog"], [class*="dialog"]')).filter(visible);
      for (var i = 0; i < dlgs.length; i++) {
        var btns = Array.from(dlgs[i].querySelectorAll('button')).filter(visible);
        for (var j = 0; j < btns.length; j++) {
          var t = (btns[j].textContent || '').trim();
          if (/^(delete|удалить)$/i.test(t)) { btns[j].click(); return { ok: true, dialog: i, label: t }; }
        }
      }
      return { ok: false, reason: 'no_confirm_dialog' };
    })()
  `);

  await sleep(700);
  return confirmed;
}

function isMultiCondAlert(msg) {
  return /\+\s*vol\s*[<>≤≥]/i.test(String(msg || ''));
}

function markerNeedle(ticker, msg) {
  const m = String(msg || '').match(/Trigger\s+\$([\d.]+)/);
  if (!m) return null;
  return `${ticker} Trigger $${m[1]}`;
}

async function removeChartMarkers(ticker, alertsToDelete, result) {
  const targets = alertsToDelete.filter((a) => isMultiCondAlert(a?.message));
  if (!targets.length) return;
  try {
    await chart.setSymbol({ symbol: ticker });
  } catch (e) {
    result.errors.push({ step: 'marker_setSymbol', error: String(e?.message || e) });
    return;
  }
  await sleep(600);
  for (const a of targets) {
    const needle = markerNeedle(ticker, a.message);
    if (!needle) continue;
    try {
      const found = await drawing.findShapesByText({ substring: needle });
      const shapes = found?.shapes || [];
      if (!shapes.length) {
        result.markers_not_found = result.markers_not_found || [];
        result.markers_not_found.push({ message: a.message, needle });
        continue;
      }
      for (const sh of shapes) {
        try {
          await drawing.removeOne({ entity_id: sh.id });
          result.markers_removed = result.markers_removed || [];
          result.markers_removed.push({ message: a.message, entity_id: sh.id });
        } catch (e) {
          result.errors.push({ step: 'marker_remove', entity_id: sh.id, error: String(e?.message || e) });
        }
      }
    } catch (e) {
      result.errors.push({ step: 'marker_find', message: a.message, error: String(e?.message || e) });
    }
    await sleep(150);
  }
}

async function deleteForTicker(ticker, allAlerts, keepMessages) {
  const ours = allAlerts.filter((a) => (a?.message || '').startsWith(`${ticker}:`));
  const result = { ticker, deleted: [], kept: [], errors: [], not_found_in_ui: [] };

  const toDelete = keepMessages
    ? ours.filter((a) => !keepMessages.has(a.message))
    : ours;

  if (keepMessages) {
    for (const a of ours) {
      if (keepMessages.has(a.message)) result.kept.push({ message: a.message });
    }
  }

  if (!toDelete.length) {
    result.note = keepMessages ? 'устаревших алертов нет' : 'нечего удалять';
    return result;
  }

  // Companion chart markers for multi-condition alerts are drawn as horizontal
  // lines tagged with `TICKER Trigger $X.XX` — remove them before deleting the
  // alerts themselves so cleanup is symmetric.
  await removeChartMarkers(ticker, toDelete, result);

  for (const a of toDelete) {
    const r = await clickDeleteForMessage(a.message);
    if (r?.ok) {
      result.deleted.push({ message: a.message });
    } else if (r?.reason === 'description_not_found') {
      result.not_found_in_ui.push({ message: a.message });
    } else {
      result.errors.push({ message: a.message, reason: r?.reason || 'unknown', detail: r });
    }
    await sleep(900);
  }

  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const plan = readPlanRaw(args);
  const tickers = args.tickers || tickersFromPlan(plan);

  if (args.keepFromPlan && !plan) {
    process.stdout.write(JSON.stringify({ error: '--keep-from-plan требует --file plan.json или план в stdin.' }, null, 2) + '\n');
    process.exit(2);
  }

  if (!tickers.length) {
    process.stdout.write(JSON.stringify({ error: 'Не указаны тикеры (--tickers или --file/stdin с signals[].ticker).' }, null, 2) + '\n');
    process.exit(2);
  }

  const keepByTicker = args.keepFromPlan ? plannedMessagesByTicker(plan) : null;

  const hc = await health.healthCheck().catch((e) => ({ ok: false, error: String(e?.message || e) }));
  if (!hc?.success && !hc?.ok) {
    process.stdout.write(JSON.stringify({ error: 'TradingView Desktop недоступен. Запусти `tv launch`.', health: hc }, null, 2) + '\n');
    process.exit(2);
  }

  const listed = await alerts.list();
  const allAlerts = listed?.alerts || [];

  const hasAnythingToDelete = tickers.some((t) => {
    const ours = allAlerts.filter((a) => (a?.message || '').startsWith(`${t}:`));
    const keep = keepByTicker?.get(t.toUpperCase());
    return keep ? ours.some((a) => !keep.has(a.message)) : ours.length > 0;
  });

  if (hasAnythingToDelete) {
    const panel = await ensureAlertsPanel();
    if (!panel.ok) {
      process.stdout.write(
        JSON.stringify(
          {
            error:
              'Не удалось активировать вкладку Alerts в TradingView (нет строк alert-item-description). Открой панель Alerts вручную и повтори.',
          },
          null,
          2
        ) + '\n'
      );
      process.exit(2);
    }
  }

  const results = [];
  for (const t of tickers) {
    const keep = keepByTicker ? (keepByTicker.get(t.toUpperCase()) || new Set()) : null;
    const r = await deleteForTicker(t, allAlerts, keep);
    results.push(r);
  }

  const summary = {
    tickers: results.length,
    deleted: results.reduce((a, r) => a + r.deleted.length, 0),
    kept: results.reduce((a, r) => a + r.kept.length, 0),
    not_found_in_ui: results.reduce((a, r) => a + r.not_found_in_ui.length, 0),
    markers_removed: results.reduce((a, r) => a + ((r.markers_removed || []).length), 0),
    markers_not_found: results.reduce((a, r) => a + ((r.markers_not_found || []).length), 0),
    errors: results.reduce((a, r) => a + r.errors.length, 0),
  };

  process.stdout.write(JSON.stringify({ results, summary, mode: args.keepFromPlan ? 'diff' : 'purge' }, null, 2) + '\n');
  process.exit(0);
}

main().catch((e) => {
  process.stderr.write(`Fatal: ${e?.stack || e}\n`);
  process.exit(1);
});
