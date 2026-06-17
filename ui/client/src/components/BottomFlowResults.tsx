import { Fragment, Suspense, lazy, useState } from 'react';
import type { BottomFlowCandidate, BottomFlowResult } from '@shared/types';
import { fmtNum, fmtPct, fmtScore, fmtSignedPct } from '../lib/format';
import { scoreColor } from '../lib/zones';
import { Empty } from './ui';

// Code-split the charting lib — loads only once a ticker is clicked.
const TickerChartModal = lazy(() => import('./TickerChartModal'));

const GRADE_ORDER: { grade: string; label: string }[] = [
  { grade: 'A', label: 'A — дно + двойная дивергенция (фундамент И накопление)' },
  { grade: 'B-accum', label: 'B-accum — дно + только накопление (контрарианский, спекулятивный)' },
  { grade: 'B-fund', label: 'B-fund — дно + только фундамент (тейп ещё не развернулся)' },
];

const RISK_LABEL: Record<string, string> = {
  unprofitable: 'убыток',
  fcf_negative: 'FCF<0',
  low_altman_z: 'низкий Altman Z',
};
const FLOW_LABEL: Record<string, string> = {
  recovering: 'восстанавливается (QoQ↑)',
  resilient: 'устойчивый (TTM высокий)',
};

/** $-amount → compact B / M string (mirrors the screener's report formatter). */
function money(v: number | null): string {
  if (v == null) return '—';
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
  return v.toFixed(0);
}

function Tags({ c }: { c: BottomFlowCandidate }) {
  return (
    <span style={{ display: 'inline-flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
      <span style={{ color: c.turning ? 'var(--green)' : 'var(--muted)' }}>
        {c.turning ? '▲ разворот' : '▽ падает'}
      </span>
      {c.flow_profile.map((f) => (
        <span key={f} className="muted" title={FLOW_LABEL[f] ?? f}>
          {FLOW_LABEL[f] ?? f}
        </span>
      ))}
      {c.organic_warn ? (
        <span style={{ color: 'var(--amber, #c80)' }} title="Рост может быть неорганическим (M&A) — проверь вручную">
          ⚠ M&A?
        </span>
      ) : null}
      {c.risk_flags.map((f) => (
        <span key={f} style={{ color: 'var(--red)' }} title="Флаг риска выживаемости">
          {RISK_LABEL[f] ?? f}
        </span>
      ))}
    </span>
  );
}

function Drilldown({ c }: { c: BottomFlowCandidate }) {
  const cells: [string, string][] = [
    ['Опер. поток', money(c.ocf)],
    ['FCF', money(c.fcf)],
    ['FCF маржа', fmtPct(c.fcf_margin, 0)],
    ['Валовая маржа', fmtPct(c.gross_margin, 0)],
    ['Опер. маржа', fmtPct(c.oper_margin, 0)],
    ['Чистая приб.', money(c.net_income)],
    ['Altman Z', fmtNum(c.altman_z, 1)],
    ['Curr. ratio', fmtNum(c.current_ratio, 1)],
    ['Кап.', money(c.mkt_cap)],
    ['Оборот/день', c.avg_vol != null ? `${(c.avg_vol / 1e6).toFixed(1)}M` : '—'],
    ['RSI', fmtNum(c.rsi, 0)],
    ['Perf 6м', fmtSignedPct(c.perf_6m, 0)],
  ];
  return (
    <div style={{ padding: '4px 2px 8px' }}>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 8 }}>
        <span style={{ color: c.fundamental_ok ? 'var(--green)' : 'var(--muted)' }}>
          {c.fundamental_ok ? '✓' : '·'} фундамент-поток
        </span>
        <span style={{ color: c.accumulation_ok ? 'var(--green)' : 'var(--muted)' }}>
          {c.accumulation_ok ? '✓' : '·'} накопление
        </span>
        <span style={{ color: c.survivable ? 'var(--green)' : 'var(--red)' }}>
          {c.survivable ? '✓ выживаемый' : '✕ риск выживаемости'}
        </span>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
          gap: 6,
        }}
      >
        {cells.map(([k, v]) => (
          <div key={k} className="stat">
            <div className="k">{k}</div>
            <div className="v">{v}</div>
          </div>
        ))}
      </div>
      {c.organic_warn ? (
        <div className="hint" style={{ marginTop: 8 }}>
          ⚠️ Очень высокий рост — вероятно поглощение (M&A), а не органика. Проверь последний отчёт
          перед тем как доверять дивергенции.
        </div>
      ) : null}
    </div>
  );
}

function GradeTable({
  candidates,
  onChart,
}: {
  candidates: BottomFlowCandidate[];
  onChart: (c: BottomFlowCandidate) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th />
            <th style={{ textAlign: 'left' }}>Symbol</th>
            <th>Score</th>
            <th>% над дном</th>
            <th>% ниже хая</th>
            <th>Perf год</th>
            <th>Perf 3м</th>
            <th>Выр. TTM</th>
            <th>Выр. QoQ</th>
            <th>CMF</th>
            <th>MFI</th>
            <th style={{ textAlign: 'left' }}>Теги</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c) => {
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
                        onChart(c);
                      }}
                    >
                      {c.symbol}
                    </button>
                  </td>
                  <td style={{ color: scoreColor(c.score) }}>{fmtScore(c.score)}</td>
                  <td>{fmtPct(c.pct_off_low, 0)}</td>
                  <td>{fmtPct(c.pct_off_high, 0)}</td>
                  <td>{fmtSignedPct(c.perf_y, 0)}</td>
                  <td>{fmtSignedPct(c.perf_3m, 0)}</td>
                  <td>{fmtSignedPct(c.rev_ttm, 0)}</td>
                  <td>{fmtSignedPct(c.rev_qoq, 0)}</td>
                  <td>{fmtNum(c.cmf, 2)}</td>
                  <td>{fmtNum(c.mfi, 0)}</td>
                  <td style={{ textAlign: 'left' }}>
                    <Tags c={c} />
                  </td>
                </tr>
                {isOpen ? (
                  <tr>
                    <td colSpan={12} style={{ background: 'rgba(127,127,127,0.06)' }}>
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
  );
}

export default function BottomFlowResults({ screener }: { screener: BottomFlowResult }) {
  const [chartFor, setChartFor] = useState<BottomFlowCandidate | null>(null);
  if (!screener.candidates.length) return <Empty>Нет кандидатов в этом прогоне.</Empty>;

  const groups = GRADE_ORDER.map((g) => ({
    ...g,
    rows: screener.candidates.filter((c) => c.grade === g.grade),
  })).filter((g) => g.rows.length > 0);

  return (
    <>
      <div style={{ display: 'grid', gap: 18 }}>
        {groups.map((g) => (
          <div key={g.grade}>
            <div className="screener-section-label">
              {g.label} · {g.rows.length}
            </div>
            <GradeTable candidates={g.rows} onChart={setChartFor} />
          </div>
        ))}
      </div>

      {chartFor ? (
        <Suspense fallback={null}>
          <TickerChartModal
            ticker={chartFor.symbol.toUpperCase()}
            levels={{ side: 'long' }}
            hasAnalysis={false}
            onClose={() => setChartFor(null)}
          />
        </Suspense>
      ) : null}
    </>
  );
}
