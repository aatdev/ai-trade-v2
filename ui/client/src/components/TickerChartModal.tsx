import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { FundamentalsResponse, IbSnapshot } from '@shared/types';
import { useFundamentals, useIbSnapshot, useOhlcv } from '../api';
import { isDeadOrder, isLimit, isStop, rowsForThesis } from '../lib/ibBrackets';
import { fmtNum, fmtSignedPct } from '../lib/format';
import CandleChart, {
  type ChartLevels,
  type ExtQuoteLine,
  hasExtendedBars,
  INTRADAY_TFS,
  visibleMas,
} from './CandleChart';
import CompanyInfoBar from './CompanyInfoBar';
import { Empty, ErrorNote, Loading, Modal, SideBadge } from './ui';

const TIMEFRAMES: { key: string; label: string }[] = [
  { key: '5', label: '5m' },
  { key: '60', label: '1H' },
  { key: 'D', label: '1D' },
  { key: 'W', label: '1W' },
  { key: 'M', label: '1M' },
];

function ChangePill({ bars }: { bars: { close: number }[] }) {
  if (bars.length < 2) return null;
  const last = bars[bars.length - 1].close;
  const prev = bars[bars.length - 2].close;
  const pct = prev ? ((last - prev) / prev) * 100 : 0;
  const color = pct >= 0 ? 'var(--green)' : 'var(--red)';
  return (
    <span className="muted" style={{ fontSize: 13 }}>
      {fmtNum(last)}{' '}
      <span style={{ color }}>
        {pct >= 0 ? '+' : ''}
        {pct.toFixed(2)}%
      </span>
    </span>
  );
}

/**
 * Pick the live extended-hours quote to surface: premarket while it runs,
 * otherwise after-hours. Shared by the header pill and the chart's price line
 * so both label the same quote. `changePct` is the move vs the prior regular
 * close (TradingView scanner).
 */
function pickExtQuote(funda?: FundamentalsResponse): ExtQuoteLine | null {
  const pre = funda?.premarket;
  if (pre) return { price: pre.price, changePct: pre.changePct, kind: 'pre' };
  const post = funda?.postmarket;
  if (post) return { price: post.price, changePct: post.changePct, kind: 'post' };
  return null;
}

/** Live extended-hours quote rendered as a pill in the chart title. */
function ExtHoursPill({ quote }: { quote: ExtQuoteLine | null }) {
  if (!quote) return null;
  const { price, changePct: pct, kind } = quote;
  const color = pct == null ? 'var(--muted)' : pct >= 0 ? 'var(--green)' : 'var(--red)';
  return (
    <span className="ext-pill" title={kind === 'pre' ? 'Премаркет' : 'Постмаркет (after-hours)'}>
      <span className="ext-pill-tag">{kind === 'pre' ? 'PRE' : 'POST'}</span>
      <span className="ext-pill-price">{fmtNum(price)}</span>
      {pct != null ? <span style={{ color }}>{fmtSignedPct(pct)}</span> : null}
    </span>
  );
}

/** Live IB order summary for a thesis, surfaced as a pill next to the price. */
interface OrderSummary {
  label: string; // Выставлен / Исполнен
  color: string;
  side: string | null; // BUY / SELL
  qty: number | null;
  entryPrice: number | null; // parent pivot (limit/stop) while working
  stopPrice: number | null; // protective stop leg
  targetPrice: number | null; // take-profit leg
  filledPrice: number | null; // avg entry from the matching position once filled
}

/**
 * Resolve the live IB bracket/order for a thesis into a flat summary. Mirrors the
 * Память table's order status (a working, unfilled leg → "Выставлен"; only filled
 * legs → "Исполнен"). The fill price comes from the matching position's avg_cost —
 * IbOrder carries no average fill price of its own. Returns null when there is no
 * live order for the thesis (or IB is unavailable).
 */
