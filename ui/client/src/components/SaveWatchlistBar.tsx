import type { SaveWatchlistMode } from '@shared/types';

/**
 * The two registration buttons (the ONLY thing that writes into trading-data):
 *  - plain → watchlist + promote screener/plan
 *  - full  → also thesis-ingest + TradingView [WL] alerts (step 5.5)
 */
export default function SaveWatchlistBar({
  candidateCount,
  disabled,
  savingMode,
  onSave,
}: {
  candidateCount: number;
  disabled: boolean;
  savingMode: SaveWatchlistMode | null;
  onSave: (mode: SaveWatchlistMode) => void;
}) {
  const saving = savingMode != null;
  const ask = (mode: SaveWatchlistMode, label: string) => {
    if (
      window.confirm(
        `${label}\n\nВ watchlist на сегодня попадёт кандидатов: ${candidateCount}.\nЗаписать в trading-data?`,
      )
    ) {
      onSave(mode);
    }
  };

  return (
    <div className="field" style={{ marginTop: 8 }}>
      <strong>Регистрация — {candidateCount} кандидат(ов) в watchlist</strong>
      <div className="btn-row" style={{ marginTop: 6 }}>
        <button
          className="primary"
          disabled={disabled || saving}
          onClick={() => ask('plain', 'Сохранить как watchlist (+ копия screener/plan в trading-data)')}
        >
          {savingMode === 'plain' ? 'Сохранение…' : '💾 Сохранить как watchlist'}
        </button>
        <button
          disabled={disabled || saving}
          onClick={() => ask('full', 'Сохранить + журнал + алерты (шаг 5.5)')}
        >
          {savingMode === 'full' ? 'Сохранение…' : '💾 + журнал + алерты (5.5)'}
        </button>
      </div>
      <span className="hint">
        «Сохранить» пишет <code>watchlist_&lt;сегодня&gt;.json</code> с реальным гейтом и копирует
        screener/plan. «5.5» дополнительно регистрирует тезисы и ставит алерты TradingView (нужен
        TradingView Desktop).
      </span>
    </div>
  );
}
