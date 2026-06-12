import { spawn, type ChildProcess } from 'node:child_process';
import type { JobDetail, JobLogLine, JobStatus, JobSummary } from '@shared/types';

const MAX_LINES = 2000; // ring buffer cap per job
const MAX_JOBS = 25; // keep the most recent N jobs in memory
const SCHEDULER_BUSY_CODE = 75; // EXIT_BUSY from run_trading_schedule.py

export interface StartOptions {
  label: string;
  cmd: string;
  args: string[];
  cwd: string;
  /** Run through `bash -c <single string>` (only for fixed, input-free commands). */
  shell?: boolean;
  /** Extra env merged over process.env for this job. */
  env?: NodeJS.ProcessEnv;
  /** Arbitrary metadata surfaced in the jobs list (e.g. { kind, ticker }). */
  meta?: Record<string, unknown>;
}

interface InternalJob {
  id: string;
  label: string;
  cmd: string;
  args: string[];
  status: JobStatus;
  startedAt: number;
  endedAt: number | null;
  exitCode: number | null;
  lines: JobLogLine[];
  proc: ChildProcess | null;
  meta?: Record<string, unknown>;
}

type Subscriber = (line: JobLogLine) => void;
type EndSubscriber = (job: JobSummary) => void;

export class JobManager {
  private jobs = new Map<string, InternalJob>();
  private order: string[] = [];
  private counter = 0;
  private activeJobId: string | null = null;
  private subs = new Map<string, Set<Subscriber>>();
  private endSubs = new Map<string, Set<EndSubscriber>>();

  /** Is a job currently running? (server-side mutex, mirrors the scheduler lock) */
  get active(): string | null {
    return this.activeJobId;
  }

  start(opts: StartOptions): { busy: true; activeJobId: string } | { busy: false; job: InternalJob } {
    if (this.activeJobId) return { busy: true, activeJobId: this.activeJobId };

    this.counter += 1;
    const id = `job-${Date.now().toString(36)}-${this.counter}`;
    const job: InternalJob = {
      id,
      label: opts.label,
      cmd: opts.cmd,
      args: opts.args,
      status: 'running',
      startedAt: Date.now(),
      endedAt: null,
      exitCode: null,
      lines: [],
      proc: null,
      meta: opts.meta,
    };
    this.jobs.set(id, job);
    this.order.push(id);
    this.activeJobId = id;
    this.trim();

    const spawnCmd = opts.shell ? 'bash' : opts.cmd;
    const spawnArgs = opts.shell ? ['-c', [opts.cmd, ...opts.args].join(' ')] : opts.args;

    this.append(job, 'system', `$ ${opts.cmd} ${opts.args.join(' ')}`);
    let proc: ChildProcess;
    try {
      proc = spawn(spawnCmd, spawnArgs, {
        cwd: opts.cwd,
        env: opts.env ? { ...process.env, ...opts.env } : process.env,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (err) {
      this.append(job, 'system', `failed to spawn: ${String(err)}`);
      this.finish(job, 1);
      return { busy: false, job };
    }
    job.proc = proc;

    this.pipe(job, proc, 'stdout');
    this.pipe(job, proc, 'stderr');

    proc.on('error', (err) => {
      this.append(job, 'system', `process error: ${err.message}`);
    });
    proc.on('close', (code) => {
      this.finish(job, code);
    });

    return { busy: false, job };
  }

  private pipe(job: InternalJob, proc: ChildProcess, stream: 'stdout' | 'stderr'): void {
    const src = proc[stream];
    if (!src) return;
    let buf = '';
    src.setEncoding('utf8');
    src.on('data', (chunk: string) => {
      buf += chunk;
      let nl: number;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        this.append(job, stream, line);
      }
    });
    src.on('end', () => {
      if (buf.length) this.append(job, stream, buf);
    });
  }

  private append(job: InternalJob, stream: JobLogLine['stream'], line: string): void {
    const entry: JobLogLine = { t: Date.now(), stream, line };
    job.lines.push(entry);
    if (job.lines.length > MAX_LINES) job.lines.shift();
    const set = this.subs.get(job.id);
    if (set) for (const fn of set) fn(entry);
  }

  private finish(job: InternalJob, code: number | null): void {
    if (job.status !== 'running') return;
    job.exitCode = code;
    job.endedAt = Date.now();
    if (code === 0) job.status = 'done';
    else if (code === SCHEDULER_BUSY_CODE) {
      job.status = 'busy';
      this.append(job, 'system', 'scheduler is already running (exit 75) — try again later');
    } else job.status = 'error';
    this.append(job, 'system', `exited with code ${code} (${job.status})`);
    if (this.activeJobId === job.id) this.activeJobId = null;
    const summary = this.toSummary(job);
    const set = this.endSubs.get(job.id);
    if (set) for (const fn of set) fn(summary);
  }

  private trim(): void {
    while (this.order.length > MAX_JOBS) {
      const id = this.order.shift();
      if (id && id !== this.activeJobId) {
        this.jobs.delete(id);
        this.subs.delete(id);
        this.endSubs.delete(id);
      }
    }
  }

  subscribe(id: string, onLine: Subscriber, onEnd: EndSubscriber): () => void {
    if (!this.subs.has(id)) this.subs.set(id, new Set());
    if (!this.endSubs.has(id)) this.endSubs.set(id, new Set());
    this.subs.get(id)!.add(onLine);
    this.endSubs.get(id)!.add(onEnd);
    return () => {
      this.subs.get(id)?.delete(onLine);
      this.endSubs.get(id)?.delete(onEnd);
    };
  }

  /** Request termination of a running job. Returns false if not running. */
  cancel(id: string): boolean {
    const job = this.jobs.get(id);
    if (!job || job.status !== 'running' || !job.proc) return false;
    this.append(job, 'system', 'cancellation requested (SIGTERM)');
    job.proc.kill('SIGTERM');
    return true;
  }

  private toSummary(job: InternalJob): JobSummary {
    return {
      id: job.id,
      label: job.label,
      status: job.status,
      startedAt: job.startedAt,
      endedAt: job.endedAt,
      exitCode: job.exitCode,
      meta: job.meta,
    };
  }

  get(id: string): JobDetail | null {
    const job = this.jobs.get(id);
    if (!job) return null;
    return { ...this.toSummary(job), cmd: job.cmd, args: job.args, lines: job.lines };
  }

  list(): JobSummary[] {
    return this.order
      .map((id) => this.jobs.get(id))
      .filter((j): j is InternalJob => !!j)
      .map((j) => this.toSummary(j))
      .reverse();
  }
}
