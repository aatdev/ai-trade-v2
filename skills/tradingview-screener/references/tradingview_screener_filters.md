# TradingView Screener Filter Reference

Complete catalog of the TradingView Stock Screener (All Stocks tab) filter
interface, mapped to `scanner.tradingview.com` API fields. Captured from the
live screener UI (filter catalog behind the **+** button: 8 categories,
238 filters) and verified against the `/america/metainfo` field registry.

## How the All Stocks Tab Queries Data

`POST https://scanner.tradingview.com/{market}/scan` — no API key, no auth.

```json
{
  "columns": ["name", "close", "market_cap_basic"],
  "filter": [
    {"left": "market_cap_basic", "operation": "egreater", "right": 100000000},
    {"left": "close", "operation": "egreater", "right": "EMA200"}
  ],
  "filter2": { "...All Stocks universe block (see below)..." },
  "symbols": {"symbolset": ["SYML:SP;SPX"]},
  "markets": ["america"],
  "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
  "range": [0, 100],
  "options": {"lang": "en"},
  "ignore_unknown_fields": false
}
```

- `filter` — flat AND list of `{left, operation, right}` expressions.
- `filter2` — boolean tree defining the symbol universe (the "All stocks"
  tab = common + preferred stocks, depositary receipts, non-ETF funds,
  excluding pre-IPO).
- `symbols.symbolset` — index membership (the "Index" pill).
- `right` may be **another field name** (string), e.g. price above EMA200:
  `{"left": "close", "operation": "greater", "right": "EMA200"}`.

### Operations

| Operation | Meaning | CLI token syntax |
|---|---|---|
| `greater` / `egreater` | > / ≥ | `field>v` / `field>=v` |
| `less` / `eless` | < / ≤ | `field<v` / `field<=v` |
| `equal` / `nequal` | = / ≠ | `field=v` / `field!=v` |
| `in_range` (numeric pair) | between | `field=lo..hi` |
| `in_range` (string array) | one of | `field=A\|B\|C` |
| `has` / `has_none_of` | array contains | (used internally for `typespecs`) |
| `crosses_above` / `crosses_below` | crossing | raw payload only |
| `match` | substring | raw payload only |

### Timeframe suffixes (technical fields)

Daily values have no suffix. Append `\|1W` (weekly), `\|1M` (monthly),
`\|240`/`\|60`/… (intraday minutes) to most technical fields: `RSI\|1W`,
`MACD.macd\|1M`. Suffix `[1]` = previous bar: `RSI[1]`, `Aroon.Up[1]`.

### Known limitations (unauthenticated)

- `analyst_rating` and `technical_rating` string fields return `null`
  without TradingView auth cookies — use the numeric equivalents
  `recommendation_mark` (1 = Strong buy … 5 = Strong sell) and
  `Recommend.All` (+1 = Strong buy … −1 = Strong sell).
- Unknown **filter** fields are silently ignored (0 matches); unknown
  **columns** raise an error. A 0-match result with a custom raw field
  usually means a typo.
- Date filters (`*_date` fields) take Unix timestamps in seconds.

---

## Filter Catalog (the "+" Button)

8 categories, 238 filters, as shown in the All Stocks tab UI. `UI ID` is
the internal identifier (`data-qa-id` suffix); fundamentals offer period
variants: `_ttm` (trailing 12m), `_fy` (fiscal year), `_fq` (quarter),
`_fh` (half-year), `_current`, plus growth variants `_qoq_growth_fq`,
`_yoy_growth_fq/fy/ttm`, `_cagr_5y`.

### 1. Security Info (25)

