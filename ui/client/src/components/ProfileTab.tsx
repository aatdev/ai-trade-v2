import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { JobStatus, SaveProfileResponse, TradingProfile } from '@shared/types';
import { recalcProfile, saveProfile, useProfile } from '../api';
import { useJobStream } from '../lib/useJobStream';
import { Card, Empty, ErrorNote, Loading } from './ui';

/** UI metadata for one editable profile field. Ranges mirror the server PROFILE_SPEC. */
interface FieldSpec {
  key: string;
  label: string;
  min: number;
  max: number;
  step: number;
  integer?: boolean;
  toggle?: boolean; // 0/1 gate → render as checkbox
  suffix?: string;
  hint?: string;
  affectsRecalc?: boolean;
  screenOnly?: boolean;
}

interface Group {
  title: string;
  fields: FieldSpec[];
}

const GROUPS: Group[] = [
  {
    title: 'Капитал и риск',
    fields: [
      { key: 'account_size', label: 'Размер счёта', min: 1, max: 1_000_000_000, step: 1000, suffix: '$', affectsRecalc: true, hint: 'База для расчёта риска и лимитов позиции' },
      { key: 'risk_pct', label: 'Риск на сделку', min: 0.01, max: 100, step: 0.1, suffix: '%', affectsRecalc: true, hint: '$-риск = счёт × риск%; задаёт размер позиции' },
      { key: 'max_position_pct', label: 'Макс. позиция', min: 0.1, max: 100, step: 1, suffix: '%', affectsRecalc: true, hint: 'Потолок размера одной позиции от счёта' },
      { key: 'max_sector_pct', label: 'Макс. на сектор', min: 0.1, max: 100, step: 1, suffix: '%', affectsRecalc: true },
      { key: 'max_portfolio_heat_pct', label: 'Макс. heat портфеля', min: 0.1, max: 100, step: 0.5, suffix: '%', affectsRecalc: true, hint: 'Суммарный открытый риск; гейтит новые входы' },
      { key: 'max_positions', label: 'Макс. позиций', min: 1, max: 100, step: 1, integer: true, hint: 'Лимит слотов (не меняет размер существующих)' },
    ],
  },
  {
    title: 'Уровни и стопы',
    fields: [
      { key: 'target_r_multiple', label: 'Цель, R-мультипл', min: 0.1, max: 20, step: 0.5, suffix: 'R', affectsRecalc: true, hint: 'Target = вход + R × (вход − стоп)' },
      { key: 'atr_multiplier', label: 'ATR-множитель стопа', min: 0.1, max: 20, step: 0.5, suffix: '×ATR', affectsRecalc: true },
    ],
  },
  {
    title: 'Гейты и фильтры',
    fields: [
      { key: 'earnings_gate_days', label: 'Earnings-гейт', min: 0, max: 60, step: 1, integer: true, suffix: 'дн', affectsRecalc: true, hint: 'Блокировать входы за N дней до отчёта' },
      { key: 'time_stop_trading_days', label: 'Time-stop', min: 0, max: 365, step: 1, integer: true, suffix: 'дн', affectsRecalc: true },
      { key: 'fundamental_gate', label: 'Фундаментальный гейт', min: 0, max: 1, step: 1, integer: true, toggle: true, affectsRecalc: true, hint: 'Soft quality-floor для лонгов' },
      { key: 'sector_rs_gate', label: 'Sector-RS гейт', min: 0, max: 1, step: 1, integer: true, toggle: true, affectsRecalc: true, screenOnly: true, hint: 'Не лезть против слабого сектора (на этапе скрина)' },
      { key: 'sector_rs_threshold', label: 'Sector-RS порог', min: 0, max: 100, step: 1, affectsRecalc: true, screenOnly: true, hint: 'Порог силы сектора в RS-пунктах (на этапе скрина)' },
    ],
  },
];

const ALL_FIELDS = GROUPS.flatMap((g) => g.fields);
const FIELD_LABEL: Record<string, string> = Object.fromEntries(ALL_FIELDS.map((f) => [f.key, f.label]));

type FormState = Record<string, string>;

function toForm(p: TradingProfile): FormState {
  const out: FormState = {};
  for (const f of ALL_FIELDS) {
    const v = p[f.key];
    out[f.key] = typeof v === 'number' && Number.isFinite(v) ? String(v) : '';
  }
  return out;
}

