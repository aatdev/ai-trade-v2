import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { JobLogLine, JobStatus, StartJobResponse } from '@shared/types';
import { memoryOp } from '../api';

type Submit = (body: Record<string, unknown>) => Promise<StartJobResponse>;

export type OpState = 'idle' | 'running' | JobStatus;

export interface MemoryOpRun {
  state: OpState;
  lines: JobLogLine[];
  error: string | null;
  run: (body: Record<string, unknown>) => Promise<void>;
  reset: () => void;
}

/**
 * Runs a single trader-memory CLI operation and streams its output. On any
 * terminal status it invalidates the memory/theses queries so the UI refreshes,
 * then calls `onDone` (e.g. to close a modal after a delete).
 *
 * `submit` defaults to the `/api/actions/memory` endpoint; pass a different
 * submitter (e.g. bulk delete-theses) to reuse the same stream + refresh logic.
 */
export function useMemoryOp(
  onDone?: (status: JobStatus) => void,
  submit: Submit = memoryOp,
): MemoryOpRun {
  const qc = useQueryClient();
  const [state, setState] = useState<OpState>('idle');
  const [lines, setLines] = useState<JobLogLine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  useEffect(() => () => esRef.current?.close(), []);

  async function run(body: Record<string, unknown>) {
    setState('running');
    setLines([]);
    setError(null);
    try {
      const res = await submit(body);
      if (!res.ok) {
        setState(res.busy ? 'busy' : 'error');
        setError(res.busy ? `another job is running (${res.activeJobId})` : res.error || 'failed');
        return;
      }
      const es = new EventSource(`/api/actions/jobs/${res.job!.id}/stream`);
      esRef.current = es;
      es.addEventListener('log', (e) => {
        const line = JSON.parse((e as MessageEvent).data) as JobLogLine;
        setLines((prev) => [...prev, line]);
      });
      es.addEventListener('end', (e) => {
        const d = JSON.parse((e as MessageEvent).data) as { status: JobStatus };
        setState(d.status);
        es.close();
        esRef.current = null;
        void qc.invalidateQueries({ queryKey: ['memory'] });
        void qc.invalidateQueries({ queryKey: ['theses'] });
        onDone?.(d.status);
      });
      es.onerror = () => es.close();
    } catch (e) {
      setState('error');
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function reset() {
    esRef.current?.close();
    esRef.current = null;
    setState('idle');
    setLines([]);
    setError(null);
  }

  return { state, lines, error, run, reset };
}