| UI Filter | UI ID | Scanner field(s) |
|---|---|---|
| Analyst rating | AnalystRating | `recommendation_mark` (1–5; `analyst_rating` needs auth) |
| CFI classification | CfiCode | `cfi_code` |
| Country of highest income | HighestIncomeCountry | `top_revenue_country_code` |
| Country or region of registration | Country | `country` |
| Exchange | Exchange | `exchange` (NASDAQ, NYSE, AMEX, OTC, CBOE) |
| Exchange market | SubMarket | `submarket` |
| Free float % | FloatShares | `float_shares_percent_current` |
| Free float | SharesFloat | `float_shares_outstanding_current` |
| Index | Index | `symbols.symbolset` (see Index IDs below) |
| Industry | Industry | `industry` (879 values) |
| IPO deal amount | IPODealAmount | `ipo_deal_amount_usd` |
| IPO offer date | IPOOfferDate | `ipo_offer_date` |
| IPO offer price | IPOOfferPrice | `ipo_offer_price_usd` |
| Number of shareholders | NumberOfShareholders | `number_of_shareholders` |
| Offer price performance % | IPOOfferPricePerformance | `ipo_offer_price_performance` |
| Primary listing | Listing | `is_primary` (bool) |
| Recent earnings date | EarningsRecent | `earnings_release_date` (unix ts) |
| Sector | Sector | `sector` |
| Sharia-compliant | Shariah | `is_shariah_compliant` (bool) |
| Symbol currency | SymbolCurrency | `currency` |
| Symbol type | SymbolType | `type` + `typespecs` |
| Target price | TargetPrice | `price_target_average` (also `_high/_low/_median`, `price_target_1y`) |
| Target price performance % | TargetPricePerformance | `price_target_1y_delta` |
| Total common shares outstanding | TotalSharesOutstanding | `total_shares_outstanding` |
| Upcoming earnings date | EarningsUpcoming | `earnings_release_next_date` (unix ts) |

### 2. Market Data (40)

| UI Filter | UI ID | Scanner field(s) |
|---|---|---|
| Average volume | AverageVolume | `average_volume_{10,30,60,90}d_calc` |
| Beta | Beta | `beta_1_year` (also `beta_3_year`, `beta_5_year`) |
| Change from open % / abs | ChangeFromOpen(Abs) | `change_from_open`, `change_from_open_abs` |
| Gap % | Gap | `gap` (also `gap_up`, `gap_down`) |
| High / Low / Open | High, Low, Open | `high`, `low`, `open` |
| Last trade time | LastTradeTime | `update_time` |
| New high / New low | New{High,Low}Simplified | compare `close` vs `High.5D/1M/3M/6M`, `price_52_week_high`, `High.All` (resp. `Low.*`) |
| Performance % | Performance | `Perf.W`, `Perf.1M`, `Perf.3M`, `Perf.6M`, `Perf.YTD`, `Perf.Y`, `Perf.5Y`, `Perf.10Y`, `Perf.All` |
| Price | Price | `close` |
| Price × average volume | PriceAvgVolume | `AvgValue.Traded_{10,30,60,90}d` |
| Price × volume (turnover) | VolumePrice | `Value.Traded` equivalent: use `AvgValue.Traded_10d` |
| Price change % / abs | Change(Abs) | `change`, `change_abs` |
| Relative volume | RelativeVolume | `relative_volume_10d_calc` |
| Relative volume at time | RelativeVolumeAtTime | `relative_volume_intraday\|5` |
| Volatility | Volatility | `Volatility.D`, `Volatility.W`, `Volatility.M` |
| Volume | Volume | `volume` |
| Volume change % / abs | VolumeChange(Abs) | `volume_change`, `volume_change_abs` |
| Post-market change/high/low/open/price/volume | PostMarket* | `postmarket_change(_abs)`, `postmarket_high`, `postmarket_low`, `postmarket_open`, `postmarket_close`, `postmarket_volume` |
| Pre-market change/from open/gap/high/low/open/price/volume | PreMarket* | `premarket_change(_abs)`, `premarket_change_from_open(_abs)`, `premarket_gap`, `premarket_high`, `premarket_low`, `premarket_open`, `premarket_close`, `premarket_volume` |

Also useful: `price_52_week_high`, `price_52_week_low`, `all_time_high`,
`all_time_low`, `High.All`, `Low.All`.

### 3. Technicals (39)

