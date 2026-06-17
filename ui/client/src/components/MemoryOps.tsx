import { useEffect, useMemo, useRef, useState } from 'react';
import type { MemoryThesis } from '@shared/types';
import { useMemoryOp, type MemoryOpRun } from '../lib/useMemoryOp';
import { GLOBAL_OPS, THESIS_OPS, type OpDef, type OpField } from '../lib/memoryOpsSchema';
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
function initialValues(def: OpDef): Values {
  return Object.fromEntries(def.fields.map((f) => [f.name, fieldDefault(f)]));
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
  onRun,
}: {
  defs: OpDef[];
  op: MemoryOpRun;
  thesisId?: string;
  thesisLabel?: string;
  onRun?: (def: OpDef) => void;
}) {
  const [opName, setOpName] = useState(defs[0].op);
  const def = useMemo(() => defs.find((d) => d.op === opName) ?? defs[0], [defs, opName]);
  const [values, setValues] = useState<Values>(() => initialValues(def));
  const running = op.state === 'running';

  useEffect(() => {
    setValues(initialValues(def));
  }, [def]);

  const missing = def.fields.some((f) => f.required && String(values[f.name] ?? '').trim() === '');

  function submit() {
    const body: Record<string, unknown> = { op: def.op };
    if (thesisId) body.thesisId = thesisId;
    for (const f of def.fields) {
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
        {def.fields.map((f) => (
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
        onRun={(def) => {
          closeRef.current = def.op === 'delete';
        }}
      />
    </div>
  );
}
