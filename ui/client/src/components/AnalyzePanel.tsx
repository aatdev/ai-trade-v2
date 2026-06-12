import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { ReconcileChange, ReconcileResult } from '@shared/types';
import { applyReconcile } from '../api';
import { fmtNum } from '../lib/format';
import type { AnalyzeRun } from '../lib/useAnalyzeRun';

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

/**
 * Shared body of the analyze dialog (options → run controls → live log → reconcile).
 * `run` is a shared {@link AnalyzeRun} engine owned by the caller so the running
 * indicator survives the modal being closed.
 */
export default function AnalyzePanel({
  ticker,
  date,
  run,
  canRun = true,
}: {
  ticker: string;
  date: string | null;
  run: AnalyzeRun;
  canRun?: boolean;
}) {
  const qc = useQueryClient();
  const [createAlerts, setCreateAlerts] = useState(false);
  const [saveToNotes, setSaveToNotes] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);
  const running = run.state === 'running';

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [run.lines]);

  return (
    <>
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
        <button
          className="primary"
          disabled={running || !canRun}
          onClick={() => void run.start(ticker, { createAlerts, saveToNotes })}
        >
          {run.state === 'idle' ? '▶ Run analysis' : running ? 'Running…' : '↻ Re-run'}
        </button>
        <button
          disabled={running || !canRun}
          onClick={() => run.loadReconcile(ticker)}
          title="Compare the latest signals.md signal for this ticker to the watchlist"
        >
          ⟳ Check watchlist update
        </button>
        {running ? (
          <button className="danger" onClick={() => void run.cancel()}>
            Cancel
          </button>
        ) : null}
      </div>

      {run.error ? <div className="err" style={{ marginBottom: 8 }}>{run.error}</div> : null}
      {run.lines.length > 0 || running ? (
        <pre className="joblog" ref={logRef}>
          {run.lines.length ? run.lines.join('\n') : '(no output yet)'}
        </pre>
      ) : null}

      {run.reconcile ? (
        <ReconcileSection
          ticker={ticker}
          date={date}
          reconcile={run.reconcile}
          onApplied={() => {
            void qc.invalidateQueries({ queryKey: ['watchlist'] });
            void qc.invalidateQueries({ queryKey: ['analysisIndex'] });
          }}
        />
      ) : null}
    </>
  );
}
