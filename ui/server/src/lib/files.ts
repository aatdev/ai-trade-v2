import fs from 'node:fs';
import path from 'node:path';

const TTL_MS = 5000;
const DATE_RE = /\d{4}-\d{2}-\d{2}/;

interface CacheEntry {
  at: number;
  names: string[];
}
const listCache = new Map<string, CacheEntry>();

/** Cached, fail-safe directory listing (filenames only, no dotfiles). */
export function listDir(dir: string): string[] {
  const now = Date.now();
  const cached = listCache.get(dir);
  if (cached && now - cached.at < TTL_MS) return cached.names;
  let names: string[] = [];
  try {
    names = fs.readdirSync(dir).filter((n) => !n.startsWith('.'));
  } catch {
    names = [];
  }
  listCache.set(dir, { at: now, names });
  return names;
}

/** Clear the listing cache (used by tests). */
export function clearListCache(): void {
  listCache.clear();
}

/**
 * Find the most recent file in `dir` whose name matches `pattern`. Filenames
 * embed `YYYY-MM-DD[_HHMMSS]`, which sorts lexicographically by recency, so the
 * last entry after a plain sort is the newest. When `date` is given, only files
 * containing that date token are considered.
 */
export function findLatest(dir: string, pattern: RegExp, date?: string | null): string | null {
  let names = listDir(dir).filter((n) => pattern.test(n));
  if (date) names = names.filter((n) => n.includes(date));
  if (names.length === 0) return null;
  names.sort();
  return path.join(dir, names[names.length - 1]);
}

/** Collect every distinct YYYY-MM-DD date token across the given directories. */
export function listDates(dirs: string[]): string[] {
  const seen = new Set<string>();
  for (const dir of dirs) {
    for (const name of listDir(dir)) {
      const m = name.match(DATE_RE);
      if (m) seen.add(m[0]);
    }
  }
  return Array.from(seen).sort().reverse();
}

export function readJson<T = unknown>(file: string | null): T | null {
  if (!file) return null;
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8')) as T;
  } catch {
    return null;
  }
}

export function readText(file: string | null): string | null {
  if (!file) return null;
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
}

/** Last `n` lines of a text file (fail-safe, returns [] if unreadable). */
export function tailLines(file: string | null, n: number): string[] {
  const text = readText(file);
  if (text == null) return [];
  const lines = text.split(/\r?\n/);
  if (lines.length && lines[lines.length - 1] === '') lines.pop();
  return lines.slice(Math.max(0, lines.length - n));
}

export function basename(file: string | null): string | null {
  return file ? path.basename(file) : null;
}
