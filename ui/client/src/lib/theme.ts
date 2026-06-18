import { useEffect, useState } from 'react';

export type Theme = 'dark' | 'light';

const KEY = 'trading-ui-theme';

export function getStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    if (v === 'light' || v === 'dark') return v;
  } catch {
    /* localStorage unavailable */
  }
  // White/parchment gallery is the brand default (see ui/DESIGN.md); dark = derived near-black-tile variant.
  return 'light';
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

/** Apply the persisted theme as early as possible (called from main.tsx before render). */
export function initTheme(): void {
  applyTheme(getStoredTheme());
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(getStoredTheme);

  useEffect(() => {
    applyTheme(theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const toggle = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'));
  return [theme, toggle];
}
