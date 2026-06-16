import type { Response } from 'express';
import type { StartJobResponse } from '@shared/types';
import type { JobManager } from './jobs';

/** Resolve the Python interpreter for spawned skill scripts (override: PYTHON_BIN). */
export function resolvePythonBin(): string {
  return process.env.PYTHON_BIN || 'python3';
}

/**
 * Start a job through the shared JobManager and write the canonical response:
 * 409 + `busy` when another job holds the single-run mutex, else 200 + the new
 * job summary. Shared by the actions and screener routers.
 */
export function startAndRespond(
  res: Response,
  jobs: JobManager,
  opts: Parameters<JobManager['start']>[0],
): void {
  const result = jobs.start(opts);
  if (result.busy) {
    const body: StartJobResponse = { ok: false, busy: true, activeJobId: result.activeJobId };
    res.status(409).json(body);
    return;
  }
  const body: StartJobResponse = {
    ok: true,
    job: {
      id: result.job.id,
      label: result.job.label,
      status: result.job.status,
      startedAt: result.job.startedAt,
      endedAt: result.job.endedAt,
      exitCode: result.job.exitCode,
    },
  };
  res.json(body);
}