| UI Filter | UI ID | Scanner field(s) |
|---|---|---|
| Aroon | Aroon | `Aroon.Up`, `Aroon.Down` |
| Average daily range / % | AverageDailyRange, AverageDayRangePercent | `ADR`, `ADRP` |
| Average directional index | AverageDirectionalIndex | `ADX` |
| Average true range / % | AverageTrueRange(Percent) | `ATR`, `ATRP` |
| Awesome oscillator | AwesomeOscillator | `AO` (also `AO[1]`, `AO[2]`) |
| Bollinger Bands | BollingerBands | `BB.upper`, `BB.lower`, `BB.basis` (`_50` variants) |
| Bull bear power | BBPower | `BBPower` |
| Candlestick pattern | Pattern | `Candle.*` (44 patterns, see list below) |
| Chaikin money flow | ChaikinMoneyFlow | `ChaikinMoneyFlow` |
| Commodity channel index | CommodityChannelIndex | `CCI20` |
| Directional movement index | DirectionalMovementIndex | `ADX+DI`, `ADX-DI` (`_9/_20/_50/_100` variants) |
| Donchian channels | DonchianChannels | `DonchCh20.Upper`, `DonchCh20.Middle`, `DonchCh20.Lower` |
| Exponential moving average | Ema | `EMA5`…`EMA200` (5,10,20,30,50,100,200) |
| Hull moving average | HullMovingAverage | `HullMA9`, `HullMA20`, `HullMA200` |
| Ichimoku cloud | IchimokuCloud | `Ichimoku.BLine`, `Ichimoku.CLine`, `Ichimoku.Lead1`, `Ichimoku.Lead2` |
| Keltner channels | KeltnerChannels | `KltChnl.upper`, `KltChnl.basis`, `KltChnl.lower` |
| Momentum | Momentum | `Mom` |
| Money flow index | MoneyFlow | `MoneyFlow` |
| MACD | Macd | `MACD.macd`, `MACD.signal`, `MACD.hist` |
| Moving averages rating | RatingMa | `Recommend.MA` (−1…+1) |
| Oscillators rating | RatingOscillators | `Recommend.Other` (−1…+1) |
| Parabolic SAR | Psar | `P.SAR` |
| Pivot points (5 systems) | Pivot{Camarilla,Classic,Demark,Fibonacci,Woodie} | `Pivot.M.<System>.{S3,S2,S1,Middle,R1,R2,R3}` |
| Rate of change | RateOfChange | `ROC` |
| Relative strength index | RelativeStrengthIndex | `RSI` (14), `RSI7` |
| Simple moving average | Ma | `SMA5`…`SMA200` |
| Stochastic | Stochastic | `Stoch.K`, `Stoch.D` |
| Stochastic RSI | StochasticRsi | `Stoch.RSI.K`, `Stoch.RSI.D` |
| Technical rating | TechnicalRating | `Recommend.All` (−1…+1) |
| Ultimate oscillator | UltimateOscillator | `UO` |
| Volume-weighted average price | VolumeWeightedAveragePrice | `VWAP` |
| Volume-weighted moving average | VolumeWeightedMovingAverage | `VWMA` |
| Williams percent range | WilliamsPercentRange | `W.R` |

### 4. Financials (31)

