import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Link } from 'react-router-dom';
import type { SignalBlock } from '@shared/types';
import { deleteSignal, useSignals, type Refetch } from '../api';
import { Card, Empty, ErrorNote, Loading } from './ui';

const REPORT_LINK_RE = /\.?\/?([A-Za-z0-9.\-]+)\/(\d{4}-\d{2}-\d{2})\//;

function MdLink({ href, children }: { href?: string; children?: React.ReactNode }) {
  const m = href?.match(REPORT_LINK_RE);
  if (m) return <Link to={`/ticker/${m[1]}/${m[2]}`}>{children}</Link>;
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

function signalColor(status: string | null): string {
  const s = (status || '').toUpperCase();
  if (/\b(SHORT|SELL)\b/.test(s)) return 'var(--red)';
  if (/\b(BUY|LONG)\b/.test(s)) return 'var(--green)';
  return 'var(--muted)';
}

export default function SignalsFeed({ refetch }: { refetch: Refetch }) {
  const { data, isLoading, error } = useSignals(refetch);
  const qc = useQueryClient();
  const [filter, setFilter] = useState<string>('');
  const [active, setActive] = useState<SignalBlock | null>(null);
  const [busy, setBusy] = useState(false);
  const [delError, setDelError] = useState<string | null>(null);

  const signals = data?.signals ?? [];
  const tickers = useMemo(
    () => Array.from(new Set(signals.map((s) => s.ticker))).sort(),
    [signals],
  );
  const rows = filter ? signals.filter((s) => s.ticker === filter) : signals;

  async function onDelete(s: SignalBlock) {
    if (!window.confirm(`Delete signal ${s.ticker} (${s.date}) from signals.md?`)) return;
    setBusy(true);
    setDelError(null);
    try {
      await deleteSignal(s.ticker, s.date);
      await qc.invalidateQueries({ queryKey: ['signals'] });
      setActive((cur) => (cur?.id === s.id ? null : cur));
    } catch (e) {
      setDelError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (isLoading)
    return (
      <Card title="Signals Feed" className="full">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Signals Feed" className="full">
        <ErrorNote error={error} />
      </Card>
    );
  if (!data?.present || data.content.trim() === '')
    return (
      <Card title="Signals Feed" className="full">
        <Empty>No signals.md journal yet.</Empty>
      </Card>
    );

  // Fallback: journal present but unparseable into blocks — render raw markdown.
  if (signals.length === 0)
    return (
      <Card title="Signals Feed" className="full">
        <div className="md feed">
          <Markdown remarkPlugins={[remarkGfm]} components={{ a: MdLink }}>
            {data.content}
          </Markdown>
        </div>
      </Card>
    );

  return (
    <Card title={`Signals Feed (${rows.length})`} className="full">
      <div className="control" style={{ marginBottom: 10 }}>
        Ticker
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">all ({signals.length})</option>
          {tickers.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        {filter ? (
          <button className="link-btn" onClick={() => setFilter('')}>
            clear
          </button>
        ) : null}
      </div>
      {delError ? <div className="err" style={{ marginBottom: 8 }}>{delError}</div> : null}

      <div className="scroll-x feed">
        <table className="rows-clickable">
          <thead>
            <tr>
              <th>Date</th>
              <th>Ticker</th>
              <th style={{ textAlign: 'left' }}>Signal</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} onClick={() => setActive(s)}>
                <td className="muted">{s.date}</td>
                <td className="sym">
                  <Link to={`/ticker/${s.ticker}`} onClick={(e) => e.stopPropagation()}>
                    {s.ticker}
                  </Link>
                </td>
                <td style={{ textAlign: 'left', color: signalColor(s.status) }} title={s.status ?? ''}>
                  <span className="signal-status">{s.status ?? s.heading}</span>
                </td>
                <td>
                  <button className="link-btn" onClick={(e) => (e.stopPropagation(), setActive(s))}>
                    view
                  </button>
                  <button
                    className="link-btn danger"
                    disabled={busy}
                    onClick={(e) => (e.stopPropagation(), void onDelete(s))}
                  >
                    🗑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {active ? (
        <div className="modal-backdrop" onClick={() => setActive(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="md">
              <Markdown remarkPlugins={[remarkGfm]} components={{ a: MdLink }}>
                {active.markdown}
              </Markdown>
            </div>
            <div className="btn-row" style={{ justifyContent: 'flex-end' }}>
              <button className="danger" disabled={busy} onClick={() => void onDelete(active)}>
                🗑 Delete signal
              </button>
              <button onClick={() => setActive(null)}>Close</button>
            </div>
          </div>
        </div>
      ) : null}
    </Card>
  );
}
