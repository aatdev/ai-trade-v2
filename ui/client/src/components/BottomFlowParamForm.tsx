import type { ReactNode } from 'react';
import type { BottomFlowUniverse } from '@shared/types';

export interface BottomFlowFormState {
  universe: BottomFlowUniverse;
  // Grades to keep (at least one required to run).
  gradeA: boolean;
  gradeBaccum: boolean;
  gradeBfund: boolean;
  // Optional hard gates.
  requireTurn: boolean;
  requireSurvivable: boolean;
  // Tunable thresholds (empty string ⇒ the script's own default applies).
  nearLowPct: string;
  minDrawdownPct: string;
  revTtmMin: string;
  mfiMin: string;
  maxPerf1y: string;
  minCapB: string; // market cap floor, in $ billions
  minAvgVolK: string; // 30d avg volume floor, in thousands of shares
  minPrice: string;
  top: string;
}

/**
 * Pre-filled with screen_bottom_flow.py's argparse defaults so an untouched form
 * == the canonical bottom + flow divergence scan. Empty numeric field ⇒ the
 * script default applies. Grades default to all three.
 */
export const BOTTOM_FLOW_DEFAULT_FORM: BottomFlowFormState = {
  universe: 'common',
  gradeA: true,
  gradeBaccum: true,
  gradeBfund: true,
  requireTurn: false,
  requireSurvivable: false,
  nearLowPct: '25',
  minDrawdownPct: '35',
  revTtmMin: '0',
  mfiMin: '50',
  maxPerf1y: '-10',
  minCapB: '1',
  minAvgVolK: '500',
  minPrice: '5',
  top: '25',
};

function FF({ label, range, children }: { label: string; range?: string; children: ReactNode }) {
  return (
    <div className="ff">
      <span className="ff-label">{label}</span>
      {children}
      {range ? <span className="ff-range">{range}</span> : null}
    </div>
  );
}

function NumFF({
  label,
  range,
  v,
  set,
  min,
  max,
  step,
  disabled,
}: {
  label: string;
  range: string;
  v: string;
  set: (s: string) => void;
  min: number;
  max: number;
  step: number;
  disabled: boolean;
}) {
  return (
    <FF label={label} range={range}>
      <input
        type="number"
        value={v}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(e) => set(e.target.value)}
      />
    </FF>
  );
}

function ChkFF({
  label,
  v,
  set,
  disabled,
}: {
  label: string;
  v: boolean;
  set: (b: boolean) => void;
  disabled: boolean;
}) {
  return (
    <label className="chk" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <input type="checkbox" checked={v} disabled={disabled} onChange={(e) => set(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

export default function BottomFlowParamForm({
  form,
  onChange,
  disabled,
}: {
  form: BottomFlowFormState;
  onChange: (next: BottomFlowFormState) => void;
  disabled: boolean;
}) {
  const set = <K extends keyof BottomFlowFormState>(k: K, v: BottomFlowFormState[K]) =>
    onChange({ ...form, [k]: v });
  const noGrade = !form.gradeA && !form.gradeBaccum && !form.gradeBfund;

  return (
    <div className="screener-form">
      <div className="screener-form-row">
        <FF label="Вселенная">
          <select
            value={form.universe}
            disabled={disabled}
            onChange={(e) => set('universe', e.target.value as BottomFlowUniverse)}
          >
            <option value="common">Обыкновенные акции</option>
            <option value="all">+ привилегированные</option>
          </select>
        </FF>
        <FF label="Грейды" range={noGrade ? 'выбери ≥1' : undefined}>
          <span style={{ display: 'inline-flex', gap: 14, flexWrap: 'wrap' }}>
            <ChkFF label="A (двойная)" v={form.gradeA} set={(b) => set('gradeA', b)} disabled={disabled} />
            <ChkFF
              label="B-accum (накопление)"
              v={form.gradeBaccum}
              set={(b) => set('gradeBaccum', b)}
              disabled={disabled}
            />
            <ChkFF
              label="B-fund (фундамент)"
              v={form.gradeBfund}
              set={(b) => set('gradeBfund', b)}
              disabled={disabled}
            />
          </span>
        </FF>
      </div>

      <div className="screener-form-row">
        <FF label="Жёсткие гейты">
          <span style={{ display: 'inline-flex', gap: 14, flexWrap: 'wrap' }}>
            <ChkFF
              label="Только разворот (turn)"
              v={form.requireTurn}
              set={(b) => set('requireTurn', b)}
              disabled={disabled}
            />
            <ChkFF
              label="Только выживаемые"
              v={form.requireSurvivable}
              set={(b) => set('requireSurvivable', b)}
              disabled={disabled}
            />
          </span>
        </FF>
      </div>

      <div>
        <div className="screener-section-label">Дно + дивергенция (пороги)</div>
        <div className="screener-grid">
          <NumFF label="≤ % над 52н дном" range="0–100" v={form.nearLowPct} set={(v) => set('nearLowPct', v)} min={0} max={100} step={1} disabled={disabled} />
          <NumFF label="≥ % ниже 52н хая" range="0–100" v={form.minDrawdownPct} set={(v) => set('minDrawdownPct', v)} min={0} max={100} step={1} disabled={disabled} />
          <NumFF label="Min рост выручки TTM, %" range="-100…1000" v={form.revTtmMin} set={(v) => set('revTtmMin', v)} min={-100} max={1000} step={1} disabled={disabled} />
          <NumFF label="Min MFI (накопление)" range="0–100" v={form.mfiMin} set={(v) => set('mfiMin', v)} min={0} max={100} step={1} disabled={disabled} />
          <NumFF label="Max perf за год, %" range="≤ 0" v={form.maxPerf1y} set={(v) => set('maxPerf1y', v)} min={-100} max={0} step={1} disabled={disabled} />
          <NumFF label="Min кап., $B" range="0–10000" v={form.minCapB} set={(v) => set('minCapB', v)} min={0} max={10000} step={0.5} disabled={disabled} />
          <NumFF label="Min оборот, K акц." range="объём/день" v={form.minAvgVolK} set={(v) => set('minAvgVolK', v)} min={0} max={100000000} step={50} disabled={disabled} />
          <NumFF label="Min цена, $" range="0–100000" v={form.minPrice} set={(v) => set('minPrice', v)} min={0} max={100000} step={0.5} disabled={disabled} />
          <NumFF label="Топ в отчёте" range="0 = все" v={form.top} set={(v) => set('top', v)} min={0} max={500} step={1} disabled={disabled} />
        </div>
      </div>

      <span className="hint">
        Источник — публичный <code>scanner.tradingview.com</code> (без API-ключа и без TradingView
        Desktop). Пустое числовое поле = дефолт скрипта. Detection-only: ничего не регистрируется и
        не выставляет заявок. «Дно» — это кандидат, а не подтверждённый разворот: подтверди базу на
        графике перед входом.
      </span>
    </div>
  );
}