| UI Filter | UI ID | Scanner field (default period) |
|---|---|---|
| Basic earnings per share | BasicEps | `earnings_per_share_basic_ttm` |
| Earnings per share diluted | EpsDiluted | `earnings_per_share_diluted_ttm` |
| Earnings per share estimate | EpsForecast | `earnings_per_share_forecast_next_fq` |
| Earnings per share reported | ReportedEps | `earnings_per_share_fq` |
| EBITDA | Ebitda | `ebitda_ttm` |
| Gross profit | GrossProfit | `gross_profit_ttm` |
| Net income | NetIncome | `net_income_ttm` |
| Net income from continuing operations | NetIncomeFromContinuingOperations | `income_from_cont_ops_ttm` |
| Net revenue | NetRevenue | `net_revenue_ttm` |
| Net revenue after provision | NetRevenueAfterProvision | `net_revenue_after_provision_ttm` |
| Operating income | OperatingIncome | `oper_income_ttm` |
| Research and development | ResearchNDevelopment | `research_and_dev_ttm` |
| Revenue estimate | RevenueEstimate | `revenue_forecast_next_fq` |
| Total revenue | TotalRevenue | `total_revenue_ttm` |
| Capital expenditures | CapitalExpenditures | `capital_expenditures_ttm` |
| Cash flow from financing | CashFromFinancing | `cash_f_financing_activities_ttm` |
| Cash flow from investing | CashFromInvesting | `cash_f_investing_activities_ttm` |
| Cash flow from operating | CashFromOperating | `cash_f_operating_activities_ttm` |
| Free cash flow | FreeCashFlow | `free_cash_flow_ttm` |
| Cash and equivalents | CashNEquivalents | `cash_n_equivalents_fq` |
| Cash and short-term investments | CashNShortTermInvest | `cash_n_short_term_invest_fq` |
| Goodwill (net) | Goodwill | `goodwill_fq` |
| Long-term debt | LTDebt | `long_term_debt_fq` |
| Net debt | NetDebt | `net_debt_fq` |
| Short-term debt | STDebt | `short_term_debt_fq` |
| Total assets | TotalAssets | `total_assets_fq` |
| Total current assets | TotalCurrentAssets | `total_current_assets_fq` |
| Total current liabilities | TotalCurrentLiabilities | `total_current_liabilities_fq` |
| Total debt | TotalDebt | `total_debt_fq` |
| Total equity | TotalEquity | `total_equity_fq` |
| Total liabilities | TotalLiabilities | `total_liabilities_fq` |

### 5. Valuation (18)

| UI Filter | UI ID | Scanner field |
|---|---|---|
| Earnings yield % | EarningsYield | `earnings_yield` |
| Enterprise value | Ev | `enterprise_value_current` |
| EV / EBITDA | EvToEbitda | `enterprise_value_ebitda_ttm` |
| EV / EBIT | EvToEbit | `enterprise_value_to_ebit_ttm` |
| EV / free cash flow | EvToFreeCashFlow | `enterprise_value_to_free_cash_flow_ttm` |
| EV / gross profit | EvToGrossProfit | `enterprise_value_to_gross_profit_ttm` |
| EV / revenue | EvToRevenue | `enterprise_value_to_revenue_ttm` |
| Forward non-GAAP P/E | ForwardPriceToEarnings | `price_earnings_forward_fy` |
| Market capitalization | MarketCap | `market_cap_basic` |
| Market cap performance % | MarketCapPerf | `Perf.{W,1M,3M,6M,YTD,Y,5Y}.MarketCap` |
| Price to book ratio | PriceToBook | `price_book_fq` |
| Price to cash flow ratio | PriceToCashFlow | `price_to_cash_f_operating_activities_ttm` |
| Price to cash ratio | PriceToCashRatio | `price_to_cash_ratio` |
| Price to earnings ratio | PriceToEarnings | `price_earnings_ttm` |
| PEG | PriceToEarningsToGrowth | `price_earnings_growth_ttm` |
| Price to free cash flow | PriceToFreeCashFlow | `price_free_cash_flow_ttm` |
| Price to net working capital | PriceToNetWorkingCapital | `price_to_working_capital_fq` |
| Price to sales ratio | PriceToSales | `price_sales_current` |

### 6. Growth (9)

