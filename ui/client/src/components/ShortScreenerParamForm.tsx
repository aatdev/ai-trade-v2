import type { ReactNode } from 'react';
import type { ScreenerUniverse, ShortMinGrade, WideUniverseInfo } from '@shared/types';

export interface ShortFormState {
  universe: ScreenerUniverse;
  symbolsText: string;
  minGrade: ShortMinGrade;
  top: string;
  rsLookback: string;
  maxCandidates: string;
  minPrice: string;
  minDollarVolM: string; // average daily dollar volume, in $ millions
  minStopPct: string;
  maxStopPct: string;
}

/**
 * Pre-filled with screen_short.py's argparse defaults so an untouched form ==
 * the canonical Stage 4 weakness scan. `maxCandidates` empty ⇒ the full S&P 500
 * (the server adds `--full-sp500`); a number caps the analyzed universe.
 */
export const SHORT_DEFAULT_FORM: ShortFormState = {
  universe: 'sp500',
  symbolsText: '',
  minGrade: 'C',
  top: '25',
  rsLookback: '63',
  maxCandidates: '',
  minPrice: '5',
  minDollarVolM: '3',
  minStopPct: '2',
  maxStopPct: '10',
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
  placeholder,
}: {
  label: string;
  range: string;
  v: string;
  set: (s: string) => void;
  min: number;
  max: number;
  step: number;
  disabled: boolean;
  placeholder?: string;
}) {
  return (
    <FF label={label} range={range}>
      <input
        type="number"
        value={v}
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => set(e.target.value)}
      />
    </FF>
  );
}

export default function ShortScreenerParamForm({
  form,
  onChange,
  disabled,
  wideUniverse,
}: {
  form: ShortFormState;
  onChange: (next: ShortFormState) => void;
  disabled: boolean;
  wideUniverse?: WideUniverseInfo;
}) {
  const set = <K extends keyof ShortFormState>(k: K, v: ShortFormState[K]) =>
    onChange({ ...form, [k]: v });
  const wideOk = wideUniverse?.available ?? false;
  const wideLabel = wideOk ? `NASDAQ+NYSE (${wideUniverse?.count})` : 'NASDAQ+NYSE (нет файла)';

  return (
    <div className="screener-form">
      <div className="screener-form-row">
        <FF label="Вселенная">
          <select
            value={form.universe}
            disabled={disabled}
            onChange={(e) => set('universe', e.target.value as ScreenerUniverse)}
          >
            <option value="sp500">S&amp;P 500</option>
            <option value="wide" disabled={!wideOk}>
              {wideLabel}
            </option>
            <option value="custom">Свой список</option>
          </select>
        </FF>
        <FF label="Мин. грейд">
          <select
            value={form.minGrade}
            disabled={disabled}
            onChange={(e) => set('minGrade', e.target.value as ShortMinGrade)}
          >
            <option value="A">A</option>
            <option value="B">B</option>
            <option value="C">C</option>
            <option value="D">D</option>
          </select>
        </FF>
      </div>

      {form.universe === 'custom' ? (
        <FF label="Тикеры (через пробел/запятую)">
          <textarea
            rows={2}
            value={form.symbolsText}
            disabled={disabled}
            placeholder="TSLA NFLX PYPL"
            onChange={(e) => set('symbolsText', e.target.value)}
          />
        </FF>
      ) : null}

      <div>
        <div className="screener-section-label">Фильтры слабости (Stage 4)</div>
        <div className="screener-grid">
          <NumFF label="Топ в отчёте" range="0 = все" v={form.top} set={(v) => set('top', v)} min={0} max={500} step={1} disabled={disabled} />
          <NumFF label="RS lookback, сесс." range="5–252" v={form.rsLookback} set={(v) => set('rsLookback', v)} min={5} max={252} step={1} disabled={disabled} />
          <NumFF label="Min цена, $" range="0–100000" v={form.minPrice} set={(v) => set('minPrice', v)} min={0} max={100000} step={0.5} disabled={disabled} />
          <NumFF label="Min оборот, $M" range="оборот/день" v={form.minDollarVolM} set={(v) => set('minDollarVolM', v)} min={0} max={100000} step={0.5} disabled={disabled} />
          <NumFF label="Min стоп, %" range="0–50" v={form.minStopPct} set={(v) => set('minStopPct', v)} min={0} max={50} step={0.5} disabled={disabled} />
          <NumFF label="Max стоп, %" range="0–100" v={form.maxStopPct} set={(v) => set('maxStopPct', v)} min={0} max={100} step={0.5} disabled={disabled} />
          <NumFF label="Max candidates" range="пусто = вся S&P 500" placeholder="вся S&P 500" v={form.maxCandidates} set={(v) => set('maxCandidates', v)} min={1} max={2000} step={1} disabled={disabled} />
        </div>
      </div>

      <span className="hint">
        Скрин читает живые данные TradingView (нужен TradingView Desktop). Пустое поле = дефолт
        скрипта. Detection-only: перед входом подтверди borrow/locate и SSR (Rule 201) у брокера.
        Результат нигде не регистрируется.
      </span>
    </div>
  );
}
