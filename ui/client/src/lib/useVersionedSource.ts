import { useState } from 'react';

/**
 * Local state for a pinned file version (a `source` basename, '' = latest).
 * Resets to latest whenever the global `date` changes, so switching dates never
 * leaves a card showing a stale snapshot from a different day. Uses the
 * store-previous-prop render-phase reset pattern (no effect, no flash).
 */
export function useVersionedSource(date: string | null): [string, (source: string) => void] {
  const [state, setState] = useState<{ date: string | null; source: string }>({ date, source: '' });
  if (state.date !== date) setState({ date, source: '' });
  const source = state.date === date ? state.source : '';
  const setSource = (next: string) => setState({ date, source: next });
  return [source, setSource];
}
