#!/usr/bin/env node
// Получение фундаментальных данных из TradingView через scanner-эндпоинт.
//
// Идея: TradingView отдаёт fundamentals по адресу
//   https://scanner.tradingview.com/symbol?symbol=<EXCHANGE>:<TICKER>&fields=<csv>&no_404=true
// Запрос нужно выполнить ИЗНУТРИ страницы tradingview.com (через CDP), чтобы
// унаследовать авторизационные cookie — тогда доступны платные/расширенные поля.
//
// Использование:
//   node scripts/tv_fundamentals.mjs NYSE:VSCO            # снимок (snapshot)
//   node scripts/tv_fundamentals.mjs NYSE:VSCO --history  # + годовые/квартальные ряды
//   node scripts/tv_fundamentals.mjs NYSE:VSCO --json     # сырой JSON в stdout
//
// Если symbol не передан — берётся символ текущего активного графика.

import CDP from 'chrome-remote-interface';

const argv = process.argv.slice(2);
const flags = new Set(argv.filter((a) => a.startsWith('--')));
let symbol = argv.find((a) => !a.startsWith('--')) || null;

// --- Группы полей (snapshot) ---
const FIELDS = {
  profile: ['name', 'description', 'sector', 'industry', 'country', 'number_of_employees'],
  valuation: [
    'market_cap_basic',
    'enterprise_value_current',
    'price_earnings_ttm',
    'price_sales_current',
    'price_book_fq',
    'price_free_cash_flow_ttm',
    'enterprise_value_ebitda_ttm',
  ],
  perShare: [
    'earnings_per_share_diluted_ttm',
    'earnings_per_share_basic_ttm',
    'book_value_per_share_fq',
    'earnings_per_share_diluted_yoy_growth_ttm',
  ],
  incomeTTM: [
    'total_revenue_ttm',
    'gross_profit_ttm',
    'oper_income_ttm',
    'ebitda_ttm',
    'net_income_ttm',
    'total_revenue_yoy_growth_ttm',
  ],
  margins: ['gross_margin_ttm', 'operating_margin_ttm', 'net_margin_ttm', 'ebitda_margin_ttm'],
  returns: ['return_on_equity_fq', 'return_on_assets_fq', 'return_on_invested_capital_fq'],
  balance: [
    'total_assets_fq',
    'total_debt_fq',
    'total_equity_fq',
    'cash_n_short_term_invest_fq',
    'current_ratio_fq',
    'quick_ratio_fq',
    'debt_to_equity_fq',
  ],
  cashflow: ['free_cash_flow_ttm', 'cash_f_operating_activities_ttm', 'capital_expenditures_ttm'],
  dividends: [
    'dividends_yield_current',
    'dividend_payout_ratio_ttm',
    'dps_common_stock_primary_issue_ttm',
  ],
};

// Исторические ряды (отдаются массивами, самый свежий период — первый элемент)
const HISTORY_FIELDS = [
  'total_revenue_fy',
  'total_revenue_fy_h',
  'net_income_fy_h',
  'gross_margin_fy_h',
  'net_margin_fy_h',
  'total_revenue_fq_h',
  'net_income_fq_h',
  'earnings_per_share_diluted_fq_h',
];

const allSnapshotFields = Object.values(FIELDS).flat();
const fields = flags.has('--history')
  ? [...allSnapshotFields, ...HISTORY_FIELDS]
  : allSnapshotFields;

// --- Подключение к TradingView Desktop по CDP ---
// В десктопе может быть несколько таргетов tradingview.com: график (/chart/),
// страница символа (/symbols/.../) и т.д. Любой из них годится для fetch
// (общий origin + авторизация), но символ удобнее тянуть с графика.
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

// Если символ не задан — берём с активного графика, иначе парсим из URL страницы символа
if (!symbol) {
  symbol = await evalAsync(
    '(()=>{try{return window.TradingViewApi.activeChart().symbol()}catch(e){' +
      'var m=location.pathname.match(/\\/symbols\\/([A-Z0-9._]+)-([A-Z0-9._]+)/i);' +
      'return m?(m[1]+":"+m[2]):null}})()'
  );
  if (!symbol) {
    console.error(
      'Не удалось определить символ. Передайте его явно: node scripts/tv_fundamentals.mjs NYSE:VSCO'
    );
    process.exit(1);
  }
}

// --- Запрос fundamentals из контекста страницы (с авторизацией) ---
const url =
  `https://scanner.tradingview.com/symbol?symbol=${encodeURIComponent(symbol)}` +
  `&fields=${fields.join(',')}&no_404=true`;

const raw = await evalAsync(
  `fetch(${JSON.stringify(url)}, {credentials:'include'}).then(r=>r.text())`
);
await c.close();

let data;
try {
  data = JSON.parse(raw);
} catch {
  console.error('Не удалось распарсить ответ scanner:', String(raw).slice(0, 300));
  process.exit(1);
}

if (flags.has('--json')) {
  console.log(JSON.stringify({ symbol, data }, null, 2));
  process.exit(0);
}

// --- Форматирование ---
const fmtNum = (v) => {
  if (v == null) return '—';
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + ' B';
  if (a >= 1e6) return (v / 1e6).toFixed(2) + ' M';
  if (a >= 1e3) return (v / 1e3).toFixed(2) + ' K';
  return String(v);
};
const fmtPct = (v) => (v == null ? '—' : v.toFixed(2) + '%');
const fmtX = (v) => (v == null ? '—' : v.toFixed(2) + 'x');

