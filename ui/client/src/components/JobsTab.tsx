import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { JobStatus, JobSummary } from '@shared/types';
import { cancelJob, useJobs } from '../api';
import { useJobLog } from '../lib/useJobLog';
import { fmtClock, LANE_LABEL_RU } from '../lib/format';
import AnalysisModal from './AnalysisModal';

/** Job kinds hidden from the Задания tab (and its running-count badge). */
const HIDDEN_JOB_KINDS = new Set(['bottom-flow-screener-run']);

/** Whether a job should appear in the Задания tab. */
export const isVisibleJob = (job: JobSummary): boolean =>
  !HIDDEN_JOB_KINDS.has(String(job.meta?.kind));

const STATUS: Record<JobStatus, { label: string; color: string }> = {
  running: { label: 'выполняется', color: 'var(--accent)' },
  done: { label: 'готово', color: 'var(--green)' },
  error: { label: 'ошибка', color: 'var(--red)' },
  busy: { label: 'занято', color: 'var(--orange)' },
};

const SCREENER_KINDS = new Set([
  'screener-run',
  'screener-plan',
  'screener-save',
  'short-screener-run',
  'bottom-flow-screener-run',
]);

/** Where the "Результат" button navigates for a finished job, or null (log only). */
function resultTarget(job: JobSummary): { ticker: string } | { screener: true } | null {
  if (job.status !== 'done') return null;
  const kind = job.meta?.kind;
  const ticker = job.meta?.ticker;
  if (kind === 'analyze-ticker' && typeof ticker === 'string') return { ticker };
  if (SCREENER_KINDS.has(String(kind))) return { screener: true };
  return null;
}

function fmtDur(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return `${m}m ${rem}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export default function JobsTab({ onNavigateTab }: { onNavigateTab: (tab: string) => void }) {
  const { data, isLoading } = useJobs(2500);
  const qc = useQueryClient();
  const [logJobId, setLogJobId] = useState<string | null>(null);
  const [analysisTicker, setAnalysisTicker] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const jobs = (data?.jobs ?? []).filter(isVisibleJob);
  const running = jobs.filter((j) => j.status === 'running');

  // Tick the running-duration display once a second while anything is running.
  useEffect(() => {
    if (running.length === 0) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running.length]);

  async function onCancel(id: string) {
    await cancelJob(id).catch(() => undefined);
    await qc.invalidateQueries({ queryKey: ['jobs'] });
  }

  function onResult(job: JobSummary) {
    const t = resultTarget(job);
    if (!t) return;
    // Ticker analysis → open the saved report in a modal (no navigation away);
    // screener result lives in the Скринер tab, so switch to it.
    if ('ticker' in t) setAnalysisTicker(t.ticker);
    else onNavigateTab('screener');
  }

  return (
    <div className="card jobs-card">
      <h2>
        Задания{' '}
        {running.length ? <span className="pill">{running.length} активн.</span> : null}
      </h2>
      <p className="hint" style={{ marginTop: -4 }}>
        Задания разных типов идут параллельно; задачи одного ресурса (дорожки) сериализуются.
      </p>

      {isLoading ? (
        <div className="muted">Загрузка…</div>
      ) : jobs.length === 0 ? (
        <div className="muted">Нет заданий.</div>
      ) : (
        <div className="jobs-list">
          {jobs.map((job) => {
            const st = STATUS[job.status];
            const dur =
              job.status === 'running'
                ? now - job.startedAt
                : (job.endedAt ?? job.startedAt) - job.startedAt;
            const target = resultTarget(job);
            return (
              <div key={job.id} className="job-row">
                <div className="job-main">
                  <span className="job-status" style={{ color: st.color, borderColor: st.color }}>
                    {st.label}
                  </span>
                  {job.lane ? <span className="chip">{LANE_LABEL_RU[job.lane]}</span> : null}
                  <span className="job-label">{job.label}</span>
                </div>
                <div className="job-meta muted">
                  <span>{fmtClock(job.startedAt)}</span>
                  <span>· {fmtDur(dur)}</span>
                  {job.exitCode != null ? <span>· code {job.exitCode}</span> : null}
                </div>
                <div className="job-actions btn-row">
                  <button className="link-btn" onClick={() => setLogJobId(job.id)}>
                    Лог
                  </button>
                  {target ? (
                    <button className="link-btn" onClick={() => onResult(job)}>
                      Результат
                    </button>
                  ) : null}
                  {job.status === 'running' ? (
                    <button className="link-btn danger" onClick={() => void onCancel(job.id)}>
                      Отменить
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {logJobId ? (
        <JobLogModal jobId={logJobId} onClose={() => setLogJobId(null)} />
      ) : null}

      {analysisTicker ? (
        <AnalysisModal symbol={analysisTicker} onClose={() => setAnalysisTicker(null)} />
      ) : null}
    </div>
  );
}

function JobLogModal({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const { lines, status } = useJobLog(jobId);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>
          Лог задания {jobId}
          {status ? <span className="muted"> — {STATUS[status]?.label ?? status}</span> : null}
        </h3>
        <pre className="joblog">
          {lines.length === 0
            ? '(нет вывода)'
            : lines.map((l, i) => (
                <div key={i} className={l.stream}>
                  <span className="muted">{fmtClock(l.t)} </span>
                  {l.line}
                </div>
              ))}
        </pre>
        <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
          <button onClick={onClose}>Закрыть</button>
        </div>
      </div>
    </div>
  );
}
