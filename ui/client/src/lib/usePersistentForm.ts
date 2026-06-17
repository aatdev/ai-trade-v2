import { useCallback, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react';

const PREFIX = 'tradingUi.screenerForm.';

function readStored<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export interface PersistentForm<T> {
  form: T;
  setForm: Dispatch<SetStateAction<T>>;
  /** Persist the current form values; they are restored on the next mount. */
  save: () => void;
  /** Drop the saved preset and return every field to its default. */
  reset: () => void;
  /**
   * Apply a programmatic "effective default" (e.g. a server-derived universe)
   * as both the form value and the dirty baseline, so it is not reported as an
   * unsaved change. Use only for one-shot init, not user edits.
   */
  rebase: (next: T) => void;
  /** A saved preset is the active baseline (i.e. values came from storage). */
  saved: boolean;
  /** The form differs from the active baseline (saved preset, or defaults). */
  dirty: boolean;
  /** A saved preset existed when the hook first mounted (for one-shot defaults). */
  hadSavedAtMount: boolean;
}

/**
 * useState for a screener param form, with explicit localStorage persistence.
 *
 * On mount the form initializes from a previously saved preset (if any), else
 * from `defaults`. `save()` writes the current values under a per-screener key;
 * `reset()` clears them and restores `defaults`. `dirty`/`saved` drive the
 * Save/Reset affordances (see ScreenerParamActions).
 *
 * Param forms are flat objects of primitives, so baseline equality is a JSON
 * compare. `key` must be stable per screener (e.g. 'vcp', 'short', 'bottomFlow').
 */
export function usePersistentForm<T extends object>(key: string, defaults: T): PersistentForm<T> {
  const initial = useRef<T | null>(readStored<T>(key));
  const hadSavedAtMount = useRef(initial.current != null).current;
  const [form, setForm] = useState<T>(() => initial.current ?? defaults);
  const [baseline, setBaseline] = useState<T>(() => initial.current ?? defaults);
  const [saved, setSaved] = useState(initial.current != null);

  const save = useCallback(() => {
    try {
      localStorage.setItem(PREFIX + key, JSON.stringify(form));
    } catch {
      /* storage unavailable (private mode / quota) — keep the in-memory form */
    }
    setBaseline(form);
    setSaved(true);
  }, [key, form]);

  const reset = useCallback(() => {
    try {
      localStorage.removeItem(PREFIX + key);
    } catch {
      /* ignore */
    }
    setForm(defaults);
    setBaseline(defaults);
    setSaved(false);
  }, [key, defaults]);

  const rebase = useCallback((next: T) => {
    setForm(next);
    setBaseline(next);
  }, []);

  const dirty = useMemo(() => JSON.stringify(form) !== JSON.stringify(baseline), [form, baseline]);

  return { form, setForm, save, reset, rebase, saved, dirty, hadSavedAtMount };
}
