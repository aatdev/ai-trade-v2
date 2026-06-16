import { lazy, Suspense, useState } from 'react';
import type { AnalysisIndexEntry, ScreenerCandidate, ScreenerResult, Sourced } from '@shared/types';
import { useAnalysisIndex, useScreeners, type Refetch } from '../api';
import { fmtNum, fmtScore } from '../lib/format';
import { useVersionedSource } from '../lib/useVersionedSource';
import { scoreColor } from '../lib/zones';
import AnalysisModal from './AnalysisModal';
import type { ChartLevels } from './CandleChart';
import SourceSelect from './SourceSelect';
import { AnalysisLink, Card, Empty, ErrorNote, GradeBadge, Loading } from './ui';

// Same code-split chunk as the watchlist — the charting library only loads once
// a symbol is clicked. (AnalysisModal shares react-markdown with the analyses
// tab, so it stays in the main chunk.)
const TickerChartModal = lazy(() => import('./TickerChartModal'));

type Index = Record<string, AnalysisIndexEntry>;

function num(metrics: Record<string, number | boolean | null>, key: string): number | null {
  const v = metrics[key];
  return typeof v === 'number' ? v : null;
}

/** Screener candidates carry side via the screener kind (swing-short ⇒ short). */
function screenerLevels(c: ScreenerCandidate, kind: string): ChartLevels {
  return {
    side: kind === 'swing-short' ? 'short' : 'long',
    entry: c.entry,
    stop: c.stop,
    target: c.target,
  };
}

function ScreenerTable({
  result,
  index,
  onOpenChart,
  onOpenAnalysis,
}: {
  result: ScreenerResult;
  index: Index;
  onOpenChart: (c: ScreenerCandidate) => void;
  onOpenAnalysis: (ticker: string) => void;
}) {
  if (result.candidates.length === 0) return <Empty>No candidates.</Empty>;
  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Grade</th>
            <th>Score</th>
            <th>Entry</th>
            <th>Stop</th>
            <th>Target</th>
            <th>RSI14</th>
            <th style={{ textAlign: 'left' }}>Sector</th>
          </tr>
        </thead>
        <tbody>
          {result.candidates.map((c) => (
            <tr key={c.symbol}>
              <td className="sym">
                <button
                  type="button"
                  className="ticker-btn"
                  title={`Open chart for ${c.symbol}`}
                  onClick={() => onOpenChart(c)}
                >
                  {c.symbol}
                </button>
                <AnalysisLink
                  ticker={c.symbol.toUpperCase()}
                  entry={index[c.symbol.toUpperCase()]}
                  compact
                  onOpen={onOpenAnalysis}
                />
              </td>
              <td>
                <GradeBadge grade={c.grade} />
              </td>
              <td style={{ color: scoreColor(c.composite_score) }}>{fmtScore(c.composite_score)}</td>
              <td>{fmtNum(c.entry)}</td>
              <td>{fmtNum(c.stop)}</td>
              <td>{fmtNum(c.target)}</td>
              <td>{fmtNum(num(c.metrics, 'rsi14'), 1)}</td>
              <td style={{ textAlign: 'left' }} className="muted">
                {c.sector ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ScreenersCard({ date, refetch }: { date: string | null; refetch: Refetch }) {
  const [vcpSource, setVcpSource] = useVersionedSource(date);
  const [swingSource, setSwingSource] = useVersionedSource(date);
  const { data, isLoading, error } = useScreeners(
    date,
    { vcp: vcpSource, swing: swingSource },
    refetch,
  );
  const { data: analysisIndex } = useAnalysisIndex(refetch);
  const index: Index = analysisIndex?.tickers ?? {};
  const [tab, setTab] = useState<'vcp' | 'swingShort'>('swingShort');
  const [chartFor, setChartFor] = useState<{ c: ScreenerCandidate; kind: string } | null>(null);
  const [analysisFor, setAnalysisFor] = useState<string | null>(null);

  if (isLoading)
    return (
      <Card title="Screeners">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Screeners">
        <ErrorNote error={error} />
      </Card>
    );

  const active = (tab === 'vcp' ? data?.vcp : data?.swingShort) as
    | Sourced<ScreenerResult>
    | undefined;
  const vcpN = data?.vcp.data?.candidates.length ?? 0;
  const shortN = data?.swingShort.data?.candidates.length ?? 0;

  // The version picker tracks the active tab's screener kind.
  const sourceSelect =
    tab === 'vcp' ? (
      <SourceSelect
        kind="vcp"
        value={vcpSource}
        latest={data?.vcp.source ?? null}
        onChange={setVcpSource}
        refetch={refetch}
      />
    ) : (
      <SourceSelect
        kind="swing-short"
        value={swingSource}
        latest={data?.swingShort.source ?? null}
        onChange={setSwingSource}
        refetch={refetch}
      />
    );

  return (
    <Card title="Screeners" sourceSelect={sourceSelect}>
      <div className="tabs">
        <button
          className={`tab ${tab === 'swingShort' ? 'active' : ''}`}
          onClick={() => setTab('swingShort')}
        >
          Swing-Short ({shortN})
        </button>
        <button className={`tab ${tab === 'vcp' ? 'active' : ''}`} onClick={() => setTab('vcp')}>
          VCP ({vcpN})
        </button>
      </div>
      {active?.data ? (
        <ScreenerTable
          result={active.data}
          index={index}
          onOpenChart={(c) => setChartFor({ c, kind: active.data!.kind })}
          onOpenAnalysis={setAnalysisFor}
        />
      ) : (
        <Empty>No screener run for this date.</Empty>
      )}

      {chartFor ? (
        <Suspense fallback={null}>
          <TickerChartModal
            ticker={chartFor.c.symbol.toUpperCase()}
            levels={screenerLevels(chartFor.c, chartFor.kind)}
            hasAnalysis={!!index[chartFor.c.symbol.toUpperCase()]}
            onClose={() => setChartFor(null)}
            onOpenAnalysis={() => {
              const t = chartFor.c.symbol.toUpperCase();
              setChartFor(null);
              setAnalysisFor(t);
            }}
          />
        </Suspense>
      ) : null}

      {analysisFor ? (
        <AnalysisModal symbol={analysisFor} onClose={() => setAnalysisFor(null)} />
      ) : null}
    </Card>
  );
}
