import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { JobLogLine, JobStatus } from '@shared/types';
import { analyzeTicker, cancelJob } from '../api';
import { summarizeClaudeEvent } from '../lib/claudeEvents';

type RunState = 'idle' | 'running' | JobStatus;

export default function AnalyzeButton({ ticker }: { ticker: string }) {
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

  async function start() {
    setState('running');
    setLines([]);
    setError(null);
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

            <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
              <button onClick={() => setOpen(false)}>Close</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