const LABELS = {
  description: 'Название',
  sector: 'Сектор',
  industry: 'Индустрия',
  country: 'Страна',
  number_of_employees: 'Сотрудников',
  market_cap_basic: 'Рыночная капитализация',
  enterprise_value_current: 'Enterprise Value',
  price_earnings_ttm: 'P/E (TTM)',
  price_sales_current: 'P/S',
  price_book_fq: 'P/B',
  price_free_cash_flow_ttm: 'P/FCF',
  enterprise_value_ebitda_ttm: 'EV/EBITDA',
  earnings_per_share_diluted_ttm: 'EPS diluted (TTM)',
  earnings_per_share_basic_ttm: 'EPS basic (TTM)',
  book_value_per_share_fq: 'Book value/share',
  earnings_per_share_diluted_yoy_growth_ttm: 'Рост EPS YoY',
  total_revenue_ttm: 'Выручка (TTM)',
  gross_profit_ttm: 'Валовая прибыль (TTM)',
  oper_income_ttm: 'Операционная прибыль (TTM)',
  ebitda_ttm: 'EBITDA (TTM)',
  net_income_ttm: 'Чистая прибыль (TTM)',
  total_revenue_yoy_growth_ttm: 'Рост выручки YoY',
  gross_margin_ttm: 'Валовая маржа',
  operating_margin_ttm: 'Операционная маржа',
  net_margin_ttm: 'Чистая маржа',
  ebitda_margin_ttm: 'EBITDA маржа',
  return_on_equity_fq: 'ROE',
  return_on_assets_fq: 'ROA',
  return_on_invested_capital_fq: 'ROIC',
  total_assets_fq: 'Активы',
  total_debt_fq: 'Долг',
  total_equity_fq: 'Капитал',
  cash_n_short_term_invest_fq: 'Кэш и эквиваленты',
  current_ratio_fq: 'Current ratio',
  quick_ratio_fq: 'Quick ratio',
  debt_to_equity_fq: 'Debt/Equity',
  free_cash_flow_ttm: 'FCF (TTM)',
  cash_f_operating_activities_ttm: 'Операционный CF (TTM)',
  capital_expenditures_ttm: 'CapEx (TTM)',
  dividends_yield_current: 'Див. доходность',
  dividend_payout_ratio_ttm: 'Payout ratio',
  dps_common_stock_primary_issue_ttm: 'Дивиденд/акция',
};

const PCT = new Set([
  'gross_margin_ttm',
  'operating_margin_ttm',
  'net_margin_ttm',
  'ebitda_margin_ttm',
  'return_on_equity_fq',
  'return_on_assets_fq',
  'return_on_invested_capital_fq',
  'total_revenue_yoy_growth_ttm',
  'earnings_per_share_diluted_yoy_growth_ttm',
  'dividends_yield_current',
  'dividend_payout_ratio_ttm',
]);
const RATIOS = new Set([
  'price_earnings_ttm',
  'price_sales_current',
  'price_book_fq',
  'price_free_cash_flow_ttm',
  'enterprise_value_ebitda_ttm',
  'current_ratio_fq',
  'quick_ratio_fq',
  'debt_to_equity_fq',
]);
const RAW = new Set([
  'earnings_per_share_diluted_ttm',
  'earnings_per_share_basic_ttm',
  'book_value_per_share_fq',
  'dps_common_stock_primary_issue_ttm',
]);

const render = (k) => {
  const v = data[k];
  if (PCT.has(k)) return fmtPct(v);
  if (RATIOS.has(k)) return fmtX(v);
  if (RAW.has(k)) return v == null ? '—' : String(v);
  return fmtNum(v);
};

const GROUP_TITLES = {
  profile: 'Профиль',
  valuation: 'Оценка',
  perShare: 'На акцию',
  incomeTTM: 'Отчёт о прибылях (TTM)',
  margins: 'Маржинальность',
  returns: 'Рентабельность',
  balance: 'Баланс',
  cashflow: 'Денежный поток',
  dividends: 'Дивиденды',
};

console.log(`\n=== Fundamentals: ${symbol} (${data.description || data.name || ''}) ===\n`);
for (const [group, keys] of Object.entries(FIELDS)) {
  console.log(`— ${GROUP_TITLES[group]} —`);
  for (const k of keys) {
    if (k === 'name') continue;
    const label = LABELS[k] || k;
    console.log(`  ${label.padEnd(26)} ${render(k)}`);
  }
  console.log('');
}

if (flags.has('--history')) {
  console.log('— Годовая динамика (свежий → старый) —');
  const fyRev = data.total_revenue_fy_h || [];
  const fyNI = data.net_income_fy_h || [];
  for (let i = 0; i < fyRev.length; i++) {
    console.log(`  FY[-${i}]  Выручка ${fmtNum(fyRev[i]).padEnd(10)} ЧП ${fmtNum(fyNI[i])}`);
  }
  console.log('\n— Квартальная динамика (свежий → старый) —');
  const fqRev = data.total_revenue_fq_h || [];
  const fqNI = data.net_income_fq_h || [];
  const fqEPS = data.earnings_per_share_diluted_fq_h || [];
  for (let i = 0; i < Math.min(8, fqRev.length); i++) {
    console.log(
      `  Q[-${i}]  Выручка ${fmtNum(fqRev[i]).padEnd(10)} ЧП ${fmtNum(fqNI[i]).padEnd(10)} EPS ${fqEPS[i] ?? '—'}`
    );
  }
  console.log('');
}
