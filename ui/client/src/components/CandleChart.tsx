import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type LineData,
  type SeriesMarker,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { OhlcvBar, Side } from '@shared/types';

/** Watchlist levels overlaid as horizontal price lines. */
export interface ChartLevels {
  side?: Side;
  entry?: number | null; // pivot / worst-entry
  stop?: number | null;
  target?: number | null;
  t1?: number | null;
  t2?: number | null;
  t3?: number | null;
}

/**
 * Current extended-hours quote, drawn as a horizontal reference line + axis
 * label so the live pre/post-market price is visible against the candles (on
 * every timeframe, not just the intraday ones that carry pre/post bars).
 */
export interface ExtQuoteLine {
  price: number;
  changePct: number | null;
  kind: 'pre' | 'post';
}

/** Moving averages drawn over the candles. Colors are theme-independent. */
export const MA_DEFS = [
  { period: 20, color: '#f5b301', label: 'MA20' },
  { period: 50, color: '#4493f8', label: 'MA50' },
  { period: 200, color: '#db61a2', label: 'MA200' },
] as const;

/** Which MAs have enough bars to be plotted (used for the legend). */
export function visibleMas(barCount: number) {
  return MA_DEFS.filter((m) => barCount >= m.period);
}

/** True when the series carries any pre/post-market bars (drives the legend). */
export function hasExtendedBars(bars: OhlcvBar[]): boolean {
  return bars.some((b) => b.session === 'pre' || b.session === 'post');
}

/** Pre/post-market bars are dimmed; hex alpha appended to the up/down color. */
const EXT_BODY_ALPHA = '73'; // ~45%
const EXT_VOL_ALPHA = '33'; // ~20%

/** Human-readable session label for the hover tooltip. */
const SESSION_LABEL: Record<NonNullable<OhlcvBar['session']>, string> = {
  pre: 'Pre-market',
  rth: 'Regular hours',
  post: 'After-hours',
};

/** Simple moving average of close; emitted only once `period` bars exist. */
function sma(bars: OhlcvBar[], period: number): LineData[] {
  if (bars.length < period) return [];
  const out: LineData[] = [];
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close;
    if (i >= period) sum -= bars[i - period].close;
    if (i >= period - 1) out.push({ time: bars[i].time as UTCTimestamp, value: sum / period });
  }
  return out;
}

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Compact volume label, e.g. 70.11M / 1.2K. */
function fmtVol(v: number | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(Math.round(v));
}

function fmtPrice(v: number | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(v >= 1 ? 2 : 4);
}

/** UTCTimestamp (seconds) → date (+ UTC time for intraday) for the hover tooltip. */
function fmtTime(time: number, intraday: boolean): string {
  const iso = new Date(time * 1000).toISOString();
  return intraday ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : iso.slice(0, 10);
}

export const INTRADAY_TFS = new Set(['5', '15', '30', '60', '120', '240']);

/**
 * Candlestick + volume + moving-average chart for one ticker, driven by OHLCV
 * pulled from the live TradingView data layer. Re-renders from scratch whenever
 * the bars, overlay levels, or theme change (cheap — it lives in a modal).
 */
