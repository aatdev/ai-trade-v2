import { useEffect, useRef, useState } from 'react';
import type { JobLogLine, SchedulerSlot, StartJobResponse } from '@shared/types';
import { deleteAlerts, runSlot, syncAlerts } from '../api';
import { busyMessage, fmtClock } from '../lib/format';

const SLOTS: SchedulerSlot[] = ['premarket', 'evening-prep', 'intraday', 'weekly', 'monthly'];

export default function ActionsPanel({ onClose }: { onClose: () => void }) {
  const [slot, setSlot] = useState<SchedulerSlot>('evening-prep');
  const [dryRun, setDryRun] = useState(true);
  const [force, setForce] = useState(false);
  const [noTelegram, setNoTelegram] = useState(false);
  const [tickers, setTickers] = useState('');
  const [jobId, setJobId] = useState<string | null>(null);
  const [lines, setLines] = useState<JobLogLine[]>([]);
  const [status, setStatus] = useState('');
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (!jobId) return;
    setLines([]);
    setStatus('running');
    const es = new EventSource(`/api/actions/jobs/${jobId}/stream`);
    es.addEventListener('log', (e) => {
      const line = JSON.parse((e as MessageEvent).data) as JobLogLine;
      setLines((prev) => [...prev, line]);
    });
    es.addEventListener('end', (e) => {
      const d = JSON.parse((e as MessageEvent).data) as { status: string };
      setStatus(d.status);
      es.close();
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [jobId]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [lines]);

  async function launch(fn: () => Promise<StartJobResponse>) {
    setError(null);
    try {
      const res = await fn();
      if (!res.ok) {
        setError(res.busy ? busyMessage(res.lane, res.activeJobId) : res.error || 'Failed to start.');
        return;
      }
      setJobId(res.job!.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function onRunSlot() {
    if (!dryRun && !window.confirm(`Run slot "${slot}" LIVE? This calls claude/Telegram and may place real intent.`))
      return;
    void launch(() => runSlot({ slot, dryRun, force, noTelegram }));
  }

  function onDelete() {
    const list = tickers.split(/[\s,]+/).filter(Boolean);
    if (list.length === 0) {
      setError('Enter at least one ticker.');
      return;
    }
    void launch(() => deleteAlerts(list));
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Actions</h3>

        <div className="field">
          <strong>Run scheduler slot</strong>
          <div className="btn-row">
            <select value={slot} onChange={(e) => setSlot(e.target.value as SchedulerSlot)}>
              {SLOTS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <label className="check">
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
              dry-run
            </label>
            <label className="check">
              <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
              force
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={noTelegram}
                onChange={(e) => setNoTelegram(e.target.checked)}
              />
              no-telegram
            </label>
            <button className="primary" onClick={onRunSlot}>
              Run
            </button>
          </div>
          <span className="hint">
            Dry-run prints prompts without calling claude/Telegram. Live runs respect the
            scheduler's single-run lock (exit 75 = busy).
          </span>
        </div>

        <div className="field">
          <strong>TradingView alerts</strong>
          <div className="btn-row">
            <button onClick={() => void launch(() => syncAlerts())}>Sync from signals.md</button>
            <input
              placeholder="tickers e.g. ABT, MP"
              value={tickers}
              onChange={(e) => setTickers(e.target.value)}
              style={{ minWidth: 180 }}
            />
            <button className="danger" onClick={onDelete}>
              Delete alerts
            </button>
          </div>
          <span className="hint">Requires TradingView Desktop running (CDP).</span>
        </div>

        {error ? <div className="err" style={{ marginBottom: 10 }}>{error}</div> : null}

        {jobId ? (
          <div className="field">
            <strong>
              Job {jobId} — <span style={{ color: status === 'done' ? 'var(--green)' : status === 'running' ? 'var(--accent)' : 'var(--orange)' }}>{status}</span>
            </strong>
            <pre className="joblog" ref={logRef}>
              {lines.map((l, i) => (
                <div key={i} className={l.stream}>
                  <span className="muted">{fmtClock(l.t)} </span>
                  {l.line}
                </div>
              ))}
            </pre>
          </div>
        ) : null}

        <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
