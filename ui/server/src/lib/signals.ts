import fs from 'node:fs';
import path from 'node:path';
import type { DeleteSignalResponse, SignalBlock } from '@shared/types';

/**
 * Signals journal helpers. The file format matches what the scheduler / the
 * signals-alerts skill produce (see skills/signals-alerts/scripts/prune_signals.mjs):
 * a preamble, then one block per signal separated by `\n---\n`, each block
 * starting with `## YYYY-MM-DD — TICKER — STATUS`.
 */

const HEADING_RE = /^##\s+(\d{4}-\d{2}-\d{2})\s*[—-]\s*([A-Za-z0-9.\-]+)\s*(?:[—-]\s*(.*))?$/m;

export function signalsFile(dataDir: string): string {
  return path.join(dataDir, 'analysis', 'signals.md');
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Split into preamble + signal blocks, dropping empty trailing chunks. */
export function splitSignals(raw: string): { head: string; blocks: string[] } {
  const parts = raw.split(/\n---\n/);
  return { head: parts[0] ?? '', blocks: parts.slice(1).filter((b) => b.trim() !== '') };
}

export function parseSignalBlocks(raw: string): SignalBlock[] {
  const { blocks } = splitSignals(raw);
  const out: SignalBlock[] = [];
  blocks.forEach((b, i) => {
    const m = b.match(HEADING_RE);
    if (!m) return;
    const [heading, date, ticker, status] = m;
    out.push({
      id: `${date}__${ticker.toUpperCase()}__${i}`,
      date,
      ticker: ticker.toUpperCase(),
      heading: heading.replace(/^##\s+/, '').trim(),
      status: status?.trim() || null,
      markdown: b.trim(),
    });
  });
  return out;
}

/** Re-join head + remaining blocks, preserving the trailing `\n---\n` convention. */
function rejoin(head: string, kept: string[]): string {
  if (kept.length === 0) return `${head.replace(/\n+$/, '')}\n`;
  return `${[head, ...kept].join('\n---\n').replace(/\n+$/, '')}\n---\n`;
}

/**
 * Delete the signal block(s) for a given ticker AND date, in place.
 * Returns how many blocks were removed/kept and whether anything matched.
 */
export function deleteSignal(dataDir: string, ticker: string, date: string): DeleteSignalResponse {
  const file = signalsFile(dataDir);
  const T = ticker.toUpperCase();
  if (!fs.existsSync(file)) return { removed: 0, kept: 0, ticker: T, date, found: false };

  const raw = fs.readFileSync(file, 'utf8');
  const { head, blocks } = splitSignals(raw);
  const matchRe = new RegExp(`^##\\s+${escapeRegex(date)}\\s*[—-]\\s*${escapeRegex(T)}\\s*[—-]`, 'm');

  const kept: string[] = [];
  let removed = 0;
  for (const b of blocks) {
    if (matchRe.test(b)) removed += 1;
    else kept.push(b);
  }
  if (removed > 0) fs.writeFileSync(file, rejoin(head, kept));
  return { removed, kept: kept.length, ticker: T, date, found: removed > 0 };
}
