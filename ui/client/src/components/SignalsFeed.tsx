import { lazy, Suspense, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Link } from 'react-router-dom';
import type { Side, SignalBlock } from '@shared/types';
import { deleteSignal, useAnalysisIndex, useSignals, type Refetch } from '../api';
import { useMemoryOp } from '../lib/useMemoryOp';
import AnalysisModal from './AnalysisModal';
import { Card, Empty, ErrorNote, Loading, Modal } from './ui';

// Code-split the charting library (lightweight-charts) — only loaded once a
// ticker is clicked, keeping the signals feed's initial bundle lean.
const TickerChartModal = lazy(() => import('./TickerChartModal'));

const REPORT_LINK_RE = /\.?\/?([A-Za-z0-9.\-]+)\/(\d{4}-\d{2}-\d{2})\//;

/** A signal carries armable levels only when it's a BUY/SELL (not 🟡 HOLD) —
 * the ingest adapter skips HOLD, so the "→ thesis" button is hidden for them. */
function isActionable(status: string | null): boolean {
  const s = (status || '').toUpperCase();
  return /\b(BUY|LONG|SELL|SHORT)\b/.test(s) || /🟢|🔴/.test(status || '');
}

function MdLink({ href, children }: { href?: string; children?: React.ReactNode }) {
  const m = href?.match(REPORT_LINK_RE);
  if (m) return <Link to={`/ticker/${m[1]}/${m[2]}`}>{children}</Link>;
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

function signalColor(status: string | null): string {
  const s = (status || '').toUpperCase();
  if (/\b(SHORT|SELL)\b/.test(s)) return 'var(--red)';
  if (/\b(BUY|LONG)\b/.test(s)) return 'var(--green)';
  return 'var(--muted)';
}

/** Derive the chart's side badge from the signal status (no parsed levels on a
 * SignalBlock — only status + raw markdown), so the chart at least labels the
 * intended direction. */
function sideFromStatus(status: string | null): Side | undefined {
  const s = (status || '').toUpperCase();
  if (/\b(SHORT|SELL)\b/.test(s) || /🔴/.test(status || '')) return 'short';
  if (/\b(BUY|LONG)\b/.test(s) || /🟢/.test(status || '')) return 'long';
  return undefined;
}

export default function SignalsFeed({ refetch }: { refetch: Refetch }) {
  const { data, isLoading, error } = useSignals(refetch);
  const { data: analysisIndex } = useAnalysisIndex(refetch);
  const index = analysisIndex?.tickers ?? {};
  const qc = useQueryClient();
  const [filter, setFilter] = useState<string>('');
  const [active, setActive] = useState<SignalBlock | null>(null);
  const [chartFor, setChartFor] = useState<SignalBlock | null>(null);
  const [analysisFor, setAnalysisFor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [delError, setDelError] = useState<string | null>(null);
  const toThesis = useMemoryOp();
  const [ingestTicker, setIngestTicker] = useState<string | null>(null);
  const ingesting = toThesis.state === 'running';

  function onToThesis(s: SignalBlock) {
    setIngestTicker(s.ticker);
    void toThesis.run({ op: 'ingest', source: 'ticker-analysis', ticker: s.ticker });
  }

  const signals = data?.signals ?? [];
  const tickers = useMemo(
    () => Array.from(new Set(signals.map((s) => s.ticker))).sort(),
    [signals],
  );
  const rows = filter ? signals.filter((s) => s.ticker === filter) : signals;

  async function onDelete(s: SignalBlock) {
    if (!window.confirm(`Delete signal ${s.ticker} (${s.date}) from signals.md?`)) return;
    setBusy(true);
    setDelError(null);
    try {
      await deleteSignal(s.ticker, s.date);
      await qc.invalidateQueries({ queryKey: ['signals'] });
      setActive((cur) => (cur?.id === s.id ? null : cur));
    } catch (e) {
      setDelError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (isLoading)
    return (
      <Card title="Signals Feed" className="full">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Signals Feed" className="full">
        <ErrorNote error={error} />
      </Card>
    );
  if (!data?.present || data.content.trim() === '')
    return (
      <Card title="Signals Feed" className="full">
        <Empty>No signals.md journal yet.</Empty>
      </Card>
    );

  // Fallback: journal present but unparsable into blocks — render raw markdown.
  if (signals.length === 0)
    return (
      <Card title="Signals Feed" className="full">
        <div className="md feed">
          <Markdown remarkPlugins={[remarkGfm]} components={{ a: MdLink }}>
            {data.content}
          </Markdown>
        </div>
      </Card>
    );

  return (
    <Card title={`Signals Feed (${rows.length})`} className="full">
      <div className="control" style={{ marginBottom: 10 }}>
        Ticker
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">all ({signals.length})</option>
          {tickers.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        {filter ? (
          <button className="link-btn" onClick={() => setFilter('')}>
            clear
          </button>
        ) : null}
      </div>
      {delError ? <div className="err" style={{ marginBottom: 8 }}>{delError}</div> : null}
      {ingesting ? (
        <div className="muted" style={{ marginBottom: 8 }}>создаю тезис {ingestTicker}…</div>
      ) : null}
      {toThesis.state === 'done' ? (
        <div className="muted" style={{ marginBottom: 8 }}>✓ тезис {ingestTicker} создан/обновлён</div>
      ) : null}
      {toThesis.error ? <div className="err" style={{ marginBottom: 8 }}>{toThesis.error}</div> : null}

      <div>
        <table className="rows-clickable">
          <thead>
            <tr>
              <th>Date</th>
              <th>Ticker</th>
              <th style={{ textAlign: 'left' }}>Signal</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} onClick={() => setActive(s)}>
                <td className="muted">{s.date}</td>
                <td className="sym">
                  <button
                    type="button"
                    className="ticker-btn"
                    title={`Открыть график ${s.ticker}`}
                    onClick={(e) => (e.stopPropagation(), setChartFor(s))}
                  >
                    {s.ticker}
                  </button>
                </td>
                <td style={{ textAlign: 'left', color: signalColor(s.status) }} title={s.status ?? ''}>
                  <span className="signal-status">{s.status ?? s.heading}</span>
                </td>
                <td>
                  <button className="link-btn" onClick={(e) => (e.stopPropagation(), setActive(s))}>
                    view
                  </button>
                  {index[s.ticker.toUpperCase()] ? (
                    <button
                      className="link-btn"
                      title={`Открыть результаты анализа ${s.ticker}`}
                      onClick={(e) => (e.stopPropagation(), setAnalysisFor(s.ticker.toUpperCase()))}
                    >
                      📄 анализ
                    </button>
                  ) : null}
                  {isActionable(s.status) ? (
                    <button
                      className="link-btn"
                      disabled={ingesting}
                      title="Создать IDEA-тезис из последнего сигнала"
                      onClick={(e) => (e.stopPropagation(), onToThesis(s))}
                    >
                      → тезис
                    </button>
                  ) : null}
                  <button
                    className="link-btn danger"
                    disabled={busy}
                    onClick={(e) => (e.stopPropagation(), void onDelete(s))}
                  >
                    🗑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {active ? (
        <Modal
          title={`${active.ticker} — ${active.date}`}
          onClose={() => setActive(null)}
          footer={
            <>
              {isActionable(active.status) ? (
                <button
                  disabled={ingesting}
                  title="Создать IDEA-тезис из этого сигнала"
                  onClick={() => onToThesis(active)}
                >
                  → тезис
                </button>
              ) : null}
              <button className="danger" disabled={busy} onClick={() => void onDelete(active)}>
                🗑 Delete signal
              </button>
              <button onClick={() => setActive(null)}>Close</button>
            </>
          }
        >
          <div className="md">
            <Markdown remarkPlugins={[remarkGfm]} components={{ a: MdLink }}>
              {active.markdown}
            </Markdown>
          </div>
        </Modal>
      ) : null}

      {chartFor ? (
        <Suspense fallback={null}>
          <TickerChartModal
            ticker={chartFor.ticker.toUpperCase()}
            levels={{ side: sideFromStatus(chartFor.status) }}
            hasAnalysis={!!index[chartFor.ticker.toUpperCase()]}
            onClose={() => setChartFor(null)}
            onOpenAnalysis={() => {
              const t = chartFor.ticker.toUpperCase();
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
