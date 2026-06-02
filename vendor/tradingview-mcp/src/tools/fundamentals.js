import { z } from 'zod';
import { jsonResult } from './_format.js';
import * as core from '../core/fundamentals.js';

export function registerFundamentalsTools(server) {
  server.tool(
    'fundamentals_get',
    'Get fundamental data for a stock from TradingView (valuation multiples, TTM income statement, margins, returns, balance sheet, cash flow, dividends). Set history=true to also get annual/quarterly time series (revenue, net income, EPS). Defaults to the active chart symbol if none given.',
    {
      symbol: z
        .string()
        .optional()
        .describe(
          'Symbol as EXCHANGE:TICKER (e.g. "NYSE:VSCO", "NASDAQ:AAPL"). Defaults to the active chart symbol.'
        ),
      history: z
        .boolean()
        .optional()
        .describe(
          'Include annual (FY) and quarterly (FQ) historical series, most recent period first. Default false.'
        ),
    },
    async ({ symbol, history }) => {
      try {
        return jsonResult(await core.get({ symbol, history }));
      } catch (err) {
        return jsonResult({ success: false, error: err.message }, true);
      }
    }
  );
}
