import { useEffect, useRef, useState } from 'react';
import type { JobLogLine, JobStatus } from '@shared/types';

export interface JobLogView {
  /** Buffered + live log lines for the attached job. */
  lines: JobLogLine[];
  /** Terminal status once the job ends (null while still running / attaching). */
  status: JobStatus | null;
}

/**
 * Attach to an EXISTING job by id and stream its log over SSE. Unlike
 * `useJobStream` (which LAUNCHES a job via a starter thunk), this only observes
 * a job already in the registry — the Jobs tab uses it to view any job's log.
 *
 * The server replays the buffered lines then streams live; for a finished job
 * it replays everything and immediately sends `end` — so this works for both
 * running and completed jobs (`routes/actions.ts` GET /actions/jobs/:id/stream).
 * Pass `null` to detach (closes the stream).
 */
export function useJobLog(jobId: string | null): JobLogView {
  const [lines, setLines] = useState<JobLogLine[]>([]);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    setLines([]);
    setStatus(null);
    if (!jobId) return;

    const es = new EventSource(`/api/actions/jobs/${jobId}/stream`);
    esRef.current = es;
    es.addEventListener('log', (e) => {
      const entry = JSON.parse((e as MessageEvent).data) as JobLogLine;
      setLines((prev) => [...prev, entry]);
    });
    es.addEventListener('end', (e) => {
      const d = JSON.parse((e as MessageEvent).data) as { status: JobStatus };
      setStatus(d.status);
      es.close();
      esRef.current = null;
    });
    es.onerror = () => es.close();

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [jobId]);

  return { lines, status };
}
