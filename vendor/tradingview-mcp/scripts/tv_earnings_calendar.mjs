#!/usr/bin/env node
// Рыночный календарь отчётностей из TradingView через scanner-эндпоинт —
// замена FMP earning_calendar для claude-trading-skills (tv_client_base.py).
//
// Идея (как в tv_fundamentals.mjs): POST на
//   https://scanner.tradingview.com/america/scan
// выполняется ИЗНУТРИ страницы tradingview.com (через CDP), чтобы унаследовать
// авторизационные cookie. Сканер хранит на тикер только ПОСЛЕДНЮЮ
// (earnings_release_date) и СЛЕДУЮЩУЮ (earnings_release_next_date) дату отчёта,
// поэтому для покрытия и прошлого, и будущего окна делаем два запроса и
// объединяем — недавнее прошлое (PEAD) и ближайшее будущее (upcoming) ловятся,
// но глубокую историю отчётностей так не получить.
//
// Использование:
//   node scripts/tv_earnings_calendar.mjs --from 2026-06-01 --to 2026-06-07
//
// Вывод (stdout, JSON): { "earnings": [ {date, symbol, exchange, eps,
//   epsEstimated, revenue, revenueEstimated, time}, ... ] } — форма FMP
// earning_calendar (eps/revenue для будущих отчётов = null).

import CDP from 'chrome-remote-interface';

const argv = process.argv.slice(2);
function arg(name) {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : null;
}
const fromDate = arg('--from');
const toDate = arg('--to');
if (!fromDate || !toDate) {
  console.error('Usage: node tv_earnings_calendar.mjs --from YYYY-MM-DD --to YYYY-MM-DD');
  process.exit(2);
}

// Полночь UTC соответствующих дат в Unix-секундах; верхняя граница — конец дня.
const fromTs = Math.floor(Date.parse(fromDate + 'T00:00:00Z') / 1000);
const toTs = Math.floor(Date.parse(toDate + 'T23:59:59Z') / 1000);
if (Number.isNaN(fromTs) || Number.isNaN(toTs)) {
  console.error('Bad date(s); use YYYY-MM-DD');
  process.exit(2);
}

const COLUMNS = [
  'name',
  'earnings_release_date',
  'earnings_release_next_date',
  'earnings_per_share_forecast_next_fq',
  'revenue_forecast_next_fq',
  'exchange',
];

// --- CDP к TradingView Desktop (любой таргет tradingview.com годится) ---
const targets = await (await fetch('http://localhost:9222/json/list')).json();
const tvTargets = targets.filter((x) => x.url?.includes('tradingview.com') && x.type === 'page');
const t = tvTargets.find((x) => x.url.includes('/chart/')) || tvTargets[0];
if (!t) {
  console.error('Нет открытой вкладки TradingView на localhost:9222.');
  process.exit(1);
}
const c = await CDP({ host: 'localhost', port: 9222, target: t.id });
await c.Runtime.enable();

async function evalAsync(expr) {
  const r = await c.Runtime.evaluate({ expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails)
    throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result?.value;
}

// Один scan-запрос с фильтром по диапазону на заданное date-поле.
async function scan(dateField) {
  const body = {
    filter: [{ left: dateField, operation: 'in_range', right: [fromTs, toTs] }],
    columns: COLUMNS,
    sort: { sortBy: dateField, sortOrder: 'asc' },
    range: [0, 3000],
  };
  const url = 'https://scanner.tradingview.com/america/scan';
  // POST с Content-Type application/json к другому origin триггерит CORS
  // preflight (OPTIONS), который scanner не обрабатывает → "Failed to fetch".
  // Без явного заголовка тело уходит как text/plain — это "simple request"
  // (без preflight), а scanner всё равно парсит JSON-тело.
  const raw = await evalAsync(
    `fetch(${JSON.stringify(url)}, {method:'POST',credentials:'include',` +
      `body:${JSON.stringify(JSON.stringify(body))}})` +
      `.then(r=>r.text())`
  );
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error('scanner parse error: ' + String(raw).slice(0, 200));
  }
  return parsed.data || [];
}

const isoDay = (ts) =>
  ts == null ? '' : new Date(ts * 1000).toISOString().slice(0, 10);

const byKey = new Map(); // symbol|date -> event (dedupe across both scans)

function ingest(rows, dateField) {
  for (const row of rows) {
    const d = row.d || [];
    const obj = {};
    COLUMNS.forEach((col, i) => (obj[col] = d[i]));
    const ts = obj[dateField];
    if (ts == null) continue;
    const date = isoDay(ts);
    if (date < fromDate || date > toDate) continue; // double-check the window
    const full = row.s || ''; // "NASDAQ:AAPL"
    const symbol = full.includes(':') ? full.split(':')[1] : full;
    const key = symbol + '|' + date;
    if (byKey.has(key)) continue;
    byKey.set(key, {
      date,
      symbol,
      exchange: obj.exchange || (full.includes(':') ? full.split(':')[0] : ''),
      eps: null, // фактический EPS сканер в этом наборе не отдаёт
      epsEstimated: obj.earnings_per_share_forecast_next_fq ?? null,
      revenue: null,
      revenueEstimated: obj.revenue_forecast_next_fq ?? null,
      time: '', // bmo/amc сканер надёжно не отдаёт
    });
  }
}

try {
  const [pastRows, nextRows] = await Promise.all([
    scan('earnings_release_date'),
    scan('earnings_release_next_date'),
  ]);
  ingest(pastRows, 'earnings_release_date');
  ingest(nextRows, 'earnings_release_next_date');
} catch (e) {
  await c.close();
  console.error(String(e.message || e));
  process.exit(1);
}
await c.close();

const earnings = [...byKey.values()].sort((a, b) =>
  a.date < b.date ? -1 : a.date > b.date ? 1 : a.symbol.localeCompare(b.symbol)
);
console.log(JSON.stringify({ earnings }));
