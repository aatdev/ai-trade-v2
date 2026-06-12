import { useState } from 'react';
import { useAnalyzeRun } from '../lib/useAnalyzeRun';
import AnalyzePanel from './AnalyzePanel';
import { Modal } from './ui';

export default function AnalyzeButton({ ticker, date }: { ticker: string; date: string | null }) {
  const [open, setOpen] = useState(false);
  const run = useAnalyzeRun(date);

  const running = run.state === 'running';
  const last = run.lines[run.lines.length - 1] ?? 'starting…';

  return (
    <div className="analyze">
      {running ? (
        <div className="analyze-running">
          <span className="spin">⟳</span>
          <span className="step" title={last}>
            {last}
          </span>
          <span className="muted">{run.elapsed}s</span>
          <button className="link-btn" onClick={() => setOpen(true)}>
            log
          </button>
          <button className="link-btn danger" onClick={() => void run.cancel()}>
            ✕
          </button>
        </div>
      ) : (
        <div className="analyze-idle">
          <button className="mini" title={`Run ticker-analysis for ${ticker}`} onClick={() => setOpen(true)}>
            🔍 Analyze
          </button>
          {run.state === 'done' ? <span style={{ color: 'var(--green)' }}>✓</span> : null}
          {run.state === 'error' || run.state === 'busy' ? (
            <span className="err" title={run.error ?? ''}>
              {run.state}
            </span>
          ) : null}
          {run.lines.length > 0 ? (
            <button className="link-btn" onClick={() => setOpen(true)}>
              log
            </button>
          ) : null}
        </div>
      )}

      {open ? (
        <Modal
          title={
            <>
              Analyze {ticker}
              {running ? (
                <span style={{ color: 'var(--accent)' }}> — running ({run.elapsed}s)</span>
              ) : run.state !== 'idle' ? (
                <span style={{ color: run.state === 'done' ? 'var(--green)' : 'var(--orange)' }}>
                  {' '}
                  — {run.state}
                </span>
              ) : null}
            </>
          }
          onClose={() => setOpen(false)}
          footer={<button onClick={() => setOpen(false)}>Close</button>}
        >
          <AnalyzePanel ticker={ticker} date={date} run={run} />
        </Modal>
      ) : null}
    </div>
  );
}
