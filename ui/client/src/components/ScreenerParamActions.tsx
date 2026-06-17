/**
 * Save / Reset controls shared by every screener param form. Rendered as an
 * inline group inside the run `btn-row` (pushed to the right via margin-left:auto)
 * so the run button and the save block sit on one line. Driven by
 * usePersistentForm: "Сохранить" persists the current values (enabled only when
 * dirty), "Сбросить" returns them to the script defaults (enabled when there is
 * something to undo). The status line tells the user which baseline is active.
 */
export default function ScreenerParamActions({
  onSave,
  onReset,
  saved,
  dirty,
  disabled,
}: {
  onSave: () => void;
  onReset: () => void;
  saved: boolean;
  dirty: boolean;
  disabled: boolean;
}) {
  const status = dirty
    ? 'есть несохранённые изменения'
    : saved
      ? 'активны сохранённые параметры'
      : 'значения по умолчанию';
  return (
    <span className="screener-param-actions">
      <span className="muted screener-param-status">{status}</span>
      <button
        type="button"
        onClick={onSave}
        disabled={disabled || !dirty}
        title="Сохранить текущие параметры — восстановятся при следующем открытии скринера"
      >
        💾 Сохранить параметры
      </button>
      <button
        type="button"
        onClick={onReset}
        disabled={disabled || (!dirty && !saved)}
        title="Сбросить параметры в значения по умолчанию"
      >
        ↺ Сбросить
      </button>
    </span>
  );
}
