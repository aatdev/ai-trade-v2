import { Link } from 'react-router-dom';
import { useIbSnapshot, type Refetch } from '../api';
import { fmtDateTime, fmtMoney, fmtNum, fmtSignedPct } from '../lib/format';
import { pnlColor, sideColor } from '../lib/zones';
import { Card, Empty, ErrorNote, Loading, SideBadge, Stat } from './ui';

/**
 * "Счёт IB" tab — live, read-only Interactive Brokers account balances and open
 * positions, fetched from GET /api/ib (which shells out to the IB Gateway).
 * When the Gateway is down / unauthenticated the snapshot comes back with
 * `ok:false`, which we render as a friendly notice rather than a hard error.
 */
export default function IbTab({ refetch }: { refetch: Refetch }) {
  const { data, isLoading, error } = useIbSnapshot(refetch);

  if (isLoading)
    return (
      <Card title="IB — Счёт">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="IB — Счёт">
        <ErrorNote error={error} />
      </Card>
    );
  if (!data)
    return (
      <Card title="IB — Счёт">
        <Empty />
      </Card>
    );

  if (!data.ok) {
    return (
      <Card title="IB — Счёт">
        <div className="warns">
          IB Gateway недоступен. {data.error ?? 'Нет соединения с Interactive Brokers.'}
        </div>
        <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>
          Запустите Claude-сессию с настроенным interactive-brokers MCP и пройдите вход в IB
          Gateway (логин / 2FA), затем обновите страницу. Проверка соединения:{' '}
          <code>python3 skills/ib-portfolio-manager/scripts/check_ib_connection.py</code>.
        </p>
      </Card>
    );
  }

  const s = data.summary;
  const positions = data.positions ?? [];
  const meta = [
    data.account_id,
    data.source === 'fixture' ? 'fixture' : null,
    data.generated_at ? fmtDateTime(data.generated_at) : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <>
      <Card title="IB — Счёт" source={meta || null}>
        <div style={{ marginBottom: 12 }}>
          <ModeBadge mode={data.mode} />
        </div>
        {s ? (
          <div className="stats">
            <Stat k="Чистая ликвидация" v={fmtMoney(s.net_liquidation)} />
            <Stat k="Денежные средства" v={fmtMoney(s.total_cash)} />
            <Stat k="Доступно" v={fmtMoney(s.available_funds)} />
            <Stat k="Покуп. способность" v={fmtMoney(s.buying_power)} />
            <Stat k="Стоимость позиций" v={fmtMoney(s.gross_position_value)} />
            <Stat
              k="Нереализ. P&L"
              v={fmtMoney(s.unrealized_pnl)}
              color={pnlColor(s.unrealized_pnl)}
            />
          </div>
        ) : (
          <Empty>Нет данных по счёту.</Empty>
        )}
      </Card>

      <Card title="IB — Позиции" className="full">
        {positions.length === 0 ? (
          <p className="muted" style={{ marginTop: 4 }}>
            Открытых позиций нет.
          </p>
        ) : (
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Тикер</th>
                  <th style={{ textAlign: 'left' }}>Сторона</th>
                  <th>Кол-во</th>
                  <th>Ср. цена</th>
                  <th>Цена</th>
                  <th>Стоимость</th>
                  <th>P&amp;L $</th>
                  <th>P&amp;L %</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={`${p.symbol}-${p.conid ?? ''}`}>
                    <td className="sym">
                      <Link to={`/ticker/${p.symbol}`}>{p.symbol}</Link>
                    </td>
                    <td style={{ textAlign: 'left' }}>
                      {p.side ? (
                        <SideBadge side={p.side} />
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td style={{ color: sideColor(p.side) }}>{fmtNum(p.position, 0)}</td>
                    <td>{fmtNum(p.avg_cost)}</td>
                    <td>{fmtNum(p.market_price)}</td>
                    <td>{fmtMoney(p.market_value)}</td>
                    <td style={{ color: pnlColor(p.unrealized_pnl) }}>
                      {fmtMoney(p.unrealized_pnl)}
                    </td>
                    <td style={{ color: pnlColor(p.unrealized_pnl_pct) }}>
                      {fmtSignedPct(p.unrealized_pnl_pct)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function ModeBadge({ mode }: { mode: string | null }) {
  if (!mode) return null;
  const live = mode.toLowerCase() === 'live';
  return (
    <span className="badge" style={{ color: live ? 'var(--red)' : 'var(--green)' }}>
      {live ? 'LIVE' : 'PAPER'}
    </span>
  );
}
