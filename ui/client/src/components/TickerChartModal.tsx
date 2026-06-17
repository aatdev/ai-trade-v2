import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useFundamentals, useOhlcv } from '../api';
import { fmtNum } from '../lib/format';
import CandleChart, { type ChartLevels, visibleMas } from './CandleChart';
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
 * Modal candlestick chart for a ticker: live candles + volume + MAs from the
 * TradingView data layer, with the caller's entry/stop/target overlaid. Driven
 * by a plain `ticker` + `levels`, so both the watchlist and screener tables
 * (and any future caller) can reuse it.
 */
export default function TickerChartModal({
  ticker: tickerProp,
  levels,
  hasAnalysis,
  onClose,
  onOpenAnalysis,
}: {
  ticker: string;
  levels: ChartLevels;
  hasAnalysis: boolean;
  onClose: () => void;
  /** When set, the "Open analysis" control opens the analysis modal in place
   *  instead of navigating to the standalone ticker page. */
  onOpenAnalysis?: () => void;
}) {
  const [tf, setTf] = useState('D');
  const ticker = tickerProp.toUpperCase();
  const { data, isLoading, error } = useOhlcv(ticker, tf, 320);
  const theme = document.documentElement.dataset.theme ?? 'dark';

  const bars = data?.ok ? data.bars : [];

  // The fundamentals endpoint resolves the exchange itself (the `tv bars` CLI
  // only echoes back the bare ticker), so just hand it the symbol.
  const { data: funda } = useFundamentals(ticker);

  const title = (
    <span className="chart-title">
      {ticker} <SideBadge side={levels.side} />
      {data?.resolved && data.resolved !== ticker ? (
        <span className="muted" style={{ fontSize: 12 }}>
          {data.resolved}
        </span>
      ) : null}
      {bars.length ? <ChangePill bars={bars} /> : null}
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
          <CandleChart bars={bars} levels={levels} theme={theme} timeframe={tf} />
        )}
      </div>
    </Modal>
  );
}
