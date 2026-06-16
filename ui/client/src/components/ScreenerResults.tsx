import { Fragment, Suspense, lazy, useState } from 'react';
import type { StagedScreener, StagedScreenerCandidate } from '@shared/types';
import { fmtNum, fmtScore } from '../lib/format';
import { scoreColor } from '../lib/zones';
import type { ChartLevels } from './CandleChart';
import { Empty } from './ui';
import ScreenerChecklist from './ScreenerChecklist';
import ScreenerScoreBreakdown from './ScreenerScoreBreakdown';

// Code-split the charting lib — it only loads once a ticker is clicked (shared
// chunk with the watchlist / overview screener tables).
const TickerChartModal = lazy(() => import('./TickerChartModal'));

/** Entry/stop/target: prefer the joined plan order, else the screener pivot geometry. */
function levels(c: StagedScreenerCandidate) {
  const pp = c.components.pivot_proximity;
  return {
    entry: c.plan?.signal_entry ?? pp.pivot_price,
    stop: c.plan?.stop_loss_price ?? pp.stop_loss_price,
    target: c.plan?.target_price ?? null,
  };
}

/** VCP candidates are long-only; overlay entry/stop/target on the chart. */
function chartLevels(c: StagedScreenerCandidate): ChartLevels {
  return { side: 'long', ...levels(c) };
}

function checklistColor(c: StagedScreenerCandidate): string {
  const cl = c.checklist;
  if (cl.allPass) return 'var(--green)';
  if (cl.points.some((p) => p.state === 'fail')) return 'var(--red)';
  return 'var(--muted)';
}

export default function ScreenerResults({ screener }: { screener: StagedScreener }) {
  const [open, setOpen] = useState<string | null>(null);
  const [chartFor, setChartFor] = useState<StagedScreenerCandidate | null>(null);
  if (!screener.candidates.length) return <Empty>Нет кандидатов в этом прогоне.</Empty>;

  return (
    <>
      <div className="scroll-x">
        <table>
        <thead>
          <tr>
            <th />
            <th style={{ textAlign: 'left' }}>Symbol</th>
            <th>Score</th>
            <th style={{ textAlign: 'left' }}>Rating</th>
            <th>Entry</th>
            <th>Stop</th>
            <th>Target</th>
            <th style={{ textAlign: 'left' }}>Состояние</th>
            <th title="Пройдено пунктов чек-листа 5.3">5.3</th>
            <th style={{ textAlign: 'left' }}>Сектор</th>
          </tr>
        </thead>
        <tbody>
          {screener.candidates.map((c) => {
            const lv = levels(c);
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
                  <td style={{ color: scoreColor(c.composite_score) }}>{fmtScore(c.composite_score)}</td>
                  <td style={{ textAlign: 'left' }} className="muted">
                    {c.rating ?? '—'}
                  </td>
                  <td>{fmtNum(lv.entry)}</td>
                  <td>{fmtNum(lv.stop)}</td>
                  <td>{fmtNum(lv.target)}</td>
                  <td style={{ textAlign: 'left' }} className="muted">
                    {c.execution_state ?? '—'}
                  </td>
                  <td style={{ color: checklistColor(c) }}>
                    {c.checklist.knownPass}/{c.checklist.total}
                  </td>
                  <td style={{ textAlign: 'left' }} className="muted">
                    {c.sector ?? '—'}
                  </td>
                </tr>
                {isOpen ? (
                  <tr>
                    <td colSpan={10} style={{ background: 'rgba(127,127,127,0.06)' }}>
                      <ScreenerChecklist checklist={c.checklist} />
                      <ScreenerScoreBreakdown c={c} />
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