function orderSummaryFor(
  ib: IbSnapshot | undefined,
  thesisId: string | undefined,
  ticker: string,
): OrderSummary | null {
  if (!thesisId || !ib?.orders?.length) return null;
  const rows = rowsForThesis(ib.orders, thesisId);
  if (!rows.length) return null;
  const row = rows[0];
  const legs = row.kind === 'bracket' ? row.legs : [row.order];
  const working = legs.some(
    (l) => !isDeadOrder(l.status) && !(l.status ?? '').toLowerCase().includes('fill'),
  );

  let side: string | null;
  let qty: number | null;
  let entryPrice: number | null;
  let stopPrice: number | null;
  let targetPrice: number | null;
  if (row.kind === 'bracket') {
    side = row.side;
    qty = row.quantity;
    entryPrice = row.entryPrice;
    stopPrice = row.stop?.stop_price ?? null;
    targetPrice = row.target?.limit_price ?? null;
  } else {
    const o = row.order;
    side = o.side;
    qty = o.total_quantity;
    entryPrice = isStop(o) ? o.stop_price : o.limit_price;
    stopPrice = isStop(o) ? o.stop_price : null;
    targetPrice = isLimit(o) && !isStop(o) ? o.limit_price : null;
  }

  // Once the entry has filled, the open position's average cost is the realized
  // entry price (the parent leg drops off the order snapshot at that point).
  const filledPrice = working
    ? null
    : (ib.positions?.find((p) => p.symbol?.toUpperCase() === ticker.toUpperCase())?.avg_cost ??
      null);

  return {
    label: working ? 'Выставлен' : 'Исполнен',
    color: working ? 'var(--accent)' : 'var(--green)',
    side,
    qty,
    entryPrice,
    stopPrice,
    targetPrice,
    filledPrice,
  };
}

/** Live IB order rendered as a pill in the chart title, beside the price. */
function OrderPill({ s }: { s: OrderSummary }) {
  const sideQty = [s.side?.toUpperCase(), s.qty != null ? `${fmtNum(s.qty, 0)} шт` : null]
    .filter(Boolean)
    .join(' ');
  return (
    <span className="ext-pill order-pill" title="Живой ордер IB по тезису">
      <span className="ext-pill-tag" style={{ color: s.color }}>
        ОРДЕР
      </span>
      <span style={{ color: s.color, fontWeight: 600 }}>{s.label}</span>
      {sideQty ? <span>{sideQty}</span> : null}
      {s.filledPrice != null ? <span className="ext-pill-price">@ {fmtNum(s.filledPrice)}</span> : null}
      {s.entryPrice != null ? <span className="ext-pill-price">вход {fmtNum(s.entryPrice)}</span> : null}
      {s.stopPrice != null ? <span className="ext-pill-price">стоп {fmtNum(s.stopPrice)}</span> : null}
      {s.targetPrice != null ? <span className="ext-pill-price">тейк {fmtNum(s.targetPrice)}</span> : null}
    </span>
  );
}

/**
 * Modal candlestick chart for a ticker: live candles + volume + MAs from the
 * TradingView data layer, with the caller's entry/stop/target overlaid. Driven
 * by a plain `ticker` + `levels`, so both the watchlist and screener tables
 * (and any future caller) can reuse it.
 */
