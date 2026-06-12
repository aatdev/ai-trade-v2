import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { ScreenerResult, Sourced } from '@shared/types';
import { useScreeners, type Refetch } from '../api';
import { fmtNum, fmtScore } from '../lib/format';
import { scoreColor } from '../lib/zones';
import { Card, Empty, ErrorNote, GradeBadge, Loading } from './ui';

function num(metrics: Record<string, number | boolean | null>, key: string): number | null {
  const v = metrics[key];
  return typeof v === 'number' ? v : null;
}

function ScreenerTable({ result }: { result: ScreenerResult }) {
  if (result.candidates.length === 0) return <Empty>No candidates.</Empty>;
  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Grade</th>
            <th>Score</th>
            <th>Entry</th>
            <th>Stop</th>
            <th>Target</th>
            <th>RSI14</th>
            <th style={{ textAlign: 'left' }}>Sector</th>
          </tr>
        </thead>
        <tbody>
          {result.candidates.map((c) => (
            <tr key={c.symbol}>
              <td className="sym">
                <Link to={`/ticker/${c.symbol}`}>{c.symbol}</Link>
              </td>
              <td>
                <GradeBadge grade={c.grade} />
              </td>
              <td style={{ color: scoreColor(c.composite_score) }}>{fmtScore(c.composite_score)}</td>
              <td>{fmtNum(c.entry)}</td>
              <td>{fmtNum(c.stop)}</td>
              <td>{fmtNum(c.target)}</td>
              <td>{fmtNum(num(c.metrics, 'rsi14'), 1)}</td>
              <td style={{ textAlign: 'left' }} className="muted">
                {c.sector ?? '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ScreenersCard({ date, refetch }: { date: string | null; refetch: Refetch }) {
  const { data, isLoading, error } = useScreeners(date, refetch);
  const [tab, setTab] = useState<'vcp' | 'swingShort'>('swingShort');

  if (isLoading)
    return (
      <Card title="Screeners">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Screeners">
        <ErrorNote error={error} />
      </Card>
    );

  const active = (tab === 'vcp' ? data?.vcp : data?.swingShort) as
    | Sourced<ScreenerResult>
    | undefined;
  const vcpN = data?.vcp.data?.candidates.length ?? 0;
  const shortN = data?.swingShort.data?.candidates.length ?? 0;

  return (
    <Card title="Screeners" source={active?.source}>
      <div className="tabs">
        <button
          className={`tab ${tab === 'swingShort' ? 'active' : ''}`}
          onClick={() => setTab('swingShort')}
        >
          Swing-Short ({shortN})
        </button>
        <button className={`tab ${tab === 'vcp' ? 'active' : ''}`} onClick={() => setTab('vcp')}>
          VCP ({vcpN})
        </button>
      </div>
      {active?.data ? <ScreenerTable result={active.data} /> : <Empty>No screener run for this date.</Empty>}
    </Card>
  );
}