/** Parse + range/integer-validate the form. Returns the patch or the first error. */
function parseForm(form: FormState): { patch: Partial<TradingProfile> } | { error: string } {
  const patch: Record<string, number> = {};
  for (const f of ALL_FIELDS) {
    const raw = (form[f.key] ?? '').trim();
    if (raw === '') return { error: `${f.label}: заполни значение` };
    const n = Number(raw);
    if (!Number.isFinite(n)) return { error: `${f.label}: не число` };
    if (n < f.min || n > f.max) return { error: `${f.label}: вне диапазона [${f.min}, ${f.max}]` };
    if (f.integer && !Number.isInteger(n)) return { error: `${f.label}: должно быть целым` };
    patch[f.key] = n;
  }
  return { patch };
}

export default function ProfileTab() {
  const qc = useQueryClient();
  const { data: profile, isLoading, error } = useProfile();
  const [form, setForm] = useState<FormState | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<SaveProfileResponse | null>(null);
  const [logOpen, setLogOpen] = useState(true);
  const logRef = useRef<HTMLPreElement>(null);

  // Seed the form once the profile loads; keep edits afterwards.
  useEffect(() => {
    if (profile && form === null) setForm(toForm(profile));
  }, [profile, form]);

  const recalc = useJobStream({
    onEnd: (status: JobStatus) => {
      if (status === 'done') {
        void qc.invalidateQueries({ queryKey: ['watchlist'] });
        void qc.invalidateQueries({ queryKey: ['screeners'] });
        void qc.invalidateQueries({ queryKey: ['theses'] });
        void qc.invalidateQueries({ queryKey: ['memory'] });
        void qc.invalidateQueries({ queryKey: ['dates'] });
        void qc.invalidateQueries({ queryKey: ['stagedScreener'] });
      }
    },
  });
  const recalcRunning = recalc.state === 'running';

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [recalc.lines]);

  const dirty = useMemo(() => {
    if (!profile || !form) return false;
    return ALL_FIELDS.some((f) => {
      const cur = form[f.key]?.trim() ?? '';
      const orig = typeof profile[f.key] === 'number' ? String(profile[f.key]) : '';
      return cur !== orig;
    });
  }, [profile, form]);

  if (isLoading) return <Card title="Профиль"><Loading /></Card>;
  if (error) return <Card title="Профиль"><ErrorNote error={error} /></Card>;
  if (!profile || !form) {
    return (
      <Card title="Профиль">
        <Empty>trading_profile.json не найден в каталоге данных.</Empty>
      </Card>
    );
  }

  const set = (k: string, v: string) => setForm((prev) => ({ ...(prev ?? {}), [k]: v }));

  async function onSave() {
    const parsed = parseForm(form!);
    if ('error' in parsed) {
      setSaveErr(parsed.error);
      return;
    }
    setSaveErr(null);
    setSaving(true);
    setSaved(null);
    recalc.reset();
    try {
      const res = await saveProfile(parsed.patch);
      if (!res.ok) {
        setSaveErr(res.error || 'не удалось сохранить');
        return;
      }
      setSaved(res);
      // Seed the cache with the authoritative saved profile so `dirty` settles
      // immediately (no refetch flicker); the form already holds these values.
      if (res.profile) qc.setQueryData(['profile'], res.profile);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  function onReset() {
    if (profile) setForm(toForm(profile));
    setSaveErr(null);
    setSaved(null);
    recalc.reset();
  }

  // Which saved changes a re-plan can actually reflect (everything bar screen-only).
  const replanKeys = (saved?.recalcAffected ?? []).filter(
    (k) => !(saved?.screenOnlyAffected ?? []).includes(k),
  );
  const screenOnly = saved?.screenOnlyAffected ?? [];
  const labels = (keys: string[]) => keys.map((k) => FIELD_LABEL[k] ?? k).join(', ');

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <Card
        title="Профиль торговли — параметры риска и гейтов"
        source={`account ${profile.account_size.toLocaleString('en-US')} $`}
      >
        <div className="screener-form">
          {GROUPS.map((g) => (
            <div key={g.title}>
              <div className="screener-section-label">{g.title}</div>
              <div className="screener-grid">
                {g.fields.map((f) => (
                  <div className="ff" key={f.key}>
                    <span className="ff-label">
                      {f.label}
                      {f.suffix ? <span className="muted">, {f.suffix}</span> : null}
                      {f.affectsRecalc ? (
                        <span title="Влияет на пересчёт ватчлиста / неактивных тезисов"> ↻</span>
                      ) : null}
                    </span>
                    {f.toggle ? (
                      <label className="check">
                        <input
                          type="checkbox"
                          checked={(form[f.key] ?? '0').trim() === '1'}
                          disabled={saving}
                          onChange={(e) => set(f.key, e.target.checked ? '1' : '0')}
                        />
                        {(form[f.key] ?? '0').trim() === '1' ? 'вкл' : 'выкл'}
                      </label>
                    ) : (
                      <input
                        type="number"
                        value={form[f.key] ?? ''}
                        min={f.min}
                        max={f.max}
                        step={f.step}
                        disabled={saving}
                        onChange={(e) => set(f.key, e.target.value)}
                      />
                    )}
                    {f.hint ? <span className="ff-range">{f.hint}</span> : null}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="btn-row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={saving || !dirty} onClick={() => void onSave()}>
            {saving ? 'Сохранение…' : dirty ? '💾 Сохранить' : '💾 Сохранено'}
          </button>
          <button disabled={saving || !dirty} onClick={onReset}>
            Сбросить
          </button>
          {dirty ? <span className="muted">● есть несохранённые изменения</span> : null}
        </div>

        <p className="hint">
          Поля с ↻ влияют на размер позиций / уровни ватчлиста и неактивных тезисов.<br/> Файл —{' '}
          <code>trading-data/trading_profile.json</code>, тот же, что читают скрипты планировщика.<br/>
          Активные позиции (ACTIVE-тезисы) пересчёт не трогает — их стоп уже стоит у брокера.
        </p>

        {saveErr ? (
          <div className="err" style={{ marginTop: 8 }}>
            {saveErr}
          </div>
        ) : null}
      </Card>

      {saved ? (
        <Card title="Пересчёт после изменения профиля">
          {(saved.changed?.length ?? 0) === 0 ? (
            <div className="empty" style={{ textAlign: 'left' }}>
              Профиль сохранён без изменений значений — пересчитывать нечего.
            </div>
          ) : (
            <>
              <div style={{ marginBottom: 10 }}>
                ✅ Сохранено. Изменены: <b>{labels(saved.changed ?? [])}</b>.
              </div>

              {replanKeys.length > 0 ? (
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
                  Влияют на ватчлист / неактивные тезисы: <b>{labels(replanKeys)}</b>. Пересчёт
                  перепланирует последний VCP-скрин с новым профилем, перезапишет ватчлист и обновит
                  уровни <b>только</b> IDEA/ENTRY_READY тезисов.
                </div>
              ) : (
                <div className="empty" style={{ textAlign: 'left', marginBottom: 10 }}>
                  Изменённые параметры не влияют на ре-план ватчлиста.
                </div>
              )}

              {screenOnly.length > 0 ? (
                <div
                  className="empty"
                  style={{
                    textAlign: 'left',
                    borderLeft: '3px solid var(--red, #c0392b)',
                    borderRadius: 4,
                    paddingLeft: 12,
                    marginBottom: 10,
                  }}
                >
                  ⚠ <b>{labels(screenOnly)}</b> применяются на этапе скрининга — ре-план их не
                  отразит. Нужен полный ре-скрин: слот <b>evening-prep</b> в «⚡ Actions».
                </div>
              ) : null}

              <div className="btn-row">
                <button
                  className="primary"
                  disabled={recalcRunning || replanKeys.length === 0}
                  onClick={() => {
                    setLogOpen(true);
                    void recalc.run(() => recalcProfile());
                  }}
                  title="Ре-план последнего скрина → ватчлист → неактивные тезисы"
                >
                  {recalcRunning ? 'Пересчёт…' : '↻ Пересчитать ватчлист + неактивные тезисы'}
                </button>
                {recalcRunning ? (
                  <button className="danger" onClick={() => void recalc.cancel()}>
                    Отмена
                  </button>
                ) : null}
                {recalcRunning ? <span className="muted">{recalc.elapsed}s</span> : null}
                {recalc.state === 'done' ? <span className="muted">готово ✓</span> : null}
              </div>

              {recalc.error ? (
                <div className="err" style={{ marginTop: 8 }}>
                  {recalc.error}
                </div>
              ) : null}
              {recalc.lines.length > 0 || recalcRunning || recalc.state !== 'idle' ? (
                <div style={{ marginTop: 8 }}>
                  <div className="collapse-head" onClick={() => setLogOpen((o) => !o)}>
                    {logOpen ? '▾' : '▸'} Лог пересчёта
                    {recalcRunning
                      ? ` · ${recalc.elapsed}s`
                      : recalc.state !== 'idle'
                        ? ` · ${recalc.state}`
                        : ''}
                  </div>
                  {logOpen ? (
                    <pre className="joblog" ref={logRef} style={{ marginTop: 8 }}>
                      {recalc.lines.length ? recalc.lines.join('\n') : '(пока нет вывода)'}
                    </pre>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </Card>
      ) : null}
    </div>
  );
}
