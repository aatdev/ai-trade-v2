import { useEffect, useRef, useState } from 'react';
import type { JobLogLine, JobStatus, StartJobResponse } from '@shared/types';
import { cancelJob } from '../api';

export type RunState = 'idle' | 'running' | JobStatus;

export interface JobStream {
  state: RunState;
  lines: string[];
  error: string | null;
  elapsed: number; // seconds since start
  jobId: string | null;
  /** Launch a job (the `starter` POSTs and returns the StartJobResponse) and stream it. */
  run: (starter: () => Promise<StartJobResponse>) => Promise<void>;
  cancel: () => Promise<void>;
  reset: () => void;
}

export interface JobStreamOptions {
  /** Fired when the job reaches a terminal state (done / error / busy). */
  onEnd?: (status: JobStatus) => void;
  /** Map a raw log line to a display string (return null to drop it). Default: the plain line. */
  formatLine?: (entry: JobLogLine) => string | null;
}

const defaultFormat = (entry: JobLogLine): string | null => entry.line;

/**
 * Generic single-job driver: launch via a `starter` thunk, stream the live log
 * over SSE, track status/elapsed, and notify `onEnd` on any terminal status.
 * The reconcile-specific machinery stays in useAnalyzeRun; this is the plain
 * variant the Screener tab uses (screener/planner/save jobs emit plain text).
 */
export function useJobStream(opts: JobStreamOptions = {}): JobStream {
  const [state, setState] = useState<RunState>('idle');
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const esRef = useRef<EventSource | null>(null);
  const optsRef = useRef(opts);
  optsRef.current = opts;

  useEffect(() => {
    if (state !== 'running') return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [state]);
  useEffect(() => () => esRef.current?.close(), []);

  async function run(starter: () => Promise<StartJobResponse>) {
    setState('running');
    setLines([]);
    setError(null);
    setStartedAt(Date.now());
    setNow(Date.now());
    try {
      const res = await starter();
      if (!res.ok) {
        setState(res.busy ? 'busy' : 'error');
        setError(
          res.busy
            ? `другая задача уже выполняется (${res.activeJobId ?? '?'})`
            : res.error || 'не удалось запустить задачу',
        );
        return;
      }
      const id = res.job!.id;
      setJobId(id);
      const es = new EventSource(`/api/actions/jobs/${id}/stream`);
      esRef.current = es;
      const fmt = optsRef.current.formatLine ?? defaultFormat;
      es.addEventListener('log', (e) => {
        const entry = JSON.parse((e as MessageEvent).data) as JobLogLine;
        const text = fmt(entry);
        if (text != null) setLines((prev) => [...prev, text]);
      });
      es.addEventListener('end', (e) => {
        const d = JSON.parse((e as MessageEvent).data) as { status: JobStatus };
        setState(d.status);
        es.close();
        esRef.current = null;
        optsRef.current.onEnd?.(d.status);
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
    setStartedAt(null);
  }

  const elapsed = startedAt ? Math.round((now - startedAt) / 1000) : 0;
  return { state, lines, error, elapsed, jobId, run, cancel, reset };
}
