import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { MemoryThesis } from '@shared/types';
import { useMemory, deleteTheses, type Refetch } from '../api';
import { useMemoryOp } from '../lib/useMemoryOp';
import { fmtMoney, fmtNum, fmtSignedPct } from '../lib/format';
import { MemoryOpsModal, ThesisOps } from './MemoryOps';
import SkillDocModal from './SkillDocModal';
import { Card, Empty, ErrorNote, Loading, Modal } from './ui';

/* ---------------- coercion helpers (records are Record<string, unknown>) ---------------- */
function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}
function str(v: unknown): string | null {
  return typeof v === 'string' && v.length > 0 ? v : null;
}
function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

const STATUS_COLOR: Record<string, string> = {
  IDEA: 'var(--muted)',
  ENTRY_READY: 'var(--accent)',
  ACTIVE: 'var(--green)',
  PARTIALLY_CLOSED: 'var(--yellow)',
  CLOSED: 'var(--pink)',
  INVALIDATED: 'var(--red)',
};
const STATUS_ORDER: Record<string, number> = {
  ACTIVE: 0,
  PARTIALLY_CLOSED: 1,
  ENTRY_READY: 2,
  IDEA: 3,
  CLOSED: 4,
  INVALIDATED: 5,
};
// States whose theses may be bulk-deleted from the table (never positions/closed).
const DELETABLE = new Set(['IDEA', 'ENTRY_READY', 'INVALIDATED']);

function StatusBadge({ status }: { status: string }) {
  return (
    <span className="badge" style={{ color: STATUS_COLOR[status] ?? 'var(--muted)' }}>
      {status}
    </span>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="kv-row">
      <div className="kv-k">{k}</div>
      <div className="kv-v">{children}</div>
    </div>
  );
}

/* ---------------- detail modal ---------------- */

