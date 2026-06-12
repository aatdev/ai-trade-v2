import { Link } from 'react-router-dom';
import type { WatchlistCandidate } from '@shared/types';
import { useWatchlist, type Refetch } from '../api';
import { fmtMoney, fmtNum, fmtScore } from '../lib/format';
import { Card, Collapsible, Empty, ErrorNote, Loading, SideBadge } from './ui';

function CandidateTable({ rows }: { rows: WatchlistCandidate[] }) {
  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <th style={{ textAlign: 'left' }}>Side</th>
            <th style={{ textAlign: 'left' }}>Setup</th>
            <th>Pivot</th>
            <th>Worst</th>
            <th>Stop</th>
            <th>Target</th>
            <th>Shares</th>
            <th>Risk $</th>
            <th>Score</th>
            <th>Val</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.ticker}>
              <td className="sym">
                <Link to={`/ticker/${c.ticker}`}>{c.ticker}</Link>
              </td>
              <td style={{ textAlign: 'left' }}>
                <SideBadge side={c.side} />
              </td>
              <td style={{ textAlign: 'left' }} className="muted">
                {c.setup ?? '—'}
              </td>
              <td>{fmtNum(c.pivot)}</td>
              <td>{fmtNum(c.worst_entry)}</td>
              <td>{fmtNum(c.stop)}</td>
              <td>{fmtNum(c.target)}</td>
              <td>{c.shares ?? '—'}</td>
              <td>{fmtMoney(c.risk_dollars)}</td>
              <td>{fmtScore(c.score)}</td>
              <td title={c.validation_note ?? undefined}>
                {c.validated === true ? (
                  <span style={{ color: 'var(--green)' }}>✓</span>
                ) : c.validated === false ? (
                  <span style={{ color: 'var(--red)' }}>✗</span>
                ) : (
                  <span className="muted">·</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function WatchlistCard({ date, refetch }: { date: string | null; refetch: Refetch }) {
  const { data, isLoading, error } = useWatchlist(date, refetch);
  if (isLoading)
    return (
      <Card title="Watchlist">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Watchlist">
        <ErrorNote error={error} />
      </Card>
    );
  const wl = data?.data;
  if (!wl)
    return (
      <Card title="Watchlist">
        <Empty />
      </Card>
    );

  return (
    <Card title="Watchlist" source={data?.source}>
      {wl.notes ? (
        <div className="muted" style={{ marginBottom: 10, fontSize: 13 }}>
          {wl.notes}
        </div>
      ) : null}
      {wl.candidates.length === 0 ? (
        <Empty>No candidates.</Empty>
      ) : (
        <CandidateTable rows={wl.candidates} />
      )}
      {wl.rejected_by_validation.length > 0 ? (
        <Collapsible label="Rejected by chart validation" count={wl.rejected_by_validation.length}>
          <CandidateTable rows={wl.rejected_by_validation} />
        </Collapsible>
      ) : null}
    </Card>
  );
}
