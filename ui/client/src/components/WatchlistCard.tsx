import { lazy, Suspense, useState } from 'react';
import type { AnalysisIndexEntry, WatchlistCandidate } from '@shared/types';
import { useAnalysisIndex, useWatchlist, type Refetch } from '../api';
import { fmtMoney, fmtNum, fmtScore } from '../lib/format';
import AnalyzeButton from './AnalyzeButton';
import type { ChartLevels } from './CandleChart';
import { AnalysisLink, Card, Collapsible, Empty, ErrorNote, Loading, SideBadge } from './ui';

// Code-split the charting library (lightweight-charts) — only loaded once a
// ticker is clicked, keeping the dashboard's initial bundle lean.
const TickerChartModal = lazy(() => import('./TickerChartModal'));

type Index = Record<string, AnalysisIndexEntry>;

function levelsFromCandidate(c: WatchlistCandidate): ChartLevels {
  return {
    side: c.side,
    entry: c.pivot ?? c.worst_entry ?? null,
    stop: c.stop,
    target: c.target,
    t1: c.t1 ?? null,
    t2: c.t2 ?? null,
    t3: c.t3 ?? null,
  };
}

function SourcePill({ c }: { c: WatchlistCandidate }) {
  const src = c.source ?? 'screener';
  const isAnalysis = src === 'analysis';
  const o = c.screener_origin;
  const title =
    isAnalysis && o
      ? `from analysis — screener was ${o.side} (pivot ${o.pivot ?? '—'} / stop ${o.stop ?? '—'} / target ${o.target ?? '—'})`
      : `source: ${src}`;
  return (
    <span className="pill" style={isAnalysis ? { color: 'var(--accent)' } : undefined} title={title}>
      {src}
    </span>
  );
}

function CandidateTable({
  rows,
  index,
  date,
  withAnalyze,
  onOpenChart,
}: {
  rows: WatchlistCandidate[];
  index: Index;
  date: string | null;
  withAnalyze: boolean;
  onOpenChart: (c: WatchlistCandidate) => void;
}) {
  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <th style={{ textAlign: 'left' }}>Side</th>
            <th style={{ textAlign: 'left' }}>Setup</th>
            <th>Pivot</th>
            <th>Worst</th>
            <th>Stop</th>
            <th>Target</th>
            <th>Shares</th>
            <th>Risk $</th>
            <th>Score</th>
            <th>Val</th>
            {withAnalyze ? <th style={{ textAlign: 'left' }}>Analysis</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => {
            const entry = index[c.ticker.toUpperCase()];
            return (
              <tr key={c.ticker}>
                <td className="sym">
                  <button
                    type="button"
                    className="ticker-btn"
                    title={`Open chart for ${c.ticker}`}
                    onClick={() => onOpenChart(c)}
                  >
                    {c.ticker}
                  </button>
                  {!withAnalyze ? <AnalysisLink ticker={c.ticker.toUpperCase()} entry={entry} /> : null}
                </td>
                <td style={{ textAlign: 'left' }}>
                  <SideBadge side={c.side} />
                </td>
                <td style={{ textAlign: 'left' }} className="muted">
                  {c.setup ?? '—'} <SourcePill c={c} />
                </td>
                <td>{fmtNum(c.pivot)}</td>
                <td>{fmtNum(c.worst_entry)}</td>
                <td>{fmtNum(c.stop)}</td>
                <td>{fmtNum(c.target)}</td>
                <td>{c.shares ?? '—'}</td>
                <td>{fmtMoney(c.risk_dollars)}</td>
                <td>{fmtScore(c.score)}</td>
                <td title={c.validation_note ?? undefined}>
                  {c.validated === true ? (
                    <span style={{ color: 'var(--green)' }}>✓</span>
                  ) : c.validated === false ? (
                    <span style={{ color: 'var(--red)' }}>✗</span>
                  ) : (
                    <span className="muted">·</span>
                  )}
                </td>
                {withAnalyze ? (
                  <td style={{ textAlign: 'left' }}>
                    <div className="analyze-cell">
                      <AnalyzeButton ticker={c.ticker.toUpperCase()} date={date} />
                      <AnalysisLink ticker={c.ticker.toUpperCase()} entry={entry} />
                    </div>
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function WatchlistCard({ date, refetch }: { date: string | null; refetch: Refetch }) {
  const { data, isLoading, error } = useWatchlist(date, refetch);
  const { data: analysisIndex } = useAnalysisIndex(refetch);
  const index: Index = analysisIndex?.tickers ?? {};
  const [chartFor, setChartFor] = useState<WatchlistCandidate | null>(null);

  if (isLoading)
    return (
      <Card title="Watchlist">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Watchlist">
        <ErrorNote error={error} />
      </Card>
    );
  const wl = data?.data;
  if (!wl)
    return (
      <Card title="Watchlist">
        <Empty />
      </Card>
    );

  return (
    <Card title="Watchlist" source={data?.source}>
      {wl.notes ? (
        <div className="muted" style={{ marginBottom: 10, fontSize: 13 }}>
          {wl.notes}
        </div>
      ) : null}
      {wl.candidates.length === 0 ? (
        <Empty>No candidates.</Empty>
      ) : (
        <CandidateTable rows={wl.candidates} index={index} date={date} withAnalyze onOpenChart={setChartFor} />
      )}
      {wl.rejected_by_validation.length > 0 ? (
        <Collapsible label="Rejected by chart validation" count={wl.rejected_by_validation.length}>
          <CandidateTable
            rows={wl.rejected_by_validation}
            index={index}
            date={date}
            withAnalyze={false}
            onOpenChart={setChartFor}
          />
        </Collapsible>
      ) : null}

      {chartFor ? (
        <Suspense fallback={null}>
          <TickerChartModal
            ticker={chartFor.ticker.toUpperCase()}
            levels={levelsFromCandidate(chartFor)}
            hasAnalysis={!!index[chartFor.ticker.toUpperCase()]}
            onClose={() => setChartFor(null)}
          />
        </Suspense>
      ) : null}
    </Card>
  );
}
