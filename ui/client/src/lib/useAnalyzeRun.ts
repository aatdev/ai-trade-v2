import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { JobLogLine, JobStatus, ReconcileResult } from '@shared/types';
import { analyzeTicker, cancelJob, fetchReconcile } from '../api';
import { summarizeClaudeEvent } from './claudeEvents';

export type RunState = 'idle' | 'running' | JobStatus;

export interface AnalyzeRun {
  state: RunState;
  lines: string[];
  error: string | null;
  elapsed: number;
  jobId: string | null;
  reconcile: ReconcileResult | null;
  start: (ticker: string, opts: { createAlerts: boolean; saveToNotes: boolean }) => Promise<void>;
  cancel: () => Promise<void>;
  loadReconcile: (ticker: string) => void;
  reset: () => void;
}

/**
 * Drives a single ticker-analysis job: launch, stream the live log, track status,
 * and pull the watchlist reconcile once the run reaches a terminal state.
 * The ticker is supplied per call to `start`, so one hook instance can serve a
 * fixed-ticker button or a free-form "Analyze <X>" dialog.
 */
export function useAnalyzeRun(date: string | null): AnalyzeRun {
  const qc = useQueryClient();
  const [state, setState] = useState<RunState>('idle');
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const [reconcile, setReconcile] = useState<ReconcileResult | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (state !== 'running') return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [state]);
  useEffect(() => () => esRef.current?.close(), []);

  function loadReconcile(ticker: string) {
    fetchReconcile(ticker, date)
      .then(setReconcile)
      .catch(() => setReconcile(null));
  }

  async function start(ticker: string, opts: { createAlerts: boolean; saveToNotes: boolean }) {
    setState('running');
    setLines([]);
    setError(null);
    setReconcile(null);
    setStartedAt(Date.now());
    setNow(Date.now());
    try {
      const res = await analyzeTicker(ticker, opts);
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
        // The run writes the signal into signals.md (Step 5) and its post-run
        // reconcile can rewrite the watchlist candidate — refresh both feeds so
        // the new signal/levels show without a manual reload. Without this the
        // Signals Feed keeps a stale cache (refetchOnWindowFocus is off).
        void qc.invalidateQueries({ queryKey: ['signals'] });
        void qc.invalidateQueries({ queryKey: ['watchlist'] });
        // Attempt reconcile on any terminal status — claude can write signals.md
        // even when it exits non-zero, so don't gate on 'done' only.
        if (d.status !== 'busy') loadReconcile(ticker);
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

  function reset() {
    esRef.current?.close();
    esRef.current = null;
    setState('idle');
    setLines([]);
    setError(null);
    setJobId(null);
    setReconcile(null);
    setStartedAt(null);
  }

  const elapsed = startedAt ? Math.round((now - startedAt) / 1000) : 0;
  return { state, lines, error, elapsed, jobId, reconcile, start, cancel, loadReconcile, reset };
}