| UI Filter | UI ID | Scanner field variants |
|---|---|---|
| Capital expenditures growth % | CapexGrowth | `capital_expenditures_{qoq_growth_fq,yoy_growth_fq,yoy_growth_fy,yoy_growth_ttm}` |
| EPS diluted growth % | EpsDilutedGrowth | `earnings_per_share_diluted_{qoq_growth_fq,yoy_growth_fq,yoy_growth_fy,yoy_growth_ttm}`, `earnings_per_share_basic_cagr_5y` |
| EBITDA growth % | EbitdaGrowth | `ebitda_{qoq_growth_fq,yoy_growth_fq,yoy_growth_fy,yoy_growth_ttm}` |
| Free cash flow growth % | FreeCashFlowGrowth | `free_cash_flow_{qoq_growth_fq,yoy_growth_fq,yoy_growth_fy,yoy_growth_ttm}`, `free_cash_flow_cagr_5y` |
| Gross profit growth % | GrossProfitGrowth | `gross_profit_{qoq_growth_fq,yoy_growth_fq,yoy_growth_fy,yoy_growth_ttm}` |
| Net income growth % | NetIncomeGrowth | `net_income_{qoq_growth_fq,yoy_growth_fq,yoy_growth_fy,yoy_growth_ttm}`, `net_income_cagr_5y` |
| Revenue growth % | RevenueGrowth | `total_revenue_{qoq_growth_fq,yoy_growth_fq,yoy_growth_fy,yoy_growth_ttm}`, `total_revenue_cagr_5y` |
| Total assets growth % | TotalAssetsGrowth | `total_assets_{qoq_growth_fq,yoy_growth_fq,yoy_growth_fy}` |
| Total debt growth % | TotalDebtGrowth | `total_debt_{qoq_growth_fq,yoy_growth_fq,yoy_growth_fy}` |

### 7. Margins & Ratios (68)

| UI Filter | Scanner field (default period) |
|---|---|
| Altman Z-score | `altman_z_score_ttm` |
| Assets to equity ratio | `total_assets_to_equity_fq` |
| Asset turnover | `asset_turnover_current` |
| Book value per share | `book_value_per_share_fq` |
| Buyback yield % | `buyback_yield` |
| Capital expenditures per share | `capex_per_share_ttm` |
| Cash dividend coverage ratio | `cash_dividend_coverage_ratio_ttm` |
| Cash per share | `cash_per_share_fq` |
| Cash ratio | `cash_ratio` |
| Cash to debt ratio | `cash_n_short_term_invest_to_total_debt_fq` |
| Current ratio | `current_ratio_fq` |
| Debt to assets ratio | `debt_to_asset_fq` |
| Debt to EBITDA ratio | `total_debt_to_ebitda_fq` |
| Debt to equity ratio | `debt_to_equity_fq` |
| Debt to revenue ratio | `debt_to_revenue_ttm` |
| EBITDA interest coverage | `ebitda_interst_cover_ttm` |
| EBITDA less CapEx interest coverage | `ebitda_less_capex_interst_cover_ttm` |
| EBITDA margin % | `ebitda_margin_ttm` |
| EBITDA per employee | `ebitda_per_employee_fy` |
| EBITDA per share | `ebitda_per_share_ttm` |
| EBIT per share | `ebit_per_share_ttm` |
| Effective interest rate on debt % | `effective_interest_rate_on_debt_ttm` |
| Equity to assets ratio | `shrhldrs_equity_to_total_assets_fq` |
| Fixed assets turnover | `fixed_assets_turnover_fq` |
| Free cash flow margin % | `free_cash_flow_margin_ttm` |
| Free cash flow per employee | `free_cash_flow_per_employee_fy` |
| Free cash flow per share | `free_cash_flow_per_share_ttm` |
| Graham's number | `graham_numbers_ttm` |
| Gross margin % | `gross_margin_ttm` |
| Interest coverage | `interst_cover_ttm` |
| Inventory turnover | `invent_turnover_current` |
| Net current asset value per share | `ncavps_ratio_current` |
| Net debt to EBITDA ratio | `net_debt_to_ebitda_fq` |
| Net income per employee | `net_income_per_employee_fy` |
| Net margin % | `net_margin_ttm` |
| Number of employees | `number_of_employees` |
| Operating cash flow per share | `operating_cash_flow_per_share_ttm` |
| Operating income per employee | `oper_income_per_employee_fy` |
| Operating margin % | `operating_margin_ttm` |
| Piotroski F-score | `piotroski_f_score_ttm` |
| Pretax margin % | `pre_tax_margin_ttm` |
| Quick ratio | `quick_ratio_fq` |
| R&D per employee | `research_and_dev_per_employee_fy` |
| R&D ratio | `research_and_dev_ratio_ttm` |
| Return on assets % | `return_on_assets_fq` |
| Return on capital employed % | `return_on_capital_employed_fq` |
| Return on common equity % | `return_on_common_equity_ttm` |
| Return on equity % | `return_on_equity_fq` |
| ROE adjusted to book value % | `return_on_equity_adjust_to_book_ttm` |
| Return on invested capital % | `return_on_invested_capital_fq` |
| Return on tangible assets % | `return_on_tang_assets_fq` |
| Return on tangible equity % | `return_on_tang_equity_fq` |
| Return on total capital % | `return_on_total_capital_fq` |
| Revenue per employee | `revenue_per_employee` |
| Revenue per share | `revenue_per_share_ttm` |
| SG&A expenses ratio | `sell_gen_admin_exp_other_ratio_ttm` |
| Shares buyback ratio % | `share_buyback_ratio_fq` |
| Sloan ratio % | `sloan_ratio_ttm` |
| Sustainable growth rate | `sustainable_growth_rate_ttm` |
| Tangible book value per share | `book_tangible_per_share_fq` |
| Tobin's Q (approximate) | `tobin_q_ratio_fq` |
| Total assets per employee | `total_assets_per_employee_fy` |
| Total debt per employee | `total_debt_per_employee_fy` |
| Total debt per share | `total_debt_per_share_fq` |
| Total debt to capital | `total_debt_to_capital_fq` |
| Total receivables turnover | `receivables_turnover_fq` |
| Working capital per share | `working_capital_per_share_fq` |
| Zmijewski score | `zmijewski_score_ttm` |

