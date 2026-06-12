import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { JobLogLine, JobStatus, ReconcileChange, ReconcileResult } from '@shared/types';
import { analyzeTicker, applyReconcile, cancelJob, fetchReconcile } from '../api';
import { summarizeClaudeEvent } from '../lib/claudeEvents';
import { fmtNum } from '../lib/format';

type RunState = 'idle' | 'running' | JobStatus;

function changeInfo(change: ReconcileChange): { label: string; color: string } {
  switch (change) {
    case 'direction-flip':
      return { label: 'Direction flip', color: 'var(--red)' };
    case 'levels-updated':
      return { label: 'Levels updated', color: 'var(--yellow)' };
    case 'new':
      return { label: 'New candidate', color: 'var(--accent)' };
    case 'unchanged':
      return { label: 'No change', color: 'var(--muted)' };
    default:
      return { label: 'No analysis signal found', color: 'var(--muted)' };
  }
}

function ReconcileSection({
  ticker,
  date,
  reconcile,
  onApplied,
}: {
  ticker: string;
  date: string | null;
  reconcile: ReconcileResult;
  onApplied: () => void;
}) {
  const [applied, setApplied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const info = changeInfo(reconcile.change);
  const cur = reconcile.current;
  const prop = reconcile.proposed;
  const canApply =
    !applied && ['direction-flip', 'levels-updated', 'new'].includes(reconcile.change);

  async function apply() {
    setBusy(true);
    setError(null);
    try {
      const res = await applyReconcile(ticker, date);
      if (res.applied) {
        setApplied(true);
        onApplied();
      } else {
        setError('Nothing to apply.');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="field">
      <strong>
        Watchlist update <span className="badge" style={{ color: info.color }}>{info.label}</span>
        {applied ? <span style={{ color: 'var(--green)' }}> ✓ applied</span> : null}
      </strong>

      {prop ? (
        <table style={{ margin: '8px 0' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left' }}>Source</th>
              <th style={{ textAlign: 'left' }}>Side</th>
              <th>Entry/Pivot</th>
              <th>Stop</th>
              <th>Target</th>
              <th>Shares</th>
            </tr>
          </thead>
          <tbody>
            {cur ? (
              <tr>
                <td style={{ textAlign: 'left' }} className="muted">
                  screener
                </td>
                <td style={{ textAlign: 'left' }}>{cur.side}</td>
                <td>{fmtNum(cur.pivot)}</td>
                <td>{fmtNum(cur.stop)}</td>
                <td>{fmtNum(cur.target)}</td>
                <td>{cur.shares ?? '—'}</td>
              </tr>
            ) : null}
            <tr>
              <td style={{ textAlign: 'left', color: 'var(--accent)' }}>analysis</td>
              <td style={{ textAlign: 'left' }}>{prop.side}</td>
              <td>{fmtNum(prop.pivot)}</td>
              <td>{fmtNum(prop.stop)}</td>
              <td>{fmtNum(prop.target)}</td>
              <td>{prop.shares ?? '—'}</td>
            </tr>
          </tbody>
        </table>
      ) : (
        <div className="muted" style={{ margin: '8px 0' }}>
          No priority signal parsed from signals.md for {ticker}.
        </div>
      )}

      {error ? <div className="err" style={{ marginBottom: 6 }}>{error}</div> : null}
      {canApply ? (
        <button className="primary" disabled={busy} onClick={() => void apply()}>
          {busy ? 'Applying…' : 'Apply to watchlist'}
        </button>
      ) : !applied && reconcile.change === 'unchanged' ? (
        <div className="muted">Analysis matches the current watchlist candidate — nothing to apply.</div>
      ) : null}
    </div>
  );
}

export default function AnalyzeButton({ ticker, date }: { ticker: string; date: string | null }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [createAlerts, setCreateAlerts] = useState(false);
  const [saveToNotes, setSaveToNotes] = useState(false);
  const [state, setState] = useState<RunState>('idle');
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const [reconcile, setReconcile] = useState<ReconcileResult | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (state !== 'running') return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [state]);
  useEffect(() => () => esRef.current?.close(), []);
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [lines]);

  // Pull the latest analysis signal (from signals.md) and compare to the watchlist.
  // Works any time the signal exists — independent of a run this session.
  function loadReconcile() {
    fetchReconcile(ticker, date)
      .then(setReconcile)
      .catch(() => setReconcile(null));
  }

  async function start() {
    setState('running');
    setLines([]);
    setError(null);
    setReconcile(null);
    setStartedAt(Date.now());
    setNow(Date.now());
    try {
      const res = await analyzeTicker(ticker, { createAlerts, saveToNotes });
      if (!res.ok) {
        setState(res.busy ? 'busy' : 'error');
        setError(res.busy ? `another job is running (${res.activeJobId})` : res.error || 'failed');
        return;
      }
      const id = res.job!.id;
      setJobId(id);
      const es = new EventSource(`/api/actions/jobs/${id}/stream`);
      esRef.current = es;
      es.addEventListener('log', (e) => {
        const entry = JSON.parse((e as MessageEvent).data) as JobLogLine;
        const summary = summarizeClaudeEvent(entry);
        if (summary) setLines((prev) => [...prev, summary]);
      });
      es.addEventListener('end', (e) => {
        const d = JSON.parse((e as MessageEvent).data) as { status: JobStatus };
        setState(d.status);
        es.close();
        esRef.current = null;
        void qc.invalidateQueries({ queryKey: ['analysisIndex'] });
        void qc.invalidateQueries({ queryKey: ['tickerDates', ticker] });
        // Attempt reconcile on any terminal status — claude can write signals.md
        // even when it exits non-zero, so don't gate on 'done' only.
        if (d.status !== 'busy') loadReconcile();
      });
      es.onerror = () => es.close();
    } catch (e) {
      setState('error');
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function cancel() {
    if (jobId) await cancelJob(jobId).catch(() => undefined);
  }

  const elapsed = startedAt ? Math.round((now - startedAt) / 1000) : 0;
  const last = lines[lines.length - 1] ?? 'starting…';
  const running = state === 'running';

  return (
    <div className="analyze">
      {running ? (
        <div className="analyze-running">
          <span className="spin">⟳</span>
          <span className="step" title={last}>
            {last}
          </span>
          <span className="muted">{elapsed}s</span>
          <button className="link-btn" onClick={() => setOpen(true)}>
            log
          </button>
          <button className="link-btn danger" onClick={() => void cancel()}>
            ✕
          </button>
        </div>
      ) : (
        <div className="analyze-idle">
          <button className="mini" title={`Run ticker-analysis for ${ticker}`} onClick={() => setOpen(true)}>
            🔍 Analyze
          </button>
          {state === 'done' ? <span style={{ color: 'var(--green)' }}>✓</span> : null}
          {state === 'error' || state === 'busy' ? (
            <span className="err" title={error ?? ''}>
              {state}
            </span>
          ) : null}
          {lines.length > 0 ? (
            <button className="link-btn" onClick={() => setOpen(true)}>
              log
            </button>
          ) : null}
        </div>
      )}

      {open ? (
        <div className="modal-backdrop" onClick={() => setOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>
              Analyze {ticker}
              {running ? (
                <span style={{ color: 'var(--accent)' }}> — running ({elapsed}s)</span>
              ) : state !== 'idle' ? (
                <span style={{ color: state === 'done' ? 'var(--green)' : 'var(--orange)' }}> — {state}</span>
              ) : null}
            </h3>

            <div className="field">
              <strong>Options</strong>
              <div className="btn-row">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={createAlerts}
                    disabled={running}
                    onChange={(e) => setCreateAlerts(e.target.checked)}
                  />
                  Create TradingView alerts
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={saveToNotes}
                    disabled={running}
                    onChange={(e) => setSaveToNotes(e.target.checked)}
                  />
                  Save to Notes
                </label>
              </div>
              <span className="hint">
                Runs the <code>ticker-analysis</code> skill on Opus 4.8. Needs TradingView Desktop
                (CDP :9222). Alerts use <code>signals-alerts</code>; Notes uses <code>save-note</code>.
              </span>
            </div>

            <div className="btn-row" style={{ marginBottom: 12 }}>
              <button className="primary" disabled={running} onClick={() => void start()}>
                {state === 'idle' ? '▶ Run analysis' : running ? 'Running…' : '↻ Re-run'}
              </button>
              <button disabled={running} onClick={loadReconcile} title="Compare the latest signals.md signal for this ticker to the watchlist">
                ⟳ Check watchlist update
              </button>
              {running ? (
                <button className="danger" onClick={() => void cancel()}>
                  Cancel
                </button>
              ) : null}
            </div>

            {error ? <div className="err" style={{ marginBottom: 8 }}>{error}</div> : null}
            {lines.length > 0 || running ? (
              <pre className="joblog" ref={logRef}>
                {lines.length ? lines.join('\n') : '(no output yet)'}
              </pre>
            ) : null}

            {reconcile ? (
              <ReconcileSection
                ticker={ticker}
                date={date}
                reconcile={reconcile}
                onApplied={() => {
                  void qc.invalidateQueries({ queryKey: ['watchlist'] });
                  void qc.invalidateQueries({ queryKey: ['analysisIndex'] });
                }}
              />
            ) : null}

            <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
              <button onClick={() => setOpen(false)}>Close</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
