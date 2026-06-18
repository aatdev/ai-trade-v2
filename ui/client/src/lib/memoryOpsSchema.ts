/**
 * Declarative schema of every trader-memory operation. Drives the generic op
 * forms in MemoryOps.tsx. Field `name`s match the POST /api/actions/memory body
 * keys validated server-side in server/src/lib/memoryOps.ts.
 */

export type FieldType = 'text' | 'number' | 'date' | 'select' | 'checkbox';

export interface OpField {
  name: string;
  label: string;
  type: FieldType;
  options?: string[]; // for select; include '' for an "(any)" choice
  required?: boolean;
  placeholder?: string;
  default?: string; // literal, or '@today' for the current date
  // When set, the select's options are computed at render time from the open
  // thesis's status instead of the static `options`. 'forwardStatus' → only
  // states reachable forward (the thesis state machine is forward-only).
  dynamicOptions?: 'forwardStatus';
}

export interface OpDef {
  op: string;
  label: string;
  scope: 'thesis' | 'global';
  danger?: boolean;
  confirm?: boolean; // ask before running (destructive / position-changing)
  hint?: string;
  fields: OpField[];
}

const STATUSES = ['IDEA', 'ENTRY_READY', 'ACTIVE', 'PARTIALLY_CLOSED', 'CLOSED', 'INVALIDATED'];
/** Forward-only state machine order (INVALIDATED is a terminal off-ramp, not in line). */
export const STATUS_SEQUENCE = ['IDEA', 'ENTRY_READY', 'ACTIVE', 'PARTIALLY_CLOSED', 'CLOSED'];
/**
 * Plain forward status changes the `transition` op accepts — never backward.
 * ACTIVE, PARTIALLY_CLOSED and the terminal states (CLOSED / INVALIDATED) are
 * NOT reachable via transition(): they have dedicated ops (open-position / trim
 * / close / terminate) and the CLI rejects them here. So the only valid plain
 * transition is IDEA → ENTRY_READY; from ENTRY_READY onward this is empty (use
 * the dedicated ops). Mirrors thesis_store.transition()'s guards.
 */
export function forwardStatuses(current: string): string[] {
  const PLAIN_TARGETS = ['ENTRY_READY'];
  const i = STATUS_SEQUENCE.indexOf(current);
  return PLAIN_TARGETS.filter((s) => STATUS_SEQUENCE.indexOf(s) > i);
}
const EXIT_REASONS = ['stop_hit', 'target_hit', 'time_stop', 'invalidated', 'manual'];
const THESIS_TYPES = [
  'dividend_income',
  'growth_momentum',
  'mean_reversion',
  'earnings_drift',
  'pivot_breakout',
];

/* ---------------- per-thesis (thesisId injected from the open thesis) ---------------- */

