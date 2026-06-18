import { useEffect, useMemo, useRef, useState } from 'react';
import type { MemoryThesis, TradingProfile } from '@shared/types';
import { useMemoryOp, type MemoryOpRun } from '../lib/useMemoryOp';
import { GLOBAL_OPS, THESIS_OPS, forwardStatuses, type OpDef, type OpField } from '../lib/memoryOpsSchema';
import { ibBracketOp, useIbSnapshot, useProfile } from '../api';
import { isLimit, isStop, rowsForThesis, type BracketRow } from '../lib/ibBrackets';
import { Modal } from './ui';

type Values = Record<string, string | boolean>;

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}
function fieldDefault(f: OpField): string | boolean {
  if (f.type === 'checkbox') return false;
  if (f.default === '@today') return todayISO();
  return f.default ?? '';
}
function initialValues(fields: OpField[]): Values {
  return Object.fromEntries(fields.map((f) => [f.name, fieldDefault(f)]));
}

/** Resolve a field's effective options/default for the open thesis. A
 *  forward-status select is restricted to states reachable forward — the thesis
 *  state machine is forward-only, so a status can never be moved back. */
function resolveField(f: OpField, thesisStatus?: string): OpField {
  if (f.dynamicOptions === 'forwardStatus' && thesisStatus) {
    const opts = forwardStatuses(thesisStatus);
    return { ...f, options: opts, default: opts[0] ?? '' };
  }
  return f;
}

