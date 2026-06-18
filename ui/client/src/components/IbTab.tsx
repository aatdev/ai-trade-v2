import { lazy, Suspense, useMemo, useState } from 'react';
import type { IbOrder, Side } from '@shared/types';
import { ibBracketOp, useAnalysisIndex, useIbSnapshot, type Refetch } from '../api';
import { useMemoryOp } from '../lib/useMemoryOp';
import { groupIbOrders, legRole, type BracketRow } from '../lib/ibBrackets';
import { fmtDateTime, fmtMoney, fmtNum, fmtSignedPct } from '../lib/format';
import { pnlColor, sideColor } from '../lib/zones';
import type { ChartLevels } from './CandleChart';
import { OpLog } from './MemoryOps';
import { Card, Empty, ErrorNote, Loading, SideBadge, Stat } from './ui';

const TickerChartModal = lazy(() => import('./TickerChartModal'));
const AnalysisModal = lazy(() => import('./AnalysisModal'));

/** Open the chart modal for a ticker, with optional entry/stop/target overlay. */
type OpenChart = (ticker: string, levels: ChartLevels) => void;

/** IB order/trade side ("BUY"/"SELL") → chart Side for overlay coloring. */
function orderSideToSide(side: string | null): Side | undefined {
  const s = (side ?? '').toUpperCase();
  if (s === 'BUY') return 'long';
  if (s === 'SELL') return 'short';
  return undefined;
}

/** Chart overlay levels from a collapsed bracket row (entry pivot / stop / take). */
function levelsFromBracket(row: BracketRow): ChartLevels {
  return {
    side: orderSideToSide(row.side),
    entry: row.entryPrice,
    stop: row.stop?.stop_price ?? null,
    target: row.target?.limit_price ?? null,
  };
}

/**
 * "Счёт IB" tab — live, read-only Interactive Brokers account balances and open
 * positions, fetched from GET /api/ib (which shells out to the IB Gateway).
 * When the Gateway is down / unauthenticated the snapshot comes back with
 * `ok:false`, which we render as a friendly notice rather than a hard error.
 */
