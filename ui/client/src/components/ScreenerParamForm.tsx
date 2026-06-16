import type { ReactNode } from 'react';
import type { ScreenerMode, ScreenerUniverse, WideUniverseInfo } from '@shared/types';

export interface ScreenerFormState {
  universe: ScreenerUniverse;
  symbolsText: string;
  maxCandidates: string;
  minAtrPct: string;
  trendMinScore: string;
  breakoutVolumeRatio: string;
  minContractions: string;
  extThreshold: string;
  mode: ScreenerMode;
  strict: boolean;
  earningsGateDays: string; // applied at the plan step
}

/**
 * Pre-filled with the values the evening-prep slot effectively uses: the VCP
 * screener's own argparse defaults (evening-prep overrides none of them), and
 * earnings_gate_days=10 from the trading profile. The universe defaults to the
 * wide NASDAQ+NYSE file when it exists (see ScreenerTab), matching evening-prep.
 */
export const DEFAULT_FORM: ScreenerFormState = {
  universe: 'sp500',
  symbolsText: '',
  maxCandidates: '', // empty = analyze the whole universe (S&P 500 default = 100)
  minAtrPct: '1',
  trendMinScore: '85',
  breakoutVolumeRatio: '1.5',
  minContractions: '2',
  extThreshold: '8',
  mode: 'all',
  strict: false,
  earningsGateDays: '10',
};

/** A vertically-stacked labelled form field (label → control → optional range hint). */
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

export default function ScreenerParamForm({
  form,
  onChange,
  disabled,
  wideUniverse,
}: {
  form: ScreenerFormState;
  onChange: (next: ScreenerFormState) => void;
  disabled: boolean;
  wideUniverse?: WideUniverseInfo;
}) {
  const set = <K extends keyof ScreenerFormState>(k: K, v: ScreenerFormState[K]) =>
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
        <FF label="Режим">
          <select
            value={form.mode}
            disabled={disabled}
            onChange={(e) => set('mode', e.target.value as ScreenerMode)}
          >
            <option value="all">все</option>
            <option value="prebreakout">pre-breakout</option>
          </select>
        </FF>
        <FF label="Строгий режим">
          <label className="check">
            <input
              type="checkbox"
              checked={form.strict}
              disabled={disabled}
              onChange={(e) => set('strict', e.target.checked)}
            />
            strict
          </label>
        </FF>
      </div>

      {form.universe === 'custom' ? (
        <FF label="Тикеры (через пробел/запятую)">
          <textarea
            rows={2}
            value={form.symbolsText}
            disabled={disabled}
            placeholder="NVDA AVGO PLTR"
            onChange={(e) => set('symbolsText', e.target.value)}
          />
        </FF>
      ) : null}

      <div>
        <div className="screener-section-label">Фильтры VCP</div>
        <div className="screener-grid">
          <NumFF label="Trend min-score" range="0–100" v={form.trendMinScore} set={(v) => set('trendMinScore', v)} min={0} max={100} step={1} disabled={disabled} />
          <NumFF label="Min ATR, %" range="0–20" v={form.minAtrPct} set={(v) => set('minAtrPct', v)} min={0} max={20} step={0.1} disabled={disabled} />
          <NumFF label="Breakout vol ×" range="0.5–10" v={form.breakoutVolumeRatio} set={(v) => set('breakoutVolumeRatio', v)} min={0.5} max={10} step={0.1} disabled={disabled} />
          <NumFF label="Min сжатий" range="2–4" v={form.minContractions} set={(v) => set('minContractions', v)} min={2} max={4} step={1} disabled={disabled} />
          <NumFF label="Ext threshold" range="0–50" v={form.extThreshold} set={(v) => set('extThreshold', v)} min={0} max={50} step={0.5} disabled={disabled} />
          <NumFF label="Max candidates" range="пусто = вся вселенная" placeholder="все" v={form.maxCandidates} set={(v) => set('maxCandidates', v)} min={1} max={2000} step={1} disabled={disabled} />
          <NumFF label="Earnings-гейт, дн" range="0–60" v={form.earningsGateDays} set={(v) => set('earningsGateDays', v)} min={0} max={60} step={1} disabled={disabled} />
        </div>
      </div>

      <span className="hint">
        Скрин читает живые данные TradingView (нужен TradingView Desktop). Пустое поле = дефолт
        скрипта. «Earnings-гейт» применяется на шаге «Построить план». Результат нигде не
        регистрируется до сохранения.
      </span>
    </div>
  );
}