### 8. Dividends (8)

| UI Filter | UI ID | Scanner field |
|---|---|---|
| Continuous dividend growth | ContinuousDivGrowth | `continuous_dividend_growth` (years) |
| Continuous dividend payout | ContinuousDivPayout | `continuous_dividend_payout` (years) |
| Dividend payout ratio % | DividendPayoutRatio | `dividend_payout_ratio_ttm` |
| Dividends per share | DividendsPerShare | `dps_common_stock_prim_issue_fy` (also `_ttm`, `dividends_per_share_fq`) |
| Dividends per share growth % | DividendsPerShareGrowth | `dps_common_stock_prim_issue_yoy_growth_fy` |
| Dividend yield % | DividendsYield | `dividends_yield_current` (also `_fy`, `dividend_yield_recent`) |
| Dividend yield % (indicated) | DividendsYieldForward | `dividends_yield` |
| Total cash dividends paid | DividendsPaid | `total_cash_dividends_paid_ttm` |

Bonus date fields: `dividend_ex_date_upcoming`, `dividend_ex_date_recent`,
`dividend_payment_date_upcoming`, `dividend_amount_upcoming` (unix ts / USD).

---

## Enum Values

### Sectors (`sector`, US market uses this taxonomy)

Commercial Services, Communications, Consumer Durables, Consumer
Non-Durables, Consumer Services, Distribution Services, Electronic
Technology, Energy Minerals, Finance, Government, Health Services, Health
Technology, Industrial Services, Miscellaneous, Non-Energy Minerals,
Process Industries, Producer Manufacturing, Retail Trade, Technology
Services, Transportation, Utilities

(Other taxonomies — e.g. "Technology", "Healthcare" — exist for other
markets; for `--market america` use the list above.)

### Index IDs (`symbols.symbolset`)

| CLI value | Symbolset ID | Index |
|---|---|---|
| `sp500` | `SYML:SP;SPX` | S&P 500 |
| `sp100` | `SYML:SP;OEX` | S&P 100 |
| `sp400` | `SYML:SP;MID` | S&P MidCap 400 |
| `nasdaq100` | `SYML:NASDAQ;NDX` | NASDAQ 100 |
| `nasdaqcomposite` | `SYML:NASDAQ;IXIC` | NASDAQ Composite |
| `dow30` | `SYML:DJ;DJI` | Dow Jones Industrial Average |
| `russell1000` | `SYML:TVC;RUI` | Russell 1000 |
| `russell2000` | `SYML:TVC;RUT` | Russell 2000 |
| `russell3000` | `SYML:TVC;RUA` | Russell 3000 |

