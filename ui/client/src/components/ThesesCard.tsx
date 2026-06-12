import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { ThesisIndexEntry } from '@shared/types';
import { useTheses, useThesis, type Refetch } from '../api';
import { Card, Empty, ErrorNote, Loading } from './ui';

function ThesisRow({ t }: { t: ThesisIndexEntry }) {
  const [open, setOpen] = useState(false);
  const { data } = useThesis(open ? t.id : null);
  return (
    <>
      <tr style={{ cursor: 'pointer' }} onClick={() => setOpen((o) => !o)}>
        <td className="sym">
          <Link to={`/ticker/${t.ticker}`} onClick={(e) => e.stopPropagation()}>
            {t.ticker}
          </Link>
        </td>
        <td style={{ textAlign: 'left' }}>
          <span className="pill">{t.status}</span>
        </td>
        <td style={{ textAlign: 'left' }} className="muted">
          {t.thesis_type ?? '—'}
        </td>
        <td className={t.review_due ? 'review-due' : 'muted'}>
          {t.next_review_date ?? '—'}
          {t.review_due ? ' ⚠' : ''}
        </td>
      </tr>
      {open && data?.thesis_statement ? (
        <tr>
          <td colSpan={4} className="muted" style={{ textAlign: 'left', whiteSpace: 'normal' }}>
            {data.thesis_statement}
          </td>
        </tr>
      ) : null}
    </>
  );
}

const STATUS_ORDER: Record<string, number> = { OPEN: 0, IDEA: 1, CLOSED: 2 };

export default function ThesesCard({ refetch }: { refetch: Refetch }) {
  const { data, isLoading, error } = useTheses(refetch);
  if (isLoading)
    return (
      <Card title="Theses">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Theses">
        <ErrorNote error={error} />
      </Card>
    );

  const theses = [...(data?.theses ?? [])].sort((a, b) => {
    if (a.review_due !== b.review_due) return a.review_due ? -1 : 1;
    return (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9);
  });

  return (
    <Card title="Theses">
      {theses.length === 0 ? (
        <Empty>No tracked theses.</Empty>
      ) : (
        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th>Ticker</th>
                <th style={{ textAlign: 'left' }}>Status</th>
                <th style={{ textAlign: 'left' }}>Type</th>
                <th>Next Review</th>
              </tr>
            </thead>
            <tbody>
              {theses.map((t) => (
                <ThesisRow key={t.id} t={t} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
