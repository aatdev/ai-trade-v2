import { useState } from 'react';
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

export default function SignalsFeed({ refetch }: { refetch: Refetch }) {
  const { data, isLoading, error } = useSignals(refetch);
  const qc = useQueryClient();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [delError, setDelError] = useState<string | null>(null);

  async function onDelete(s: SignalBlock) {
    if (!window.confirm(`Delete signal ${s.ticker} (${s.date}) from signals.md?`)) return;
    setBusyId(s.id);
    setDelError(null);
    try {
      await deleteSignal(s.ticker, s.date);
      await qc.invalidateQueries({ queryKey: ['signals'] });
    } catch (e) {
      setDelError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
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

  return (
    <Card title="Signals Feed" className="full">
      {delError ? <div className="err" style={{ marginBottom: 8 }}>{delError}</div> : null}
      {data.signals.length === 0 ? (
        // Fallback: unparseable journal — render the raw markdown without delete controls.
        <div className="md feed">
          <Markdown remarkPlugins={[remarkGfm]} components={{ a: MdLink }}>
            {data.content}
          </Markdown>
        </div>
      ) : (
        <div className="feed">
          {data.signals.map((s) => (
            <div className="signal-block md" key={s.id}>
              <button
                className="signal-del"
                title={`Delete ${s.ticker} (${s.date})`}
                disabled={busyId === s.id}
                onClick={() => void onDelete(s)}
              >
                {busyId === s.id ? '…' : '🗑'}
              </button>
              <Markdown remarkPlugins={[remarkGfm]} components={{ a: MdLink }}>
                {s.markdown}
              </Markdown>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
