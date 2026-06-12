import { useState } from 'react';
import { useAnalyzeRun } from '../lib/useAnalyzeRun';
import AnalyzePanel from './AnalyzePanel';
import { Modal } from './ui';

// Mirror the server's analyze-ticker validation (routes/actions.ts).
const SYMBOL_RE = /^[A-Z0-9.\-]{1,10}$/;

/**
 * Global "Analyze <ticker>" launcher for the topbar. Lets the user type any
 * ticker and run the ticker-analysis skill, reusing the same engine/panel as the
 * per-row watchlist button. The running indicator survives the modal being closed.
 */
export default function AnalyzeDialog({ date }: { date: string | null }) {
  const [open, setOpen] = useState(false);
  const [ticker, setTicker] = useState('');
  const run = useAnalyzeRun(date);

  const running = run.state === 'running';
  const sym = ticker.trim().toUpperCase();
  const valid = SYMBOL_RE.test(sym);
  const last = run.lines[run.lines.length - 1] ?? 'starting…';

  function onTickerChange(v: string) {
    setTicker(v.toUpperCase());
    // Clear stale log/reconcile from a previous ticker once we're idle again.
    if (!running && run.state !== 'idle') run.reset();
  }

  return (
    <>
      {running ? (
        <button className="analyze-topbar" title={last} onClick={() => setOpen(true)}>
          <span className="spin">⟳</span> {sym} {run.elapsed}s
        </button>
      ) : (
        <button onClick={() => setOpen(true)} title="Analyze any ticker">
          🔍 Analyze
        </button>
      )}

      {open ? (
        <Modal
          title={
            <>
              Analyze {sym || '…'}
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
          <div className="field">
            <strong>Ticker</strong>
            <input
              autoFocus
              placeholder="напр. AOS"
              value={ticker}
              disabled={running}
              onChange={(e) => onTickerChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && valid && !running)
                  void run.start(sym, { createAlerts: false, saveToNotes: false });
              }}
              style={{ width: 160, textTransform: 'uppercase' }}
            />
            {ticker && !valid ? (
              <span className="err">Invalid ticker.</span>
            ) : (
              <span className="hint">Latin letters, digits, dot or dash; up to 12 chars.</span>
            )}
          </div>

          <AnalyzePanel ticker={sym} date={date} run={run} canRun={valid} />
        </Modal>
      ) : null}
    </>
  );
}
