/**
 * Core fundamentals access logic.
 *
 * TradingView отдаёт фундаментальные данные через scanner-эндпоинт:
 *   https://scanner.tradingview.com/symbol?symbol=<EXCHANGE>:<TICKER>&fields=<csv>&no_404=true
 * Запрос выполняется ИЗНУТРИ страницы tradingview.com (через CDP), чтобы
 * унаследовать авторизационные cookie — иначе доступен лишь урезанный набор полей.
 */
import { evaluate, evaluateAsync, KNOWN_PATHS } from '../connection.js';

const CHART_API = KNOWN_PATHS.chartApi;

// Поля snapshot, сгруппированные по смыслу.
export const FIELD_GROUPS = {
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
  per_share: [
    'earnings_per_share_diluted_ttm',
    'earnings_per_share_basic_ttm',
    'book_value_per_share_fq',
    'earnings_per_share_diluted_yoy_growth_ttm',
  ],
  income_ttm: [
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

// Исторические ряды — массивы, самый свежий период первым.
const HISTORY_FIELDS = [
  'total_revenue_fy_h',
  'net_income_fy_h',
  'earnings_per_share_diluted_fy_h',
  'gross_margin_fy_h',
  'net_margin_fy_h',
  'total_revenue_fq_h',
  'net_income_fq_h',
  'earnings_per_share_diluted_fq_h',
];

const SNAPSHOT_FIELDS = Object.values(FIELD_GROUPS).flat();

/**
 * Получить фундаментальные данные по символу.
 * @param {object} opts
 * @param {string} [opts.symbol] — "EXCHANGE:TICKER"; по умолчанию символ активного графика.
 * @param {boolean} [opts.history] — добавить годовые/квартальные ряды.
 */
export async function get({ symbol, history = false } = {}) {
  let resolvedSymbol = symbol;
  if (!resolvedSymbol) {
    resolvedSymbol = await evaluate(`
      (function() {
        try { return ${CHART_API}.symbol(); }
        catch (e) {
          var m = location.pathname.match(/\\/symbols\\/([A-Z0-9._]+)-([A-Z0-9._]+)/i);
          return m ? (m[1] + ':' + m[2]) : null;
        }
      })()
    `);
    if (!resolvedSymbol) {
      throw new Error(
        'Could not determine symbol from active chart. Pass symbol explicitly (e.g. "NYSE:VSCO").'
      );
    }
  }

  const fields = history ? [...SNAPSHOT_FIELDS, ...HISTORY_FIELDS] : SNAPSHOT_FIELDS;
  const url =
    `https://scanner.tradingview.com/symbol?symbol=${encodeURIComponent(resolvedSymbol)}` +
    `&fields=${fields.join(',')}&no_404=true`;

  const raw = await evaluateAsync(
    `fetch(${JSON.stringify(url)}, { credentials: 'include' }).then(function(r) {
      return r.text().then(function(t) { return JSON.stringify({ status: r.status, body: t }); });
    })`
  );

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(`Scanner request failed (unparseable response): ${String(raw).slice(0, 200)}`);
  }
  if (parsed.status !== 200) {
    throw new Error(
      `Scanner returned HTTP ${parsed.status} for ${resolvedSymbol}: ${String(parsed.body).slice(0, 200)}`
    );
  }

  let data;
  try {
    data = JSON.parse(parsed.body);
  } catch {
    throw new Error(
      `Scanner returned non-JSON body for ${resolvedSymbol}: ${String(parsed.body).slice(0, 200)}`
    );
  }

  // Раскладываем по группам, пропуская отсутствующие поля.
  const grouped = {};
  for (const [group, keys] of Object.entries(FIELD_GROUPS)) {
    const obj = {};
    for (const k of keys) {
      if (k in data && data[k] !== null) obj[k] = data[k];
    }
    if (Object.keys(obj).length) grouped[group] = obj;
  }

  const result = {
    success: true,
    symbol: resolvedSymbol,
    name: data.description || data.name || resolvedSymbol,
    ...grouped,
  };

  if (history) {
    const hist = {};
    for (const k of HISTORY_FIELDS) {
      if (Array.isArray(data[k])) hist[k] = data[k];
    }
    if (Object.keys(hist).length) result.history = hist;
  }

  return result;
}
