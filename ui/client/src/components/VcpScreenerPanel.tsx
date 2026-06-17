import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { JobStatus, SaveWatchlistMode, ScreenerRunRequest, StagedPlan } from '@shared/types';
import {
  runScreener,
  runScreenerPlan,
  saveWatchlist,
  useStagedScreener,
  type Refetch,
} from '../api';
import { useJobStream } from '../lib/useJobStream';
import ScreenerHelpModal from './ScreenerHelpModal';
import ScreenerParamForm, { DEFAULT_FORM, type ScreenerFormState } from './ScreenerParamForm';
import ScreenerResults from './ScreenerResults';
import SaveWatchlistBar from './SaveWatchlistBar';
import { Card, Collapsible, Empty, ErrorNote, Loading, Stat } from './ui';

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

type Action = 'run' | 'plan' | 'save-plain' | 'save-full';

function RejectList({ items }: { items: StagedPlan['rejected'] }) {
  return (
    <ul style={{ margin: 0, paddingLeft: 18 }}>
      {items.map((r, i) => (
        <li key={i}>
          <span className="sym">{r.symbol}</span>
          {r.reason ? <span className="muted"> — {r.reason}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function PlanSummary({ plan }: { plan: StagedPlan }) {
  const s = plan.summary;
  return (
    <>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
          gap: 6,
          marginBottom: 8,
        }}
      >
        <Stat k="В сделку" v={s.actionable_count ?? plan.actionable.length} />
        <Stat k="Ревалидация" v={s.revalidation_count ?? plan.revalidation.length} />
        <Stat k="Отклонено" v={s.rejected_count ?? plan.rejected.length} />
        <Stat k="Earnings-блок" v={s.blocked_earnings_count ?? plan.blocked_earnings.length} />
        <Stat k="Heat-defer" v={s.deferred_count ?? plan.deferred.length} />
      </div>
      {plan.rejected.length ? (
        <Collapsible label="Отклонённые" count={plan.rejected.length}>
          <RejectList items={plan.rejected} />
        </Collapsible>
      ) : null}
      {plan.blocked_earnings.length ? (
        <Collapsible label="Заблокировано по earnings" count={plan.blocked_earnings.length}>
          <RejectList items={plan.blocked_earnings} />
        </Collapsible>
      ) : null}
      {plan.deferred.length ? (
        <Collapsible label="Отложено по heat" count={plan.deferred.length}>
          <RejectList items={plan.deferred} />
        </Collapsible>
      ) : null}
    </>
  );
}

export default function VcpScreenerPanel({
  date,
  refetch,
}: {
  date: string | null;
  refetch: Refetch;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<ScreenerFormState>(DEFAULT_FORM);
  const [helpOpen, setHelpOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(true);
  const actionRef = useRef<Action | null>(null);
  const didInitUniverse = useRef(false);
  const logRef = useRef<HTMLPreElement>(null);

  const stream = useJobStream({
    onEnd: (status: JobStatus) => {
      void qc.invalidateQueries({ queryKey: ['stagedScreener'] });
      if ((actionRef.current === 'save-plain' || actionRef.current === 'save-full') && status === 'done') {
        void qc.invalidateQueries({ queryKey: ['watchlist'] });
        void qc.invalidateQueries({ queryKey: ['dates'] });
        void qc.invalidateQueries({ queryKey: ['screeners'] });
      }
    },
  });
  const running = stream.state === 'running';

  // Pause polling while a job runs (we refetch explicitly on the SSE `end`).
  const { data: staged, isLoading, error } = useStagedScreener({}, running ? false : refetch);

  // Default the universe to the wide NASDAQ+NYSE file when it exists — that is
  // what evening-prep uses. One-shot, so a later manual choice is never clobbered.
  useEffect(() => {
    if (didInitUniverse.current || !staged) return;
    didInitUniverse.current = true;
    if (staged.wideUniverse?.available) setForm((f) => ({ ...f, universe: 'wide' }));
  }, [staged]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [stream.lines]);

  const screener = staged?.screener ?? null;
  const plan = staged?.plan ?? null;
  const planCount = plan ? plan.actionable.length + plan.revalidation.length : 0;
  const rejectedCount = plan ? (plan.summary.rejected_count ?? plan.rejected.length) : 0;
  const blockedCount = plan ? (plan.summary.blocked_earnings_count ?? plan.blocked_earnings.length) : 0;
  const deferredCount = plan ? (plan.summary.deferred_count ?? plan.deferred.length) : 0;
  const savingMode: SaveWatchlistMode | null =
    running && actionRef.current === 'save-plain'
      ? 'plain'
      : running && actionRef.current === 'save-full'
        ? 'full'
        : null;

  function doRun() {
    actionRef.current = 'run';
    setLogOpen(true);
    const body: ScreenerRunRequest = {
      universe: form.universe,
      symbols: form.universe === 'custom' ? parseSymbols(form.symbolsText) : undefined,
      maxCandidates: numOr(form.maxCandidates),
      minAtrPct: numOr(form.minAtrPct),
      trendMinScore: numOr(form.trendMinScore),
      breakoutVolumeRatio: numOr(form.breakoutVolumeRatio),
      minContractions: numOr(form.minContractions),
      extThreshold: numOr(form.extThreshold),
      mode: form.mode,
      strict: form.strict || undefined,
    };
    void stream.run(() => runScreener(body));
  }
  function doPlan() {
    actionRef.current = 'plan';
    setLogOpen(true);
    void stream.run(() => runScreenerPlan({ earningsGateDays: numOr(form.earningsGateDays) }));
  }
  function doSave(mode: SaveWatchlistMode) {
    actionRef.current = mode === 'full' ? 'save-full' : 'save-plain';
    setLogOpen(true);
    void stream.run(() => saveWatchlist({ mode }));
  }

  const gate = staged?.gate?.data ?? null;
  const heat = staged?.heat?.data ?? null;

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Card
        title="Скринер VCP — параметры (5.1)"
        sourceSelect={
          <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 12 }}>
            {gate ? (
              <span
                className="muted"
                style={{ textTransform: 'none', letterSpacing: 0, fontSize: 11 }}
              >
                Гейт: <b style={{ color: 'var(--text)' }}>{gate.decision}</b>
                {heat?.open_risk_pct != null
                  ? ` · heat ${heat.open_risk_pct}% / ${heat.max_portfolio_heat_pct ?? 6}% · позиций ${heat.positions_count ?? 0}/${heat.max_positions ?? 6}`
                  : ''}
              </span>
            ) : null}
            <button onClick={() => setHelpOpen(true)} title="Как работать со скринером">
              ❓ Документация
            </button>
          </span>
        }
      >
        <ScreenerParamForm
          form={form}
          onChange={setForm}
          disabled={running}
          wideUniverse={staged?.wideUniverse}
        />
        <div className="btn-row">
          <button className="primary" disabled={running} onClick={doRun}>
            {running && actionRef.current === 'run' ? 'Скрин…' : '▶ Запустить скрин'}
          </button>
          <button
            disabled={running || !screener || screener.candidates.length === 0}
            onClick={doPlan}
            title="breakout-trade-planner по staged-результату"
          >
            {running && actionRef.current === 'plan' ? 'План…' : '🧮 Построить план (5.4)'}
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

      <Card title="Результаты (топ-100)" source={screener?.source ?? undefined}>
        {isLoading ? (
          <Loading />
        ) : error ? (
          <ErrorNote error={error} />
        ) : screener ? (
          <ScreenerResults screener={screener} date={date} />
        ) : (
          <Empty>Запусти скрин — результаты появятся здесь и не регистрируются до сохранения.</Empty>
        )}
      </Card>

      {plan ? (
        <Card title="План сделок (5.4)">
          {planCount === 0 ? (
            <div
              className="empty"
              style={{
                textAlign: 'left',
                borderLeft: '3px solid var(--yellow)',
                borderRadius: 4,
                paddingLeft: 12,
                marginBottom: 10,
              }}
            >
              <strong>Нет покупаемых кандидатов.</strong> Отсеяно {rejectedCount}
              {blockedCount ? ` · earnings-блок ${blockedCount}` : ''}
              {deferredCount ? ` · heat-defer ${deferredCount}` : ''}. Пустой план — сигнал
              «стоять в стороне», а не ошибка (причины — в списках ниже).
            </div>
          ) : null}
          <PlanSummary plan={plan} />
          <SaveWatchlistBar
            candidateCount={planCount}
            disabled={running}
            savingMode={savingMode}
            onSave={doSave}
          />
        </Card>
      ) : null}

      {helpOpen ? <ScreenerHelpModal onClose={() => setHelpOpen(false)} /> : null}
    </div>
  );
}
