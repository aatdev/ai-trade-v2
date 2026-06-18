import { afterEach, describe, expect, it } from 'vitest';
import { JobManager } from './jobs';

/**
 * The single-job mutex semantics. Assertions read the SYNCHRONOUS return of
 * `start()` (the mutex flips before it returns), so the spawned `sleep` never
 * needs to finish — afterEach cancels every still-running child.
 */
describe('JobManager single-job mutex', () => {
  const cleanups: Array<() => void> = [];
  afterEach(() => {
    for (const c of cleanups) c();
    cleanups.length = 0;
  });

  function sleeper(jm: JobManager, exclusive?: boolean) {
    const r = jm.start({
      label: 'sleeper',
      cmd: 'sleep',
      args: ['30'],
      cwd: process.cwd(),
      ...(exclusive === undefined ? {} : { exclusive }),
    });
    if (!r.busy) cleanups.push(() => jm.cancel(r.job.id));
    return r;
  }

  it('blocks a second EXCLUSIVE job while one is running', () => {
    const jm = new JobManager();
    expect(sleeper(jm).busy).toBe(false); // default exclusive
    const second = sleeper(jm);
    expect(second.busy).toBe(true);
    if (second.busy) expect(second.activeJobId).toBe(jm.active);
  });

  it('lets a NON-EXCLUSIVE job run alongside an exclusive one', () => {
    const jm = new JobManager();
    expect(sleeper(jm, true).busy).toBe(false); // exclusive holds the mutex
    expect(sleeper(jm, false).busy).toBe(false); // bypasses it
    expect(sleeper(jm).busy).toBe(true); // another exclusive is still blocked
  });

  it('a running NON-EXCLUSIVE job never claims the mutex, so it blocks nothing', () => {
    const jm = new JobManager();
    expect(sleeper(jm, false).busy).toBe(false);
    expect(jm.active).toBeNull(); // mutex never taken
    expect(sleeper(jm, true).busy).toBe(false); // exclusive starts freely
  });
});
