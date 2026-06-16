import { Fragment, Suspense, lazy, useState } from 'react';
import type { ScreenerCandidate, ScreenerResult } from '@shared/types';
import { fmtNum, fmtScore } from '../lib/format';
import { scoreColor } from '../lib/zones';
import type { ChartLevels } from './CandleChart';
import { Empty, Gauge, GradeBadge } from './ui';

// Code-split the charting lib — loads only once a ticker is clicked.
const TickerChartModal = lazy(() => import('./TickerChartModal'));

/** 5-factor weakness model (mirror scorer.COMPONENT_WEIGHTS); order = display order. */
const COMPONENTS: { key: string; label: string; weight: number }[] = [
  { key: 'trend_structure', label: 'Тренд (Stage 4)', weight: 0.3 },
  { key: 'relative_strength', label: 'Отн. слабость', weight: 0.25 },
  { key: 'base_breakdown', label: 'Пробой базы', weight: 0.2 },
  { key: 'lower_highs', label: 'Нижние максимумы', weight: 0.15 },
  { key: 'liquidity', label: 'Ликвидность / borrow', weight: 0.1 },
];

function num(metrics: Record<string, number | boolean | null>, key: string): number | null {
  const v = metrics[key];
  return typeof v === 'number' ? v : null;
}

/** Swing-short candidates are short-only; overlay entry/stop/target on the chart. */
function chartLevels(c: ScreenerCandidate): ChartLevels {
  return { side: 'short', entry: c.entry, stop: c.stop, target: c.target };
}

/** Derive the oversold/extended warning from metrics (RSI<25 or >20% below MA50). */
function oversoldNote(c: ScreenerCandidate): string | null {
  const rsi = num(c.metrics, 'rsi14');
  const price = num(c.metrics, 'price');
  const ma50 = num(c.metrics, 'ma50');
  const pctBelow = price != null && ma50 ? ((ma50 - price) / ma50) * 100 : null;
  if ((rsi != null && rsi < 25) || (pctBelow != null && pctBelow > 20)) {
    return 'Oversold/extended — риск отскока (mean-reversion). Лучше ретест нижнего максимума, чем гнать пробой.';
  }
  return null;
}

/** Sector-RS warning: shorting into a leading sector caps the grade at C. */
function sectorNote(c: ScreenerCandidate): string | null {
  if (!c.sector_fight) return null;
  const rs = c.sector_rs != null ? `${c.sector_rs >= 0 ? '+' : ''}${c.sector_rs.toFixed(0)}%` : '';
  return `Сектор ${c.sector_etf ?? ''} ${rs} лидирует над SPY — шорт против сильной группы; грейд снижен до C. Лучше шортить в отстающих секторах.`;
}

function MetricRow({ c }: { c: ScreenerCandidate }) {
  const m = c.metrics;
  const r = num(m, 'stock_return');
  const adv = num(m, 'avg_dollar_vol');
  const cells: [string, string][] = [
    ['Цена', fmtNum(num(m, 'price'))],
    ['MA50', fmtNum(num(m, 'ma50'))],
    ['MA200', fmtNum(num(m, 'ma200'))],
    ['RSI14', fmtNum(num(m, 'rsi14'), 1)],
    ['Vol ×', fmtNum(num(m, 'vol_ratio'), 2)],
    ['Death cross', m.death_cross === true ? 'да' : m.death_cross === false ? 'нет' : '—'],
    ['Пробой саппорта', m.broke_support === true ? 'да' : m.broke_support === false ? 'нет' : '—'],
    ['RS-перфоманс', r != null ? `${(r * 100).toFixed(1)}%` : '—'],
    ['Оборот/день', adv != null ? `$${(adv / 1e6).toFixed(1)}M` : '—'],
  ];
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
        gap: 6,
        marginTop: 10,
      }}
    >
      {cells.map(([k, v]) => (
        <div key={k} className="stat">
          <div className="k">{k}</div>
          <div className="v">{v}</div>
        </div>
      ))}
    </div>
  );
}

function Drilldown({ c }: { c: ScreenerCandidate }) {
  const note = oversoldNote(c);
  const sNote = sectorNote(c);
  return (
    <div style={{ padding: '4px 2px 8px' }}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
          gap: 10,
        }}
      >
        {COMPONENTS.map((comp) => (
          <Gauge
            key={comp.key}
            label={`${comp.label} · ${Math.round(comp.weight * 100)}%`}
            score={typeof c.components[comp.key] === 'number' ? c.components[comp.key] : null}
          />
        ))}
      </div>
      <MetricRow c={c} />
      {note ? (
        <div className="hint" style={{ marginTop: 8 }}>
          ⚠️ {note}
        </div>
      ) : null}
      {sNote ? (
        <div className="hint" style={{ marginTop: 8 }}>
          ⚠️ {sNote}
        </div>
      ) : null}
    </div>
  );
}

export default function ShortScreenerResults({ screener }: { screener: ScreenerResult }) {
  const [open, setOpen] = useState<string | null>(null);
  const [chartFor, setChartFor] = useState<ScreenerCandidate | null>(null);
  if (!screener.candidates.length) return <Empty>Нет кандидатов в этом прогоне.</Empty>;

  return (
    <>
      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th />
              <th style={{ textAlign: 'left' }}>Symbol</th>
              <th>Grade</th>
              <th>Score</th>
              <th>Entry</th>
              <th>Stop</th>
              <th>Target 2R</th>
              <th>RSI14</th>
              <th style={{ textAlign: 'left' }}>Сектор</th>
            </tr>
          </thead>
          <tbody>
            {screener.candidates.map((c) => {
              const isOpen = open === c.symbol;
              return (
                <Fragment key={c.symbol}>
                  <tr style={{ cursor: 'pointer' }} onClick={() => setOpen(isOpen ? null : c.symbol)}>
                    <td>{isOpen ? '▾' : '▸'}</td>
                    <td className="sym" style={{ textAlign: 'left' }}>
                      <button
                        type="button"
                        className="ticker-btn"
                        title={`Открыть график ${c.symbol}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setChartFor(c);
                        }}
                      >
                        {c.symbol}
                      </button>
                    </td>
                    <td>
                      <GradeBadge grade={c.grade} />
                    </td>
                    <td style={{ color: scoreColor(c.composite_score) }}>
                      {fmtScore(c.composite_score)}
                    </td>
                    <td>{fmtNum(c.entry)}</td>
                    <td>{fmtNum(c.stop)}</td>
                    <td>{fmtNum(c.target)}</td>
                    <td>{fmtNum(num(c.metrics, 'rsi14'), 1)}</td>
                    <td style={{ textAlign: 'left' }} className="muted">
                      {c.sector ?? '—'}
                      {c.sector_leadership ? (
                        <span style={{ color: c.sector_fight ? 'var(--red)' : undefined }}>
                          {' · '}
                          {c.sector_etf}
                          {c.sector_rs != null
                            ? ` ${c.sector_rs >= 0 ? '+' : ''}${c.sector_rs.toFixed(0)}%`
                            : ''}
                        </span>
                      ) : null}
                    </td>
                  </tr>
                  {isOpen ? (
                    <tr>
                      <td colSpan={9} style={{ background: 'rgba(127,127,127,0.06)' }}>
                        <Drilldown c={c} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {chartFor ? (
        <Suspense fallback={null}>
          <TickerChartModal
            ticker={chartFor.symbol.toUpperCase()}
            levels={chartLevels(chartFor)}
            hasAnalysis={false}
            onClose={() => setChartFor(null)}
          />
        </Suspense>
      ) : null}
    </>
  );
}