export default function CandleChart({
  bars,
  levels,
  extQuote,
  theme,
  timeframe = 'D',
}: {
  bars: OhlcvBar[];
  levels?: ChartLevels;
  extQuote?: ExtQuoteLine | null;
  theme: string;
  timeframe?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || bars.length === 0) return;

    const intraday = INTRADAY_TFS.has(timeframe);

    const text = cssVar('--text', '#e6edf3');
    const bg = cssVar('--bg-elev', '#161b22');
    const border = cssVar('--border', '#2a313c');
    const up = cssVar('--green', '#3fb950');
    const down = cssVar('--red', '#f0506e');
    const accent = cssVar('--accent', '#4493f8');
    const yellow = cssVar('--yellow', '#d6a930');

    const chart: IChartApi = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: bg },
        textColor: text,
        fontSize: 11,
      },
      grid: {
        vertLines: { color: border },
        horzLines: { color: border },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: border },
      timeScale: { borderColor: border, rightOffset: 4, timeVisible: intraday, secondsVisible: false },
    });

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      wickUpColor: up,
      wickDownColor: down,
      borderVisible: false,
    });
    candle.setData(
      bars.map((b): CandlestickData => {
        const bar: CandlestickData = {
          time: b.time as UTCTimestamp,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        };
        // Dim extended-hours candles so the regular session stands out, while
        // keeping the up/down direction readable. RTH bars use series defaults.
        if (b.session === 'pre' || b.session === 'post') {
          const c = b.close >= b.open ? up : down;
          bar.color = `${c}${EXT_BODY_ALPHA}`;
          bar.wickColor = `${c}${EXT_BODY_ALPHA}`;
          bar.borderColor = `${c}${EXT_BODY_ALPHA}`;
        }
        return bar;
      }),
    );

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(
      bars.map((b): HistogramData => {
        const ext = b.session === 'pre' || b.session === 'post';
        const c = b.close >= b.open ? up : down;
        return {
          time: b.time as UTCTimestamp,
          value: b.volume,
          color: `${c}${ext ? EXT_VOL_ALPHA : '66'}`,
        };
      }),
    );

    for (const ma of MA_DEFS) {
      const data = sma(bars, ma.period);
      if (data.length === 0) continue;
      const line = chart.addSeries(LineSeries, {
        color: ma.color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(data);
    }

    const priceLine = (price: number | null | undefined, color: string, title: string) => {
      if (price == null || !Number.isFinite(price)) return;
      candle.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title,
      });
    };
    if (levels) {
      priceLine(levels.entry, accent, 'Entry');
      priceLine(levels.stop, down, 'Stop');
      priceLine(levels.target, up, 'Target');
      priceLine(levels.t1, up, 'T1');
      priceLine(levels.t2, up, 'T2');
      priceLine(levels.t3, up, 'T3');
    }

    // Live pre/post-market price as a dotted reference line (yellow, distinct
    // from the dashed trade levels). Shown on every timeframe so the current
    // extended-hours quote is placed against the candles, not just in the pill.
    if (extQuote && Number.isFinite(extQuote.price)) {
      candle.createPriceLine({
        price: extQuote.price,
        color: yellow,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: true,
        title: extQuote.kind === 'pre' ? 'PRE' : 'POST',
      });
    }

    // Mark the most recent regular-session open (the first RTH bar after a
    // pre/overnight gap) so the premarket run-up is easy to place on intraday.
    let lastOpen: UTCTimestamp | null = null;
    for (let i = 1; i < bars.length; i++) {
      if (bars[i].session === 'rth' && bars[i - 1].session !== 'rth') {
        lastOpen = bars[i].time as UTCTimestamp;
      }
    }
    if (lastOpen != null) {
      const markers: SeriesMarker<UTCTimestamp>[] = [
        { time: lastOpen, position: 'belowBar', color: accent, shape: 'arrowUp', text: 'RTH' },
      ];
      createSeriesMarkers(candle, markers);
    }

    // Session by bar time, for the hover tooltip.
    const sessionByTime = new Map<number, NonNullable<OhlcvBar['session']>>();
    for (const b of bars) if (b.session) sessionByTime.set(b.time, b.session);

    chart.timeScale().fitContent();

    // Floating tooltip: show the hovered bar's date, OHLC and volume.
    chart.subscribeCrosshairMove((param) => {
      const tip = tooltipRef.current;
      if (!tip) return;
      const point = param.point;
      if (param.time == null || !point || point.x < 0 || point.y < 0) {
        tip.style.display = 'none';
        return;
      }
      const c = param.seriesData.get(candle) as CandlestickData | undefined;
      const v = param.seriesData.get(volume) as HistogramData | undefined;
      if (!c && !v) {
        tip.style.display = 'none';
        return;
      }
      const isUp = c ? c.close >= c.open : true;
      const volColor = isUp ? up : down;
      const session = sessionByTime.get(param.time as number);
      tip.innerHTML =
        `<div class="t-date">${fmtTime(param.time as number, intraday)}</div>` +
        (c
          ? `<div class="t-row">O ${fmtPrice(c.open)}  H ${fmtPrice(c.high)}  L ${fmtPrice(c.low)}  C ${fmtPrice(c.close)}</div>`
          : '') +
        `<div class="t-row">Vol <b style="color:${volColor}">${fmtVol(v?.value)}</b></div>` +
        (session && session !== 'rth'
          ? `<div class="t-row t-session">${SESSION_LABEL[session]}</div>`
          : '');
      tip.style.display = 'block';

      // Position next to the cursor, flipping/clamping to stay inside the chart.
      const pad = 12;
      const w = tip.offsetWidth;
      const h = tip.offsetHeight;
      let left = point.x + pad;
      if (left + w > el.clientWidth) left = point.x - w - pad;
      left = Math.max(0, Math.min(left, el.clientWidth - w));
      let top = point.y + pad;
      if (top + h > el.clientHeight) top = point.y - h - pad;
      top = Math.max(0, top);
      tip.style.left = `${left}px`;
      tip.style.top = `${top}px`;
    });

    return () => chart.remove();
  }, [bars, levels, extQuote, theme, timeframe]);

  return (
    <div className="candle-chart-wrap">
      <div ref={containerRef} className="candle-chart" />
      <div ref={tooltipRef} className="candle-tooltip" style={{ display: 'none' }} />
    </div>
  );
}
