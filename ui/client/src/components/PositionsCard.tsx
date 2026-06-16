import { Link } from 'react-router-dom';
import { usePortfolio, type Refetch } from '../api';
import { fmtDateTime, fmtMoney, fmtNum, fmtPct, fmtSignedPct } from '../lib/format';
import { useVersionedSource } from '../lib/useVersionedSource';
import { pnlColor } from '../lib/zones';
import SourceSelect from './SourceSelect';
import { Card, Empty, ErrorNote, Loading, SideBadge, Stat } from './ui';

export default function PositionsCard({ date, refetch }: { date: string | null; refetch: Refetch }) {
  const [source, setSource] = useVersionedSource(date);
  const { data, isLoading, error } = usePortfolio(date, source, refetch);
  if (isLoading)
    return (
      <Card title="Open Positions / Heat">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Open Positions / Heat">
        <ErrorNote error={error} />
      </Card>
    );
  const h = data?.data;
  if (!h)
    return (
      <Card title="Open Positions / Heat">
        <Empty />
      </Card>
    );

  return (
    <Card
      title="Open Positions / Heat"
      sourceSelect={
        <SourceSelect
          kind="portfolio"
          value={source}
          latest={data?.source ?? null}
          onChange={setSource}
          refetch={refetch}
        />
      }
    >
      <div className="stats">
        <Stat k="Open Risk" v={`${fmtPct(h.open_risk_pct)} · ${fmtMoney(h.open_risk_dollars)}`} />
        <Stat
          k="Heat Left"
          v={`${fmtPct(h.remaining_heat_pct)} / ${fmtPct(h.max_portfolio_heat_pct)}`}
        />
        <Stat k="Slots Used" v={`${h.positions_count ?? 0} / ${h.max_positions ?? '—'}`} />
        <Stat k="Account" v={fmtMoney(h.account_size)} />
      </div>

      {h.positions.length === 0 ? (
        <p className="muted" style={{ marginTop: 12 }}>
          No open positions — all {h.max_positions ?? 0} slots free.
        </p>
      ) : (
        <div className="scroll-x" style={{ marginTop: 12 }}>
          <table>
            <thead>
              <tr>
                <th>Ticker</th>
                <th style={{ textAlign: 'left' }}>Side</th>
                <th>Entry</th>
                <th>Stop</th>
                <th>Last</th>
                <th>P&amp;L %</th>
                <th>Days</th>
                <th>MAE / MFE</th>
              </tr>
            </thead>
            <tbody>
              {h.positions.map((p) => (
                <tr key={p.ticker}>
                  <td className="sym">
                    <Link to={`/ticker/${p.ticker}`}>{p.ticker}</Link>
                  </td>
                  <td style={{ textAlign: 'left' }}>
                    <SideBadge side={p.side} />
                  </td>
                  <td>{fmtNum(p.entry_price)}</td>
                  <td>{fmtNum(p.stop_loss)}</td>
                  <td>{fmtNum(p.current_price)}</td>
                  <td style={{ color: pnlColor(p.pnl_pct) }}>{fmtSignedPct(p.pnl_pct)}</td>
                  <td>{p.days_held ?? '—'}</td>
                  <td className="muted">
                    {fmtPct(p.mae_pct)} / {fmtPct(p.mfe_pct)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {h.warnings.length > 0 ? <div className="warns">{h.warnings.join(' · ')}</div> : null}
      {h.generated_at ? (
        <div className="muted" style={{ marginTop: 8, fontSize: 11 }}>
          snapshot {fmtDateTime(h.generated_at)}
        </div>
      ) : null}
    </Card>
  );
}