export const THESIS_OPS: OpDef[] = [
  {
    op: 'transition',
    label: 'Сменить статус',
    scope: 'thesis',
    fields: [
      { name: 'newStatus', label: 'Новый статус', type: 'select', options: STATUSES, dynamicOptions: 'forwardStatus', required: true, default: 'ENTRY_READY' },
      { name: 'reason', label: 'Причина', type: 'text', required: true },
      { name: 'eventDate', label: 'Дата события (опц.)', type: 'date' },
    ],
  },
  {
    op: 'open-position',
    label: 'Открыть позицию',
    scope: 'thesis',
    confirm: true,
    hint: 'Фактический вход → ACTIVE.',
    fields: [
      { name: 'price', label: 'Цена входа', type: 'number', required: true },
      { name: 'date', label: 'Дата входа', type: 'date', required: true, default: '@today' },
      { name: 'shares', label: 'Акций (опц.)', type: 'number' },
      { name: 'reason', label: 'Причина (опц.)', type: 'text' },
      { name: 'eventDate', label: 'Дата события (опц.)', type: 'date' },
    ],
  },
  {
    op: 'attach-position',
    label: 'Прикрепить сайзинг',
    scope: 'thesis',
    hint: 'JSON-отчёт position-sizer (относительный путь).',
    fields: [
      { name: 'report', label: 'Отчёт (.json)', type: 'text', required: true, placeholder: 'reports/sizer.json' },
      { name: 'expectedEntry', label: 'Ожид. вход (опц.)', type: 'number' },
      { name: 'expectedStop', label: 'Ожид. стоп (опц.)', type: 'number' },
    ],
  },
  {
    op: 'trim',
    label: 'Частично закрыть (trim)',
    scope: 'thesis',
    confirm: true,
    hint: 'Продажа части позиции по цене и количеству.',
    fields: [
      { name: 'sharesSold', label: 'Продано акций', type: 'number', required: true },
      { name: 'price', label: 'Цена', type: 'number', required: true },
      { name: 'date', label: 'Дата', type: 'date', required: true, default: '@today' },
      { name: 'reason', label: 'Причина (опц.)', type: 'text' },
      { name: 'exitReason', label: 'Причина выхода (если полное закрытие)', type: 'select', options: ['', ...EXIT_REASONS] },
      { name: 'eventDate', label: 'Дата события (опц.)', type: 'date' },
    ],
  },
  {
    op: 'close',
    label: 'Закрыть',
    scope: 'thesis',
    confirm: true,
    hint: 'Полное закрытие ACTIVE → CLOSED.',
    fields: [
      { name: 'exitReason', label: 'Причина выхода', type: 'select', options: EXIT_REASONS, required: true, default: 'manual' },
      { name: 'price', label: 'Цена выхода', type: 'number', required: true },
      { name: 'date', label: 'Дата выхода', type: 'date', required: true, default: '@today' },
      { name: 'eventDate', label: 'Дата события (опц.)', type: 'date' },
    ],
  },
  {
    op: 'terminate',
    label: 'Завершить',
    scope: 'thesis',
    confirm: true,
    hint: 'Перевод в терминальный статус (напр. отмена идеи без сделки).',
    fields: [
      { name: 'terminalStatus', label: 'Статус', type: 'select', options: ['CLOSED', 'INVALIDATED'], required: true, default: 'CLOSED' },
      { name: 'exitReason', label: 'Причина', type: 'text', required: true },
      { name: 'price', label: 'Цена выхода (опц.)', type: 'number' },
      { name: 'date', label: 'Дата выхода (опц.)', type: 'date' },
      { name: 'eventDate', label: 'Дата события (опц.)', type: 'date' },
    ],
  },
  {
    op: 'mark-reviewed',
    label: 'Отметить ревью',
    scope: 'thesis',
    fields: [
      { name: 'outcome', label: 'Оценка', type: 'select', options: ['OK', 'WARN', 'REVIEW'], required: true, default: 'OK' },
      { name: 'notes', label: 'Заметка (опц.)', type: 'text' },
      { name: 'reviewDate', label: 'Дата ревью (опц.)', type: 'date' },
    ],
  },
  { op: 'postmortem', label: 'Постмортем', scope: 'thesis', hint: 'P&L + MAE/MFE по закрытому тезису.', fields: [{ name: 'journalDir', label: 'Каталог журнала (опц.)', type: 'text' }] },
  { op: 'get', label: 'Показать YAML (get)', scope: 'thesis', fields: [] },
  { op: 'delete', label: 'Удалить', scope: 'thesis', danger: true, confirm: true, hint: 'Безвозвратное удаление файла и записи индекса.', fields: [] },
];

/* IB bracket place/cancel is driven by the bespoke <IbBracketOps> panel
 * (MemoryOps.tsx), not the generic schema — it prefills computed/live values
 * and disables placement once an order exists, which the static schema can't. */

/* ---------------- global (no thesisId) ---------------- */

export const GLOBAL_OPS: OpDef[] = [
  {
    op: 'ingest',
    label: 'Ingest — зарегистрировать из отчёта',
    scope: 'global',
    hint: 'Относительный путь к JSON-отчёту скринера; без «..».',
    fields: [
      { name: 'source', label: 'Источник (скил)', type: 'text', required: true, placeholder: 'vcp-screener' },
      { name: 'input', label: 'Файл (.json)', type: 'text', required: true, placeholder: 'reports/screener.json' },
    ],
  },
  { op: 'review-due', label: 'Просроченные ревью', scope: 'global', fields: [{ name: 'asOf', label: 'На дату (опц.)', type: 'date' }] },
  { op: 'summary', label: 'Сводка', scope: 'global', fields: [] },
  {
    op: 'list',
    label: 'Список (фильтры)',
    scope: 'global',
    fields: [
      { name: 'ticker', label: 'Тикер', type: 'text' },
      { name: 'status', label: 'Статус', type: 'select', options: ['', ...STATUSES] },
      { name: 'type', label: 'Тип', type: 'select', options: ['', ...THESIS_TYPES] },
      { name: 'dateFrom', label: 'Создан с', type: 'date' },
      { name: 'dateTo', label: 'Создан по', type: 'date' },
    ],
  },
  {
    op: 'heat',
    label: 'Portfolio heat',
    scope: 'global',
    hint: 'Открытый риск по ACTIVE-тезисам.',
    fields: [
      { name: 'accountSize', label: 'Капитал ($, опц.)', type: 'number' },
      { name: 'maxHeatPct', label: 'Лимит heat % (опц.)', type: 'number' },
      { name: 'maxPositions', label: 'Макс. позиций (опц.)', type: 'number' },
      { name: 'jsonOnly', label: 'Только JSON', type: 'checkbox' },
    ],
  },
  { op: 'doctor', label: 'Doctor (проверка)', scope: 'global', fields: [] },
  { op: 'rebuild-index', label: 'Перестроить индекс', scope: 'global', fields: [] },
];
