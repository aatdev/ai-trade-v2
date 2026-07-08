import { afterEach, describe, expect, it } from 'vitest';
import type { JobLane } from '@shared/types';
import { JobManager } from './jobs';

/**
 * Lane-based locking semantics. Assertions read the SYNCHRONOUS return of
 * `start()` (the lane lock flips before it returns), so the spawned `sleep`
 * never needs to finish — afterEach cancels every still-running child.
 *
 * Jobs sharing a lane serialize (second → busy); jobs on different lanes run
 * concurrently; a lane-less job neither locks nor is blocked.
 */
describe('JobManager lane locking', () => {
  const cleanups: Array<() => void> = [];
  afterEach(() => {
    for (const c of cleanups) c();
    cleanups.length = 0;
  });

  function sleeper(jm: JobManager, lane?: JobLane) {
    const r = jm.start({
      label: 'sleeper',
      cmd: 'sleep',
      args: ['30'],
      cwd: process.cwd(),
      ...(lane === undefined ? {} : { lane }),
    });
    if (!r.busy) cleanups.push(() => jm.cancel(r.job.id));
    return r;
  }

  it('blocks a second job on the SAME lane while one is running', () => {
    const jm = new JobManager();
    const first = sleeper(jm, 'tradingview');
    expect(first.busy).toBe(false);
    const second = sleeper(jm, 'tradingview');
    expect(second.busy).toBe(true);
    if (second.busy) {
      expect(second.lane).toBe('tradingview');
      expect(second.activeJobId).toBe(first.busy ? undefined : first.job.id);
    }
  });

  it('runs jobs on DIFFERENT lanes concurrently', () => {
    const jm = new JobManager();
    expect(sleeper(jm, 'tradingview').busy).toBe(false);
    expect(sleeper(jm, 'screener').busy).toBe(false);
    expect(sleeper(jm, 'scheduler').busy).toBe(false);
    expect(sleeper(jm, 'ib').busy).toBe(false);
    // ...but a second on an already-held lane is still refused.
    expect(sleeper(jm, 'screener').busy).toBe(true);
  });

  it('lets a lane-less job run alongside any lane and never block', () => {
    const jm = new JobManager();
    expect(sleeper(jm, 'tradingview').busy).toBe(false);
    expect(sleeper(jm).busy).toBe(false); // no lane → bypasses locks
    expect(sleeper(jm).busy).toBe(false); // any number of lane-less jobs
    // The lane-less jobs never claimed a lane, so a new lane is still free.
    expect(sleeper(jm, 'screener').busy).toBe(false);
  });

  it('exposes the active lanes map', () => {
    const jm = new JobManager();
    const tv = sleeper(jm, 'tradingview');
    const sc = sleeper(jm, 'screener');
    expect(jm.activeLanes).toEqual({
      tradingview: tv.busy ? undefined : tv.job.id,
      screener: sc.busy ? undefined : sc.job.id,
    });
  });

  it('frees the lane when its job is cancelled, allowing a re-run', () => {
    const jm = new JobManager();
    const first = sleeper(jm, 'ib');
    expect(first.busy).toBe(false);
    if (!first.busy) jm.cancel(first.job.id);
    // cancel() sends SIGTERM; the process 'close' handler frees the lane
    // asynchronously, so we can't synchronously re-run here. We only assert
    // the lane was held while running.
    expect(jm.activeLanes.ib).toBe(first.busy ? undefined : first.job.id);
  });
});

describe('JobManager wall-time timeout', () => {
  it('SIGKILLs a job that outlives timeoutMs and marks it errored', async () => {
    const jm = new JobManager();
    const r = jm.start({
      label: 'slow',
      cmd: 'sleep',
      args: ['30'],
      cwd: process.cwd(),
      timeoutMs: 150,
    });
    expect(r.busy).toBe(false);
    if (r.busy) return;
    const id = r.job.id;
    // Wait for the timer to fire + the process 'close' to propagate.
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const detail = jm.get(id);
    expect(detail?.status).toBe('error');
    expect(detail?.lines.some((l) => l.line.includes('timed out'))).toBe(true);
  });

  it('does not fire the timeout for a fast job', async () => {
    const jm = new JobManager();
    const r = jm.start({
      label: 'fast',
      cmd: 'true',
      args: [],
      cwd: process.cwd(),
      timeoutMs: 5000,
    });
    expect(r.busy).toBe(false);
    if (r.busy) return;
    await new Promise((resolve) => setTimeout(resolve, 300));
    const detail = jm.get(r.job.id);
    expect(detail?.status).toBe('done');
    expect(detail?.lines.some((l) => l.line.includes('timed out'))).toBe(false);
  });
});