export default function TickerChartModal({
  ticker: tickerProp,
  levels,
  hasAnalysis,
  thesisId,
  onClose,
  onOpenAnalysis,
}: {
  ticker: string;
  levels: ChartLevels;
  hasAnalysis: boolean;
  /** When set (opened from a thesis), surface that thesis's live IB order as a
   *  pill next to the price. Omitted for screener/watchlist callers. */
  thesisId?: string;
  onClose: () => void;
  /** When set, the "Open analysis" control opens the analysis modal in place
   *  instead of navigating to the standalone ticker page. */
  onOpenAnalysis?: () => void;
}) {
  const [tf, setTf] = useState('D');
  const ticker = tickerProp.toUpperCase();
  // Intraday timeframes request extended hours so pre/post-market bars are
  // included (and shaded); daily+ have no separate extended-hours bars.
  const extended = INTRADAY_TFS.has(tf);
  const { data, isLoading, error } = useOhlcv(ticker, tf, 320, true, extended);
  const theme = document.documentElement.dataset.theme ?? 'dark';

  const bars = data?.ok ? data.bars : [];

  // The fundamentals endpoint resolves the exchange itself (the `tv bars` CLI
  // only echoes back the bare ticker), so just hand it the symbol.
  const { data: funda } = useFundamentals(ticker);

  // react-query keeps `funda` referentially stable across renders (structural
  // sharing), so this only recomputes when the quote actually changes — which
  // keeps the chart effect from rebuilding on unrelated re-renders.
  const extQuote = useMemo(() => pickExtQuote(funda), [funda]);

  // Live IB order for this thesis (shared ['ib'] query key with the IB tab /
  // Память card — no extra fetch when those are mounted). Only when opened from
  // a thesis; screener/watchlist callers pass no thesisId.
  const { data: ib } = useIbSnapshot();
  const orderSummary = useMemo(() => orderSummaryFor(ib, thesisId, ticker), [ib, thesisId, ticker]);

  const title = (
    <span className="chart-title">
      {ticker} <SideBadge side={levels.side} />
      {data?.resolved && data.resolved !== ticker ? (
        <span className="muted" style={{ fontSize: 12 }}>
          {data.resolved}
        </span>
      ) : null}
      {bars.length ? <ChangePill bars={bars} /> : null}
      <ExtHoursPill quote={extQuote} />
      {orderSummary ? <OrderPill s={orderSummary} /> : null}
    </span>
  );

  return (
    <Modal
      title={title}
      onClose={onClose}
      fullscreen
      footer={<button onClick={onClose}>Close</button>}
    >
      <CompanyInfoBar funda={funda} />

      <div className="chart-toolbar">
        <div className="tabs" style={{ marginBottom: 0 }}>
          {TIMEFRAMES.map((t) => (
            <button
              key={t.key}
              className={`tab ${t.key === tf ? 'active' : ''}`}
              onClick={() => setTf(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="chart-legend">
          {visibleMas(bars.length).map((m) => (
            <span key={m.period} className="legend-item">
              <span className="swatch" style={{ background: m.color }} />
              {m.label}
            </span>
          ))}
          {hasExtendedBars(bars) ? (
            <span className="legend-item" title="Премаркет / постмаркет (приглушённые свечи)">
              <span className="swatch swatch--ext" />
              Pre / post
            </span>
          ) : null}
          {extQuote ? (
            <span
              className="legend-item"
              title="Текущая цена премаркет / постмаркет (пунктирная линия)"
            >
              <span className="swatch swatch--ext-line" />
              {extQuote.kind === 'pre' ? 'PRE' : 'POST'}
            </span>
          ) : null}
        </div>
        <span style={{ flex: 1 }} />
        {hasAnalysis ? (
          onOpenAnalysis ? (
            <button type="button" className="chart-analysis-link link-btn" onClick={onOpenAnalysis}>
              📄 Open analysis →
            </button>
          ) : (
            <Link to={`/ticker/${ticker}`} className="chart-analysis-link">
              📄 Open analysis →
            </Link>
          )
        ) : null}
      </div>

      <div className="chart-body">
        {isLoading ? (
          <Loading />
        ) : error ? (
          <ErrorNote error={error} />
        ) : !data?.ok ? (
          <Empty>
            {data?.error ?? 'No data.'} (TradingView Desktop must be running with CDP on :9222.)
          </Empty>
        ) : (
          <CandleChart bars={bars} levels={levels} extQuote={extQuote} theme={theme} timeframe={tf} />
        )}
      </div>
    </Modal>
  );
}