export function OpLog({ op, collapsible = false }: { op: MemoryOpRun; collapsible?: boolean }) {
  const logRef = useRef<HTMLPreElement>(null);
  const [open, setOpen] = useState(true);
  // Re-open on each new run so live progress is visible even after a collapse.
  useEffect(() => {
    if (op.state === 'running') setOpen(true);
  }, [op.state]);
  useEffect(() => {
    if (open) logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [op.lines, open]);
  if (op.state === 'idle') return null;
  const color =
    op.state === 'done' ? 'var(--green)' : op.state === 'running' ? 'var(--accent)' : 'var(--orange)';
  const statusText = op.state === 'running' ? 'выполняется…' : op.state;
  const hasBody = op.lines.length > 0 || op.state === 'running';
  const showBody = hasBody && (!collapsible || open);
  return (
    <div className="field">
      {collapsible ? (
        <button
          className="link-btn"
          onClick={() => setOpen((v) => !v)}
          style={{ padding: 0, fontWeight: 600, alignSelf: 'flex-start', textAlign: 'left' }}
          title={open ? 'Свернуть лог' : 'Развернуть лог'}
        >
          {hasBody ? (open ? '▾' : '▸') : '•'} Лог — <span style={{ color }}>{statusText}</span>
        </button>
      ) : (
        <strong>
          Лог — <span style={{ color }}>{statusText}</span>
        </strong>
      )}
      {op.error ? <div className="err" style={{ marginBottom: 6 }}>{op.error}</div> : null}
      {showBody ? (
        <pre className="joblog" ref={logRef}>
          {op.lines.length
            ? op.lines.map((l, i) => (
                <div key={i} className={l.stream}>
                  {l.line}
                </div>
              ))
            : '(no output yet)'}
        </pre>
      ) : null}
    </div>
  );
}

function FieldInput({
  f,
  value,
  onChange,
  disabled,
}: {
  f: OpField;
  value: string | boolean;
  onChange: (v: string | boolean) => void;
  disabled: boolean;
}) {
  if (f.type === 'checkbox') {
    return (
      <label className="check">
        <input type="checkbox" checked={!!value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        {f.label}
      </label>
    );
  }
  if (f.type === 'select') {
    return (
      <label className="field-inline">
        <span className="muted">{f.label}</span>
        <select value={String(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
          {(f.options ?? []).map((o) => (
            <option key={o} value={o}>
              {o === '' ? '(любой)' : o}
            </option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label className="field-inline">
      <span className="muted">{f.label}</span>
      <input
        type={f.type === 'date' ? 'date' : f.type === 'number' ? 'number' : 'text'}
        value={String(value)}
        disabled={disabled}
        placeholder={f.placeholder ?? ''}
        onChange={(e) => onChange(e.target.value)}
        style={{ width: f.type === 'text' ? 200 : 150 }}
      />
    </label>
  );
}

/** A single op picker + its form fields + Run, sharing the given op runner. */
function OpRunner({
  defs,
  op,
  thesisId,
  thesisLabel,
  thesisStatus,
  onRun,
}: {
  defs: OpDef[];
  op: MemoryOpRun;
  thesisId?: string;
  thesisLabel?: string;
  thesisStatus?: string;
  onRun?: (def: OpDef) => void;
}) {
  const [opName, setOpName] = useState(defs[0].op);
  const def = useMemo(() => defs.find((d) => d.op === opName) ?? defs[0], [defs, opName]);
  // Fields with their options/default resolved for the open thesis (e.g. the
  // status select restricted to forward-only transitions).
  const fields = useMemo(
    () => def.fields.map((f) => resolveField(f, thesisStatus)),
    [def, thesisStatus],
  );
  const [values, setValues] = useState<Values>(() => initialValues(fields));
  const running = op.state === 'running';

  useEffect(() => {
    setValues(initialValues(fields));
  }, [fields]);

  const missing = fields.some((f) => f.required && String(values[f.name] ?? '').trim() === '');

  function submit() {
    const body: Record<string, unknown> = { op: def.op };
    if (thesisId) body.thesisId = thesisId;
    for (const f of fields) {
      const v = values[f.name];
      if (f.type === 'checkbox') {
        if (v === true) body[f.name] = true;
      } else if (String(v).trim() !== '') {
        body[f.name] = String(v).trim();
      }
    }
    if (def.confirm) {
      const what = thesisLabel ? `${def.label} — ${thesisLabel}` : def.label;
      if (!window.confirm(`${what}. Выполнить?`)) return;
    }
    onRun?.(def);
    void op.run(body);
  }

  return (
    <div className="field">
      <div className="btn-row" style={{ alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <label className="field-inline">
          <span className="muted">Операция</span>
          <select value={opName} disabled={running} onChange={(e) => setOpName(e.target.value)}>
            {defs.map((d) => (
              <option key={d.op} value={d.op}>
                {d.label}
              </option>
            ))}
          </select>
        </label>
        {fields.map((f) => (
          <FieldInput
            key={f.name}
            f={f}
            value={values[f.name] ?? ''}
            disabled={running}
            onChange={(v) => setValues((prev) => ({ ...prev, [f.name]: v }))}
          />
        ))}
        <button className={def.danger ? 'danger' : 'primary'} disabled={running || missing} onClick={submit}>
          {running ? 'Выполняется…' : def.danger ? 'Удалить' : 'Выполнить'}
        </button>
      </div>
      {def.hint ? <span className="hint">{def.hint}</span> : null}
      {fields.some((f) => f.dynamicOptions === 'forwardStatus' && (f.options ?? []).length === 0) ? (
        <span className="hint">
          Прямых переходов нет (статус только вперёд): для ACTIVE — «Открыть позицию», для
          частичного — «Частично закрыть», для выхода — «Закрыть» / «Завершить».
        </span>
      ) : null}
      <OpLog op={op} />
    </div>
  );
}

/* ---------------- global ops (card header → modal) ---------------- */

export function MemoryOpsModal({ onClose }: { onClose: () => void }) {
  const op = useMemoryOp();
  return (
    <Modal title="⚙ Операции памяти" onClose={onClose} footer={<button onClick={onClose}>Закрыть</button>}>
      <OpRunner defs={GLOBAL_OPS} op={op} />
    </Modal>
  );
}

/* ---------------- per-thesis ops (detail modal) ---------------- */

export function ThesisOps({ thesis, onClose }: { thesis: MemoryThesis; onClose: () => void }) {
  const closeRef = useRef(false);
  const op = useMemoryOp((status) => {
    if (closeRef.current && status === 'done') onClose();
  });
  return (
    <div className="field" style={{ borderTop: '1px solid var(--border)', paddingTop: 12 }}>
      <strong>Операции</strong>
      <OpRunner
        defs={THESIS_OPS}
        op={op}
        thesisId={thesis.id}
        thesisLabel={`${thesis.ticker} (${thesis.status})`}
        thesisStatus={thesis.status}
        onRun={(def) => {
          closeRef.current = def.op === 'delete';
        }}
      />
      {thesis.status === 'ENTRY_READY' ? <IbBracketOps thesis={thesis} /> : null}
    </div>
  );
}

/* ---------------- IB bracket placement (ENTRY_READY thesis) ---------------- */

function num(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  const s = String(v ?? '').trim();
  if (s === '') return null; // undefined / null / empty → null (NOT 0)
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}
function asRec(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/** Risk-based shares from the profile — mirrors watchlist_orders auto-sizing. */
function computeShares(
  profile: TradingProfile | null | undefined,
  entry: number | null,
  stop: number | null,
): number | null {
  if (!profile || entry == null || stop == null) return null;
  const account = Number(profile.account_size) || 0;
  const dist = Math.abs(entry - stop);
  if (account <= 0 || entry <= 0 || dist <= 0) return null;
  const riskPct = Number(profile.risk_pct) || 1;
  const capPct = Number(profile.max_position_pct) || 25;
  const shares = Math.min(
    Math.floor((account * riskPct) / 100 / dist),
    Math.floor((account * capPct) / 100 / entry),
  );
  return shares > 0 ? shares : null;
}

const IB_FIELDS = [
  { key: 'quantity', body: 'quantity', label: 'Кол-во акций' },
  { key: 'entry', body: 'entryPrice', label: 'Вход T1' },
  { key: 'stop', body: 'stopPrice', label: 'Стоп' },
  { key: 't1', body: 'targetPrice', label: 'Цель T1' },
  { key: 't2', body: 'target2', label: 'Цель T2' },
  { key: 't3', body: 'target3', label: 'Цель T3' },
] as const;

/**
 * Place / cancel a native IB bracket for an ENTRY_READY thesis. The fields show
 * the COMPUTED values that would be placed (thesis levels + auto-sized shares);
 * once a live bracket exists for this thesis they switch to the CURRENT order
 * values and the "Поставить" button is disabled (cancel instead).
 */
function IbBracketOps({ thesis }: { thesis: MemoryThesis }) {
  const { data: profile } = useProfile();
  const { data: ib } = useIbSnapshot();
  const op = useMemoryOp(undefined, ibBracketOp);
  const [override, setOverride] = useState<Record<string, string>>({});

  const e = asRec(thesis.entry);
  const x = asRec(thesis.exit);
  const raw = asRec(thesis.raw);
  const pos = asRec(raw.position);
  const long = String(raw.side ?? 'long').toLowerCase() !== 'short';

  // Live bracket(s) for this thesis. The thesis id lives in the bracket PARENT's
  // cOID only; child stop/take legs link by the parent's numeric order_id, so we
  // group the FULL order list and keep the rows that belong to this thesis —
  // that pulls the children in (a bare parent-id filter would miss them and the
  // value fields would render empty). A 50/25/25 scale-out is N independent
  // brackets, so this can be several rows; dead (cancelled/…) rows are dropped.
  const allOrders = ib?.orders ?? [];
  const liveRows = useMemo(
    () =>
      rowsForThesis(allOrders, thesis.id).filter(
        (r): r is BracketRow => r.kind === 'bracket',
      ),
    [allOrders, thesis.id],
  );
  const placed = liveRows.length > 0;
  // One take-profit per sub-bracket → T1/T2/T3 are the takes across all rows,
  // ordered by price (ascending for a long, descending for a short).
  const liveTakes = liveRows
    .flatMap((r) => r.legs)
    .filter((l) => isLimit(l) && !isStop(l))
    .sort((a, b) => ((a.limit_price ?? 0) - (b.limit_price ?? 0)) * (long ? 1 : -1));
  // Full size = sum of the per-tranche take quantities (each tranche's take
  // carries that tranche's size); falls back to the first row's quantity.
  const liveQty =
    liveTakes.reduce((s, l) => s + (l.total_quantity ?? 0), 0) || liveRows[0]?.quantity || null;
  const firstRow = liveRows[0];

  const computedEntry = num(e.target_price);
  const computedStop = num(x.stop_loss);
  const shown: Record<string, number | null> = placed
    ? {
        quantity: liveQty,
        entry: firstRow?.entryPrice ?? null,
        stop: firstRow?.stop?.stop_price ?? null,
        t1: liveTakes[0]?.limit_price ?? null,
        t2: liveTakes[1]?.limit_price ?? null,
        t3: liveTakes[2]?.limit_price ?? null,
      }
    : {
        quantity: num(pos.shares) ?? computeShares(profile, computedEntry, computedStop),
        entry: computedEntry,
        stop: computedStop,
        t1: num(x.take_profit),
        t2: num(x.take_profit_2),
        t3: num(x.take_profit_3),
      };

  const running = op.state === 'running';
  const valOf = (key: string): string =>
    override[key] ?? (shown[key] != null ? String(shown[key]) : '');

  function place() {
    const body: Record<string, unknown> = { op: 'place-ib-bracket', thesisId: thesis.id };
    for (const f of IB_FIELDS) {
      const v = valOf(f.key).trim();
      if (v !== '') body[f.body] = v;
    }
    if (!window.confirm(`Поставить IB-bracket для ${thesis.ticker}? Реальный ордер при IB_ALLOW_ORDER_PLACEMENT.`))
      return;
    void op.run(body);
  }
  function cancel() {
    if (!window.confirm(`Отменить выставленный IB-bracket для ${thesis.ticker}?`)) return;
    void op.run({ op: 'cancel-ib-bracket', thesisId: thesis.id });
  }

  return (
    <div className="field" style={{ borderTop: '1px dashed var(--border)', paddingTop: 12 }}>
      <strong>
        IB-ордера{' '}
        {placed ? (
          <span className="badge" style={{ color: 'var(--green)' }}>
            выставлен
          </span>
        ) : null}
      </strong>
      <div className="btn-row" style={{ alignItems: 'flex-end', flexWrap: 'wrap' }}>
        {IB_FIELDS.map((f) => (
          <label key={f.key} className="field-inline">
            <span className="muted">{f.label}</span>
            <input
              type="number"
              value={valOf(f.key)}
              disabled={running || placed}
              onChange={(ev) => setOverride((p) => ({ ...p, [f.key]: ev.target.value }))}
              style={{ width: 110 }}
            />
          </label>
        ))}
        <button className="primary" disabled={running || placed} onClick={place}>
          {running ? 'Выполняется…' : 'Поставить'}
        </button>
        {placed ? (
          <button className="danger" disabled={running} onClick={cancel}>
            Отменить
          </button>
        ) : null}
      </div>
      <span className="hint">
        {placed
          ? 'Ордер уже выставлен — показаны текущие значения. Чтобы изменить — отмените и поставьте заново.'
          : 'Показаны расчётные значения (уровни тезиса + размер по профилю). Если заданы T2 и T3 — размер дробится 50/25/25. Реальный ордер только при IB_ALLOW_ORDER_PLACEMENT=true в .env.'}
      </span>
      <OpLog op={op} collapsible />
    </div>
  );
}