export default function IbTab({ refetch }: { refetch: Refetch }) {
  const { data, isLoading, error, refetch: reload, isFetching } = useIbSnapshot(refetch);
  const reloadIb = () => void reload();
  // Cancel op (declared before the early returns so hook order stays stable).
  // Reloads the snapshot once the cancel job finishes.
  const cancelOp = useMemoryOp((status) => {
    if (status === 'done') void reload();
  }, ibBracketOp);
  const onCancelOrders = (orderIds: string[], label: string) => {
    const ids = orderIds.filter(Boolean);
    if (!ids.length) return;
    if (!window.confirm(`Отменить в IB: ${label} (${ids.length} ног)?`)) return;
    void cancelOp.run({ op: 'cancel-ib-order', orderIds: ids });
  };

  // Click a ticker (position / order / bracket / trade) → chart modal, mirroring
  // the watchlist & screener cards. The analysis index drives the "есть анализ"
  // flag + the in-place "open analysis" control.
  const { data: analysisIndex } = useAnalysisIndex(refetch);
  const index = analysisIndex?.tickers ?? {};
  const [chartFor, setChartFor] = useState<{ ticker: string; levels: ChartLevels } | null>(null);
  const [analysisFor, setAnalysisFor] = useState<string | null>(null);
  const openChart: OpenChart = (ticker, levels) =>
    setChartFor({ ticker: ticker.toUpperCase(), levels });

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
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
          <RefreshButton onClick={reloadIb} busy={isFetching} />
        </div>
        <div className="warns">
          IB Gateway недоступен. {data.error ?? 'Нет соединения с Interactive Brokers.'}
        </div>
        <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>
          Запустите Claude-сессию с настроенным interactive-brokers MCP и пройдите вход в IB
          Gateway (логин / 2FA), затем нажмите «Обновить». Проверка соединения:{' '}
          <code>python3 skills/ib-portfolio-manager/scripts/check_ib_connection.py</code>.
        </p>
      </Card>
    );
  }

  const s = data.summary;
  const positions = data.positions ?? [];
  const orders = data.orders ?? [];
  const trades = data.trades ?? [];
  const meta = [
    data.account_id,
    data.source === 'fixture' ? 'fixture' : null,
    data.generated_at ? fmtDateTime(data.generated_at) : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className="grid full" style={{ gap: 12, flex: 1, overflow: 'auto' }}>
      <div style={{ display: 'grid', gap: 12, alignContent: 'start' }}>
        <Card title="IB — Счёт" source={meta || null}>
          <div
            style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}
          >
            <ModeBadge mode={data.mode} />
            <span style={{ flex: 1 }} />
            <RefreshButton onClick={reloadIb} busy={isFetching} />
          </div>
          <SyncFills />
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

        <Card title="IB — Позиции">
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
                        <button
                          type="button"
                          className="ticker-btn"
                          title={`График ${p.symbol}`}
                          onClick={() =>
                            openChart(p.symbol, { side: p.side ?? undefined, entry: p.avg_cost })
                          }
                        >
                          {p.symbol}
                        </button>
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

        <Card title="IB — Ордера">
          <OrdersTable
            orders={orders}
            onCancel={onCancelOrders}
            onOpenChart={openChart}
            busy={cancelOp.state === 'running'}
          />
          <OpLog op={cancelOp} collapsible />
        </Card>
      </div>

      <div style={{ display: 'grid', gap: 12, alignContent: 'start' }}>
        <Card title="IB — История операций">
          {trades.length === 0 ? (
            <p className="muted" style={{ marginTop: 4 }}>
              Сделок за последние дни нет.
            </p>
          ) : (
            <div className="scroll-x">
              <table>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left' }}>Время</th>
                    <th>Тикер</th>
                    <th style={{ textAlign: 'left' }}>Сторона</th>
                    <th>Кол-во</th>
                    <th>Цена</th>
                    <th>Сумма</th>
                    <th>Комиссия</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t, i) => (
                    <tr key={t.execution_id ?? `${t.symbol}-${i}`}>
                      <td style={{ textAlign: 'left' }} className="muted">
                        {fmtTradeTime(t.trade_time)}
                      </td>
                      <td className="sym">
                        <button
                          type="button"
                          className="ticker-btn"
                          title={`График ${t.symbol}`}
                          onClick={() =>
                            openChart(t.symbol, { side: orderSideToSide(t.side), entry: t.price })
                          }
                        >
                          {t.symbol}
                        </button>
                      </td>
                      <td style={{ textAlign: 'left' }}>
                        <OrderSide side={t.side} />
                      </td>
                      <td>{fmtNum(t.quantity, 0)}</td>
                      <td>{fmtNum(t.price)}</td>
                      <td>{fmtMoney(t.amount)}</td>
                      <td>{t.commission != null ? fmtNum(t.commission) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {chartFor ? (
        <Suspense fallback={null}>
          <TickerChartModal
            ticker={chartFor.ticker}
            levels={chartFor.levels}
            hasAnalysis={!!index[chartFor.ticker]}
            onClose={() => setChartFor(null)}
            onOpenAnalysis={() => {
              const t = chartFor.ticker;
              setChartFor(null);
              setAnalysisFor(t);
            }}
          />
        </Suspense>
      ) : null}

      {analysisFor ? (
        <Suspense fallback={null}>
          <AnalysisModal symbol={analysisFor} onClose={() => setAnalysisFor(null)} />
        </Suspense>
      ) : null}
    </div>
  );
}

/**
 * IB open-orders table. Native bracket legs (entry + stop + target) are
 * collapsed into a single row showing the entry pivot, take-profit (Лимит)
 * and protective stop (Стоп); click the row to expand its individual legs.
 */
type CancelHandler = (orderIds: string[], label: string) => void;

/** Status is still working (cancellable) — not filled / cancelled / rejected. */
function cancellable(status: string | null): boolean {
  const t = (status ?? '').toLowerCase();
  return !(t.includes('fill') || t.includes('cancel') || t.includes('reject'));
}

/** Terminal "dead" order — cancelled / rejected / inactive. These linger in the
 * IB snapshot but are noise in the orders table, so they're filtered out. */
function isDead(status: string | null): boolean {
  const t = (status ?? '').toLowerCase();
  return t.includes('cancel') || t.includes('reject') || t.includes('inactive');
}

function OrdersTable({
  orders,
  onCancel,
  onOpenChart,
  busy,
}: {
  orders: IbOrder[];
  onCancel: CancelHandler;
  onOpenChart: OpenChart;
  busy: boolean;
}) {
  // Hide cancelled / rejected / inactive rows — IB keeps them in the snapshot
  // after a cancel, but the orders table should show only working/filled ones.
  const visible = useMemo(() => orders.filter((o) => !isDead(o.status)), [orders]);
  const rows = useMemo(() => groupIbOrders(visible), [visible]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  if (visible.length === 0)
    return (
      <p className="muted" style={{ marginTop: 4 }}>
        Активных ордеров нет.
      </p>
    );

  const toggle = (k: string) => setExpanded((e) => ({ ...e, [k]: !e[k] }));

  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>Тикер</th>
            <th style={{ textAlign: 'left' }}>Сторона</th>
            <th style={{ textAlign: 'left' }}>Тип</th>
            <th>Кол-во</th>
            <th>Лимит</th>
            <th>Стоп</th>
            <th>TIF</th>
            <th style={{ textAlign: 'left' }}>Статус</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) =>
            row.kind === 'single' ? (
              <OrderRow
                key={row.key}
                o={row.order}
                onCancel={onCancel}
                onOpenChart={onOpenChart}
                busy={busy}
              />
            ) : (
              <BracketRows
                key={row.key}
                row={row}
                open={!!expanded[row.key]}
                onToggle={() => toggle(row.key)}
                onCancel={onCancel}
                onOpenChart={onOpenChart}
                busy={busy}
              />
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}

/** ✕ button that cancels the given IB order id(s) at the broker. */
function CancelBtn({
  ids,
  label,
  onCancel,
  busy,
}: {
  ids: (string | null | undefined)[];
  label: string;
  onCancel: CancelHandler;
  busy: boolean;
}) {
  const clean = ids.filter((x): x is string => !!x);
  if (!clean.length) return <span className="muted">—</span>;
  return (
    <button
      className="link-btn"
      style={{ color: 'var(--red)' }}
      disabled={busy}
      title="Отменить ордер в IB"
      onClick={(e) => {
        e.stopPropagation();
        onCancel(clean, label);
      }}
    >
      ✕ Отменить
    </button>
  );
}

/** A single standalone order (one row). */
function OrderRow({
  o,
  onCancel,
  onOpenChart,
  busy,
}: {
  o: IbOrder;
  onCancel: CancelHandler;
  onOpenChart: OpenChart;
  busy: boolean;
}) {
  return (
    <tr>
      <td className="sym">
        <button
          type="button"
          className="ticker-btn"
          title={`График ${o.symbol}`}
          onClick={() =>
            onOpenChart(o.symbol, {
              side: orderSideToSide(o.side),
              entry: o.limit_price ?? o.stop_price,
            })
          }
        >
          {o.symbol}
        </button>
      </td>
      <td style={{ textAlign: 'left' }}>
        <OrderSide side={o.side} />
      </td>
      <td style={{ textAlign: 'left' }}>{o.order_type ?? '—'}</td>
      <td>{fmtNum(o.total_quantity, 0)}</td>
      <td>{o.limit_price != null ? fmtNum(o.limit_price) : '—'}</td>
      <td>{o.stop_price != null ? fmtNum(o.stop_price) : '—'}</td>
      <td>{o.tif ?? '—'}</td>
      <td style={{ textAlign: 'left' }}>
        <OrderStatus status={o.status} />
      </td>
      <td style={{ textAlign: 'right' }}>
        {cancellable(o.status) ? (
          <CancelBtn ids={[o.order_id]} label={`${o.symbol} ${o.order_type ?? ''}`} onCancel={onCancel} busy={busy} />
        ) : null}
      </td>
    </tr>
  );
}

/** A native bracket as one summary row (Лимит=цель, Стоп=защитный стоп), expandable to its legs. */
function BracketRows({
  row,
  open,
  onToggle,
  onCancel,
  onOpenChart,
  busy,
}: {
  row: BracketRow;
  open: boolean;
  onToggle: () => void;
  onCancel: CancelHandler;
  onOpenChart: OpenChart;
  busy: boolean;
}) {
  const legIds = row.legs.map((l) => l.order_id);
  const anyCancellable = row.legs.some((l) => cancellable(l.status));
  return (
    <>
      <tr className="bracket-row" onClick={onToggle} title="Нативный bracket-ордер — нажмите, чтобы раскрыть ноги">
        <td className="sym">
          <div className="bracket-sym">
            <span className="disc">{open ? '▾' : '▸'}</span>
            <button
              type="button"
              className="ticker-btn"
              title={`График ${row.symbol}`}
              onClick={(e) => {
                e.stopPropagation();
                onOpenChart(row.symbol, levelsFromBracket(row));
              }}
            >
              {row.symbol}
            </button>
            <span className="pill">BRACKET</span>
          </div>
          {row.entryPrice != null && (
            <div className="muted leg-sub">вход {fmtNum(row.entryPrice)}</div>
          )}
        </td>
        <td style={{ textAlign: 'left' }}>
          <OrderSide side={row.side} />
        </td>
        <td style={{ textAlign: 'left' }}>Брекет · {row.legs.length}</td>
        <td>{fmtNum(row.quantity, 0)}</td>
        <td>{row.target?.limit_price != null ? fmtNum(row.target.limit_price) : '—'}</td>
        <td>{row.stop?.stop_price != null ? fmtNum(row.stop.stop_price) : '—'}</td>
        <td>{row.tif ?? '—'}</td>
        <td style={{ textAlign: 'left' }}>
          <OrderStatus status={row.status} />
        </td>
        <td style={{ textAlign: 'right' }}>
          {anyCancellable ? (
            <CancelBtn ids={legIds} label={`${row.symbol} bracket`} onCancel={onCancel} busy={busy} />
          ) : null}
        </td>
      </tr>
      {open &&
        row.legs.map((leg, i) => (
          <tr className="bracket-leg" key={`${row.key}:${leg.order_id ?? i}`}>
            <td className="sym leg-name">{legRole(leg, row)}</td>
            <td style={{ textAlign: 'left' }}>
              <OrderSide side={leg.side} />
            </td>
            <td style={{ textAlign: 'left' }}>{leg.order_type ?? '—'}</td>
            <td>{fmtNum(leg.total_quantity, 0)}</td>
            <td>{leg.limit_price != null ? fmtNum(leg.limit_price) : '—'}</td>
            <td>{leg.stop_price != null ? fmtNum(leg.stop_price) : '—'}</td>
            <td>{leg.tif ?? '—'}</td>
            <td style={{ textAlign: 'left' }}>
              <OrderStatus status={leg.status} />
            </td>
            <td style={{ textAlign: 'right' }}>
              {cancellable(leg.status) ? (
                <CancelBtn ids={[leg.order_id]} label={`${row.symbol} ${legRole(leg, row)}`} onCancel={onCancel} busy={busy} />
              ) : null}
            </td>
          </tr>
        ))}
    </>
  );
}

function fmtTradeTime(t: string | null): string {
  if (!t) return '—';
  return Number.isNaN(Date.parse(t)) ? t : fmtDateTime(t);
}

function RefreshButton({ onClick, busy }: { onClick: () => void; busy: boolean }) {
  return (
    <button onClick={onClick} disabled={busy} title="Загрузить данные с IB Gateway">
      {busy ? '↻ Загрузка…' : '↻ Обновить'}
    </button>
  );
}

/**
 * "Сверить с тезисами" — runs the Telegram-free fill reconcile (sync): any IB
 * entry order that has filled flips its thesis ENTRY_READY → ACTIVE. Read-only
 * on IB; the only write is the thesis transition. Streams its log inline.
 */
function SyncFills() {
  const op = useMemoryOp(undefined, ibBracketOp);
  const running = op.state === 'running';
  return (
    <div className="field" style={{ marginBottom: 12 }}>
      <div className="btn-row">
        <button
          className="secondary"
          disabled={running}
          onClick={() => void op.run({ op: 'sync-ib-fills' })}
          title="Сверить исполнения IB с тезисами (ENTRY_READY → ACTIVE при филле входа)"
        >
          {running ? 'Сверка…' : '⟳ Сверить с тезисами'}
        </button>
      </div>
      <OpLog op={op} collapsible />
    </div>
  );
}

function OrderSide({ side }: { side: string | null }) {
  if (!side) return <span className="muted">—</span>;
  const sell = side.toUpperCase() === 'SELL';
  return (
    <span className="badge" style={{ color: sell ? 'var(--red)' : 'var(--green)' }}>
      {side.toUpperCase()}
    </span>
  );
}

function OrderStatus({ status }: { status: string | null }) {
  if (!status) return <span className="muted">—</span>;
  const s = status.toLowerCase();
  if (s.includes('fill')) return <span style={{ color: 'var(--green)' }}>{status}</span>;
  if (s.includes('cancel') || s.includes('inactive') || s.includes('reject'))
    return <span className="muted">{status}</span>;
  return <span>{status}</span>;
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