830 more index IDs exist (any `SYML:...` value passes through `--index`).

### Ratings

| Category | `recommendation_mark` (analyst) | `Recommend.All` / `.MA` / `.Other` (technical) |
|---|---|---|
| Strong buy | 1.0 – 1.5 | 0.5 … 1.0 |
| Buy | 1.5 – 2.5 | 0.1 … 0.5 |
| Neutral / Hold | 2.5 – 3.5 | −0.1 … 0.1 |
| Sell | 3.5 – 4.5 | −0.5 … −0.1 |
| Strong sell | 4.5 – 5.0 | −1.0 … −0.5 |

### Candlestick patterns (`Candle.*` numeric fields, value 1 = detected)

Filter as `Candle.Hammer=1`, `Candle.Engulfing.Bullish=1`, etc. (44 fields,
verified against metainfo):

`Candle.3BlackCrows`, `Candle.3WhiteSoldiers`, `Candle.AbandonedBaby.Bullish/.Bearish`,
`Candle.DarkCloudCover.Bearish`, `Candle.Doji`, `Candle.Doji.Dragonfly`,
`Candle.Doji.Gravestone`, `Candle.DojiStar.Bullish/.Bearish`,
`Candle.Engulfing.Bullish/.Bearish`, `Candle.EveningStar`,
`Candle.EveningDojiStar.Bearish`, `Candle.FallingThreeMethods.Bearish`,
`Candle.FallingWindow.Bearish`, `Candle.Hammer`, `Candle.HangingMan`,
`Candle.Harami.Bullish/.Bearish`, `Candle.HaramiCross.Bullish/.Bearish`,
`Candle.InvertedHammer`, `Candle.Kicking.Bullish/.Bearish`,
`Candle.LongShadow.Lower/.Upper`, `Candle.Marubozu.Black/.White`,
`Candle.MorningStar`, `Candle.MorningDojiStar.Bullish`, `Candle.OnNeck.Bearish`,
`Candle.Piercing.Bullish`, `Candle.RisingThreeMethods.Bullish`,
`Candle.RisingWindow.Bullish`, `Candle.ShootingStar`,
`Candle.SpinningTop.Black/.White`, `Candle.TriStar.Bullish/.Bearish`,
`Candle.TweezerBottom.Bullish`, `Candle.TweezerTop.Bearish`,
`Candle.UpsideTasukiGap.Bullish`, `Candle.DownsideTasukiGap.Bearish`

### Markets (`--market` / endpoint)

`america` (default), `global` (entire world), plus per-country endpoints:
`germany`, `uk`, `japan`, `india`, `china`, `hongkong`, `france`, `italy`,
`spain`, `switzerland`, `canada`, `australia`, `brazil`, `korea`, `taiwan`,
and others — same field names apply.

---

## The All Stocks Universe Block (`filter2`)

Captured verbatim from the All Stocks tab:

```json
{
  "operator": "and",
  "operands": [
    {"operation": {"operator": "or", "operands": [
      {"operation": {"operator": "and", "operands": [
        {"expression": {"left": "type", "operation": "equal", "right": "stock"}},
        {"expression": {"left": "typespecs", "operation": "has", "right": ["common"]}}]}},
      {"operation": {"operator": "and", "operands": [
        {"expression": {"left": "type", "operation": "equal", "right": "stock"}},
        {"expression": {"left": "typespecs", "operation": "has", "right": ["preferred"]}}]}},
      {"operation": {"operator": "and", "operands": [
        {"expression": {"left": "type", "operation": "equal", "right": "dr"}}]}},
      {"operation": {"operator": "and", "operands": [
        {"expression": {"left": "type", "operation": "equal", "right": "fund"}},
        {"expression": {"left": "typespecs", "operation": "has_none_of", "right": ["etf", "mutual"]}}]}}
    ]}},
    {"expression": {"left": "typespecs", "operation": "has_none_of", "right": ["pre-ipo"]}}
  ]
}
```

The default flat filter additionally applies `is_blacklisted = false` and
`is_primary = true` (primary listing only).
