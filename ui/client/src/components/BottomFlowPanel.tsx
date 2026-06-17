import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { BottomFlowRunRequest } from '@shared/types';
import { runBottomFlowScreener, useStagedBottomFlow, type Refetch } from '../api';
import { useJobStream } from '../lib/useJobStream';
import { usePersistentForm } from '../lib/usePersistentForm';
import BottomFlowHelpModal from './BottomFlowHelpModal';
import BottomFlowParamForm, { BOTTOM_FLOW_DEFAULT_FORM } from './BottomFlowParamForm';
import BottomFlowResults from './BottomFlowResults';
import ScreenerParamActions from './ScreenerParamActions';
import { Card, Empty, ErrorNote, Loading } from './ui';

const numOr = (s: string): number | undefined => {
  const t = s.trim();
  if (!t) return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
};

export default function BottomFlowPanel({
  date,
  refetch,
}: {
  date: string | null;
  refetch: Refetch;
}) {
  const qc = useQueryClient();
  const { form, setForm, save, reset, saved, dirty } = usePersistentForm(
    'bottomFlow',
    BOTTOM_FLOW_DEFAULT_FORM,
  );
  const [helpOpen, setHelpOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(true);
  const logRef = useRef<HTMLPreElement>(null);

  const stream = useJobStream({
    onEnd: () => void qc.invalidateQueries({ queryKey: ['stagedBottomFlow'] }),
  });
  const running = stream.state === 'running';

  // Pause polling while a job runs (we refetch explicitly on the SSE `end`).
  const { data: staged, isLoading, error } = useStagedBottomFlow({}, running ? false : refetch);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [stream.lines]);

  const screener = staged?.screener ?? null;
  const gate = staged?.gate?.data ?? null;
  const grades = [
    form.gradeA && 'A',
    form.gradeBaccum && 'B-accum',
    form.gradeBfund && 'B-fund',
  ].filter(Boolean) as string[];
  const noGrade = grades.length === 0;

  function doRun() {
    if (noGrade) return;
    setLogOpen(true);
    const capB = numOr(form.minCapB);
    const volK = numOr(form.minAvgVolK);
    const body: BottomFlowRunRequest = {
      universe: form.universe,
      grades: grades.join(','),
      requireTurn: form.requireTurn,
      requireSurvivable: form.requireSurvivable,
      nearLowPct: numOr(form.nearLowPct),
      minDrawdownPct: numOr(form.minDrawdownPct),
      revTtmMin: numOr(form.revTtmMin),
      mfiMin: numOr(form.mfiMin),
      maxPerf1y: numOr(form.maxPerf1y),
      minCap: capB != null ? Math.round(capB * 1e9) : undefined,
      minAvgVol: volK != null ? Math.round(volK * 1e3) : undefined,
      minPrice: numOr(form.minPrice),
      top: numOr(form.top),
    };
    void stream.run(() => runBottomFlowScreener(body));
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Card
        title="Дно + дивергенция — параметры"
        sourceSelect={
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 12 }}>
            {gate ? (
              <span
                className="muted"
                style={{ textTransform: 'none', letterSpacing: 0, fontSize: 11 }}
              >
                Гейт: <b style={{ color: 'var(--text)' }}>{gate.decision}</b> · лонг-разворот
              </span>
            ) : null}
            <button onClick={() => setHelpOpen(true)} title="Как работать со скринером дна">
              ❓ Документация
            </button>
          </span>
        }
      >
        <BottomFlowParamForm form={form} onChange={setForm} disabled={running} />
        <div className="btn-row">
          <button className="primary" disabled={running || noGrade} onClick={doRun}>
            {running ? 'Скрин…' : '▶ Запустить скрин'}
          </button>
          {running ? (
            <button className="danger" onClick={() => void stream.cancel()}>
              Отмена
            </button>
          ) : null}
          {running ? <span className="muted">{stream.elapsed}s</span> : null}
          {noGrade ? <span className="muted">выбери хотя бы один грейд</span> : null}
          <ScreenerParamActions
            onSave={save}
            onReset={reset}
            saved={saved}
            dirty={dirty}
            disabled={running}
          />
        </div>

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

      <Card title="Кандидаты (дно + дивергенция потока)" source={staged?.source ?? undefined}>
        {isLoading ? (
          <Loading />
        ) : error ? (
          <ErrorNote error={error} />
        ) : screener ? (
          <BottomFlowResults screener={screener} date={date} />
        ) : (
          <Empty>
            Запусти скрин — кандидаты появятся здесь. Detection-only, ничего не регистрируется.
          </Empty>
        )}
      </Card>

      {helpOpen ? <BottomFlowHelpModal onClose={() => setHelpOpen(false)} /> : null}
    </div>
  );
}
