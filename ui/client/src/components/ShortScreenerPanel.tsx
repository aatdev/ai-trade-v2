import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { ShortScreenerRunRequest } from '@shared/types';
import { runShortScreener, useStagedShortScreener, type Refetch } from '../api';
import { useJobStream } from '../lib/useJobStream';
import ShortScreenerHelpModal from './ShortScreenerHelpModal';
import ShortScreenerParamForm, {
  SHORT_DEFAULT_FORM,
  type ShortFormState,
} from './ShortScreenerParamForm';
import ShortScreenerResults from './ShortScreenerResults';
import { Card, Empty, ErrorNote, Loading } from './ui';

const numOr = (s: string): number | undefined => {
  const t = s.trim();
  if (!t) return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
};
const parseSymbols = (s: string): string[] =>
  s
    .split(/[\s,;]+/)
    .map((x) => x.trim().toUpperCase())
    .filter(Boolean);

export default function ShortScreenerPanel({
  date,
  refetch,
}: {
  date: string | null;
  refetch: Refetch;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<ShortFormState>(SHORT_DEFAULT_FORM);
  const [helpOpen, setHelpOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(true);
  const didInitUniverse = useRef(false);
  const logRef = useRef<HTMLPreElement>(null);

  const stream = useJobStream({
    onEnd: () => void qc.invalidateQueries({ queryKey: ['stagedShortScreener'] }),
  });
  const running = stream.state === 'running';

  // Pause polling while a job runs (we refetch explicitly on the SSE `end`).
  const { data: staged, isLoading, error } = useStagedShortScreener({}, running ? false : refetch);

  // Default the universe to the wide NASDAQ+NYSE file when it exists (one-shot).
  useEffect(() => {
    if (didInitUniverse.current || !staged) return;
    didInitUniverse.current = true;
    if (staged.wideUniverse?.available) setForm((f) => ({ ...f, universe: 'wide' }));
  }, [staged]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [stream.lines]);

  const screener = staged?.screener ?? null;
  const gate = staged?.gate?.data ?? null;

  function doRun() {
    setLogOpen(true);
    const dollarVolM = numOr(form.minDollarVolM);
    const body: ShortScreenerRunRequest = {
      universe: form.universe,
      symbols: form.universe === 'custom' ? parseSymbols(form.symbolsText) : undefined,
      minGrade: form.minGrade,
      top: numOr(form.top),
      rsLookback: numOr(form.rsLookback),
      maxCandidates: numOr(form.maxCandidates),
      minPrice: numOr(form.minPrice),
      minDollarVol: dollarVolM != null ? Math.round(dollarVolM * 1e6) : undefined,
      minStopPct: numOr(form.minStopPct),
      maxStopPct: numOr(form.maxStopPct),
    };
    void stream.run(() => runShortScreener(body));
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Card
        title="Swing-short — параметры"
        sourceSelect={
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 12 }}>
            {gate ? (
              <span
                className="muted"
                style={{ textTransform: 'none', letterSpacing: 0, fontSize: 11 }}
              >
                Гейт: <b style={{ color: 'var(--text)' }}>{gate.decision}</b> · шорты — для слабого
                рынка
              </span>
            ) : null}
            <button onClick={() => setHelpOpen(true)} title="Как работать со скринером шортов">
              ❓ Документация
            </button>
          </span>
        }
      >
        <ShortScreenerParamForm
          form={form}
          onChange={setForm}
          disabled={running}
          wideUniverse={staged?.wideUniverse}
        />
        <div className="btn-row">
          <button className="primary" disabled={running} onClick={doRun}>
            {running ? 'Скрин…' : '▶ Запустить скрин'}
          </button>
          {running ? (
            <button className="danger" onClick={() => void stream.cancel()}>
              Отмена
            </button>
          ) : null}
          {running ? <span className="muted">{stream.elapsed}s</span> : null}
        </div>

        {staged?.notes?.map((n, i) => (
          <div key={i} className="hint">
            ⓘ {n}
          </div>
        ))}
        {stream.error ? (
          <div className="err" style={{ marginTop: 8 }}>
            {stream.error}
          </div>
        ) : null}
        {stream.lines.length > 0 || running || stream.state !== 'idle' ? (
          <div>
            <div className="collapse-head" onClick={() => setLogOpen((o) => !o)}>
              {logOpen ? '▾' : '▸'} Лог выполнения
              {running
                ? ` · ${stream.elapsed}s`
                : stream.state !== 'idle'
                  ? ` · ${stream.state}`
                  : ''}
            </div>
            {logOpen ? (
              <pre className="joblog" ref={logRef} style={{ marginTop: 8 }}>
                {stream.lines.length ? stream.lines.join('\n') : '(пока нет вывода)'}
              </pre>
            ) : null}
          </div>
        ) : null}
      </Card>

      <Card title="Кандидаты (Stage 4 слабость)" source={staged?.source ?? undefined}>
        {isLoading ? (
          <Loading />
        ) : error ? (
          <ErrorNote error={error} />
        ) : screener ? (
          <ShortScreenerResults screener={screener} date={date} />
        ) : (
          <Empty>
            Запусти скрин — кандидаты появятся здесь. Detection-only, ничего не регистрируется.
          </Empty>
        )}
      </Card>

      {helpOpen ? <ShortScreenerHelpModal onClose={() => setHelpOpen(false)} /> : null}
    </div>
  );
}