function ThesisDetailModal({ t, onClose }: { t: MemoryThesis; onClose: () => void }) {
  const entry = rec(t.entry);
  const exit = rec(t.exit);
  const mon = rec(t.monitoring);
  const out = rec(t.outcome);
  const origin = rec(t.origin);
  const pos = rec(t.raw.position);
  const mkt = rec(t.raw.market_context);
  const history = arr(t.raw.status_history);
  const evidence = arr(t.raw.evidence).map(String).filter(Boolean);
  const kill = arr(t.raw.kill_criteria).map(String).filter(Boolean);
  const triggers = arr(mon.triggers_config);
  const alerts = arr(mon.alerts).map(String).filter(Boolean);
  const linked = arr(t.raw.linked_reports);
  const pnl = num(out.pnl_dollars);

  return (
    <Modal
      title={
        <>
          {t.ticker} <StatusBadge status={t.status} />{' '}
          <span className="muted" style={{ fontSize: 13 }}>
            {t.thesis_type ?? '—'}
          </span>
        </>
      }
      onClose={onClose}
      footer={
        <>
          <Link to={`/ticker/${t.ticker}`} className="back-link">
            Открыть тикер ↗
          </Link>
          <button onClick={onClose}>Закрыть</button>
        </>
      }
    >
      {t.thesis_statement ? (
        <p style={{ marginTop: 0 }}>{t.thesis_statement}</p>
      ) : null}

      <div className="kv">
        {t.setup_type ? <Row k="Сетап">{t.setup_type}</Row> : null}
        {str(t.raw.catalyst) ? <Row k="Катализатор">{str(t.raw.catalyst)}</Row> : null}
        {str(t.raw.mechanism_tag) ? <Row k="Механизм">{str(t.raw.mechanism_tag)}</Row> : null}
        {str(t.raw.confidence) || num(t.raw.confidence_score) != null ? (
          <Row k="Уверенность">
            {str(t.raw.confidence) ?? '—'}
            {num(t.raw.confidence_score) != null ? ` (${fmtNum(num(t.raw.confidence_score), 2)})` : ''}
          </Row>
        ) : null}
        <Row k="Вход">
          цель {fmtNum(num(entry.target_price))}
          {num(entry.actual_price) != null ? ` · факт ${fmtNum(num(entry.actual_price))}` : ''}
        </Row>
        <Row k="Выход">
          стоп {fmtNum(num(exit.stop_loss))} · тейк {fmtNum(num(exit.take_profit))}
          {num(exit.take_profit_rr) != null ? ` (RR ${fmtNum(num(exit.take_profit_rr), 1)})` : ''}
          {str(exit.exit_reason) ? ` · ${str(exit.exit_reason)}` : ''}
        </Row>
        {num(pos.shares) != null ? (
          <Row k="Позиция">
            {fmtNum(num(pos.shares), 0)} шт · риск {fmtMoney(num(pos.risk_dollars))}
            {num(pos.position_value) != null ? ` · объём ${fmtMoney(num(pos.position_value))}` : ''}
          </Row>
        ) : null}
        {str(mkt.regime) || num(mkt.breadth_score) != null ? (
          <Row k="Рынок">
            {str(mkt.regime) ?? '—'}
            {num(mkt.breadth_score) != null ? ` · breadth ${fmtNum(num(mkt.breadth_score), 0)}` : ''}
            {str(mkt.sector) ? ` · ${str(mkt.sector)}` : ''}
          </Row>
        ) : null}
        <Row k="Ревью">
          {str(mon.review_status) ?? 'OK'} · след. {str(mon.next_review_date) ?? '—'}
          {t.review_due ? <span className="review-due"> ⚠ просрочено</span> : ''}
          {num(mon.review_interval_days) != null ? ` · интервал ${num(mon.review_interval_days)} дн` : ''}
        </Row>
        {origin.skill ? (
          <Row k="Источник">
            {String(origin.skill)}
            {str(origin.screening_grade) ? ` · grade ${str(origin.screening_grade)}` : ''}
          </Row>
        ) : null}
      </div>

      {pnl != null || num(out.mae_pct) != null || str(out.lessons_learned) ? (
        <div className="field">
          <strong>Итог</strong>
          <div className="kv">
            {pnl != null ? (
              <Row k="P&L">
                <span style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                  {fmtMoney(pnl)} ({fmtSignedPct(num(out.pnl_pct))})
                </span>
                {num(out.holding_days) != null ? ` · ${num(out.holding_days)} дн` : ''}
              </Row>
            ) : null}
            {num(out.mae_pct) != null || num(out.mfe_pct) != null ? (
              <Row k="MAE / MFE">
                {fmtSignedPct(num(out.mae_pct))} / {fmtSignedPct(num(out.mfe_pct))}
                {str(out.mae_mfe_source) ? ` · ${str(out.mae_mfe_source)}` : ''}
              </Row>
            ) : null}
            {str(out.lessons_learned) ? <Row k="Уроки">{str(out.lessons_learned)}</Row> : null}
          </div>
        </div>
      ) : null}

      {evidence.length > 0 ? (
        <div className="field">
          <strong>Подтверждения</strong>
          <ul className="bullets">
            {evidence.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {kill.length > 0 ? (
        <div className="field">
          <strong>Критерии отмены</strong>
          <ul className="bullets">
            {kill.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {triggers.length > 0 || alerts.length > 0 ? (
        <div className="field">
          <strong>Мониторинг</strong>
          <ul className="bullets">
            {triggers.map((tr, i) => {
              const o = rec(tr);
              return (
                <li key={`t${i}`}>
                  {str(o.trigger) ?? '—'}
                  {str(o.description) ? ` — ${str(o.description)}` : ''}
                </li>
              );
            })}
            {alerts.map((a, i) => (
              <li key={`a${i}`}>{a}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {history.length > 0 ? (
        <div className="field">
          <strong>История статусов</strong>
          <ul className="bullets">
            {history.map((h, i) => {
              const o = rec(h);
              return (
                <li key={i}>
                  <StatusBadge status={String(o.status ?? '?')} />{' '}
                  <span className="muted">{str(o.at)?.slice(0, 10) ?? ''}</span>
                  {str(o.reason) ? ` — ${str(o.reason)}` : ''}
                  {num(o.realized_pnl) != null ? ` (P&L ${fmtMoney(num(o.realized_pnl))})` : ''}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {linked.length > 0 ? (
        <div className="field">
          <strong>Связанные отчёты</strong>
          <ul className="bullets">
            {linked.map((l, i) => {
              const o = rec(l);
              return (
                <li key={i}>
                  {String(o.skill ?? '?')} · {str(o.date) ?? ''} · <code>{String(o.file ?? '')}</code>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      <ThesisOps thesis={t} onClose={onClose} />
    </Modal>
  );
}

/* ---------------- card ---------------- */

export default function TraderMemoryCard({ refetch }: { refetch: Refetch }) {
  const { data, isLoading, error } = useMemory(refetch);
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const [sel, setSel] = useState<MemoryThesis | null>(null);
  const [docsOpen, setDocsOpen] = useState(false);
  const [opsOpen, setOpsOpen] = useState(false);
  const [selIds, setSelIds] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);

  // Bulk delete reuses the memory job runner (SSE + ['memory']/['theses'] refresh).
  const del = useMemoryOp(
    () => {
      setSelIds(new Set());
      setConfirming(false);
    },
    (body) => deleteTheses((body.ids as string[]) ?? []),
  );

  const toggleId = (id: string, on: boolean) =>
    setSelIds((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const theses = useMemo(() => {
    let list = [...(data?.theses ?? [])];
    if (status) list = list.filter((t) => t.status === status);
    const needle = q.trim().toUpperCase();
    if (needle) list = list.filter((t) => t.ticker.toUpperCase().includes(needle));
    list.sort((a, b) => {
      if (a.review_due !== b.review_due) return a.review_due ? -1 : 1;
      return (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9);
    });
    return list;
  }, [data, status, q]);

  const deletableVisible = useMemo(
    () => theses.filter((t) => DELETABLE.has(t.status)).map((t) => t.id),
    [theses],
  );
  const allDeletableSelected =
    deletableVisible.length > 0 && deletableVisible.every((id) => selIds.has(id));

  const headerBtns = (
    <div className="btn-row">
      <button className="link-btn" onClick={() => setOpsOpen(true)} title="Операции над памятью">
        ⚙ Операции
      </button>
      <button className="link-btn" onClick={() => setDocsOpen(true)} title="Документация скила">
        📖 Документация
      </button>
    </div>
  );

  if (isLoading)
    return (
      <Card title="Trader Memory" className="full">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Trader Memory" className="full">
        <ErrorNote error={error} />
      </Card>
    );

  const s = data?.summary;
  const winRate = s && s.closed > 0 ? Math.round((s.wins / s.closed) * 100) : null;
  const statuses = Object.keys(s?.byStatus ?? {}).sort(
    (a, b) => (STATUS_ORDER[a] ?? 9) - (STATUS_ORDER[b] ?? 9),
  );

  return (
    <Card title="Trader Memory" className="full">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div className="stats" style={{ gap: 18 }}>
          <div className="stat">
            <div className="k">Тезисов</div>
            <div className="v">{s?.total ?? 0}</div>
          </div>
          <div className="stat">
            <div className="k">Активных</div>
            <div className="v" style={{ color: 'var(--green)' }}>{s?.active ?? 0}</div>
          </div>
          <div className="stat">
            <div className="k">Ревью</div>
            <div className="v" style={{ color: s && s.reviewDue > 0 ? 'var(--orange)' : undefined }}>
              {s?.reviewDue ?? 0}
            </div>
          </div>
          <div className="stat">
            <div className="k">Закрытых</div>
            <div className="v">
              {s?.closed ?? 0}
              {winRate != null ? <span className="muted" style={{ fontSize: 13 }}> · {winRate}% win</span> : null}
            </div>
          </div>
          {s?.realizedPnl != null ? (
            <div className="stat">
              <div className="k">Реализ. P&L</div>
              <div className="v" style={{ color: s.realizedPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {fmtMoney(s.realizedPnl)}
              </div>
            </div>
          ) : null}
        </div>
        {headerBtns}
      </div>

      {(data?.theses.length ?? 0) === 0 ? (
        <Empty>Тезисов пока нет. Зарегистрируй их через trader-memory-core (ingest).</Empty>
      ) : (
        <>
          <div className="control" style={{ marginBottom: 10, gap: 12 }}>
            Статус
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">все ({s?.total ?? 0})</option>
              {statuses.map((st) => (
                <option key={st} value={st}>
                  {st} ({s?.byStatus[st]})
                </option>
              ))}
            </select>
            Тикер
            <input
              placeholder="фильтр…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ width: 100 }}
            />
            {status || q ? (
              <button className="link-btn" onClick={() => (setStatus(''), setQ(''))}>
                clear
              </button>
            ) : null}
          </div>

          {deletableVisible.length > 0 ? (
            <div className="control" style={{ marginBottom: 10, gap: 12 }}>
              {!confirming ? (
                <button
                  className="link-btn"
                  disabled={selIds.size === 0 || del.state === 'running'}
                  onClick={() => setConfirming(true)}
                  title="Удалить выбранные тезисы (только IDEA / ENTRY_READY / INVALIDATED)"
                >
                  🗑 Удалить выбранные ({selIds.size})
                </button>
              ) : (
                <>
                  <span>Удалить {selIds.size} тезис(ов) безвозвратно?</span>
                  <button
                    className="link-btn"
                    style={{ color: 'var(--red)' }}
                    disabled={del.state === 'running'}
                    onClick={() => void del.run({ ids: [...selIds] })}
                  >
                    Да, удалить
                  </button>
                  <button className="link-btn" onClick={() => setConfirming(false)}>
                    Отмена
                  </button>
                </>
              )}
              {del.state === 'running' ? <span className="muted">удаление…</span> : null}
              {del.state === 'error' ? <span style={{ color: 'var(--red)' }}>{del.error}</span> : null}
            </div>
          ) : null}

          <div className="scroll-x">
            <table className="rows-clickable">
              <thead>
                <tr>
                  <th style={{ width: 28 }}>
                    <input
                      type="checkbox"
                      aria-label="выбрать все удаляемые"
                      disabled={deletableVisible.length === 0}
                      checked={allDeletableSelected}
                      onChange={(e) =>
                        setSelIds((prev) => {
                          const next = new Set(prev);
                          for (const id of deletableVisible) {
                            if (e.target.checked) next.add(id);
                            else next.delete(id);
                          }
                          return next;
                        })
                      }
                    />
                  </th>
                  <th>Тикер</th>
                  <th style={{ textAlign: 'left' }}>Статус</th>
                  <th style={{ textAlign: 'left' }}>Тип</th>
                  <th>Вход</th>
                  <th>Стоп</th>
                  <th>Тейк</th>
                  <th>P&L</th>
                  <th>Ревью</th>
                </tr>
              </thead>
              <tbody>
                {theses.map((t) => {
                  const entry = rec(t.entry);
                  const exit = rec(t.exit);
                  const out = rec(t.outcome);
                  const pnl = num(out.pnl_dollars);
                  return (
                    <tr key={t.id} onClick={() => setSel(t)}>
                      <td onClick={(e) => e.stopPropagation()} style={{ textAlign: 'center' }}>
                        {DELETABLE.has(t.status) ? (
                          <input
                            type="checkbox"
                            aria-label={`выбрать ${t.ticker}`}
                            checked={selIds.has(t.id)}
                            onChange={(e) => toggleId(t.id, e.target.checked)}
                          />
                        ) : null}
                      </td>
                      <td className="sym">
                        <Link to={`/ticker/${t.ticker}`} onClick={(e) => e.stopPropagation()}>
                          {t.ticker}
                        </Link>
                      </td>
                      <td style={{ textAlign: 'left' }}>
                        <StatusBadge status={t.status} />
                      </td>
                      <td style={{ textAlign: 'left' }} className="muted">
                        {t.thesis_type ?? '—'}
                      </td>
                      <td>{fmtNum(num(entry.actual_price) ?? num(entry.target_price))}</td>
                      <td>{fmtNum(num(exit.stop_loss))}</td>
                      <td>{fmtNum(num(exit.take_profit))}</td>
                      <td style={{ color: pnl == null ? undefined : pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {pnl == null ? '—' : fmtSignedPct(num(out.pnl_pct))}
                      </td>
                      <td className={t.review_due ? 'review-due' : 'muted'}>
                        {t.next_review_date ?? '—'}
                        {t.review_due ? ' ⚠' : ''}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {sel ? <ThesisDetailModal t={sel} onClose={() => setSel(null)} /> : null}
      {opsOpen ? <MemoryOpsModal onClose={() => setOpsOpen(false)} /> : null}
      {docsOpen ? (
        <SkillDocModal skill="trader-memory-core" title="Trader Memory Core" onClose={() => setDocsOpen(false)} />
      ) : null}
    </Card>
  );
}
