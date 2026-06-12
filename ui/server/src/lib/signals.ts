import fs from 'node:fs';
import path from 'node:path';
import type { AnalysisSignal, DeleteSignalResponse, SignalBlock } from '@shared/types';

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

/* ---------------- signal level parsing (mirrors parse_signals.mjs) ---------------- */

function firstDollar(s: string): number | null {
  const m = s.match(/\$\s*(\d+(?:\.\d+)?)/);
  if (m) return parseFloat(m[1]);
  const n = s.match(/(\d+(?:\.\d+)?)/);
  return n ? parseFloat(n[1]) : null;
}

function allDollars(s: string): number[] {
  const out: number[] = [];
  const re = /\$\s*(\d+(?:\.\d+)?)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s))) out.push(parseFloat(m[1]));
  if (out.length === 0) {
    const re2 = /(\d+(?:\.\d+)?)/g;
    while ((m = re2.exec(s))) out.push(parseFloat(m[1]));
  }
  return out;
}

/**
 * Parse the priority-scenario levels (direction + Trigger/Stop/T1/T2/T3 + entry
 * range) from one signals.md block. Mirrors parse_signals.mjs so the numbers
 * match what alert creation would use. Alternative-scenario lines are skipped.
 * Returns null when direction or the core levels are missing.
 */
export function parseSignalLevels(block: SignalBlock): AnalysisSignal | null {
  // A 🟡 HOLD block is a wait-read: even when it carries a Trigger line (it
  // should not, per ticker-analysis SKILL.md), it must not arm levels — a
  // HOLD once flipped a grade-A screener short into a "validated" long.
  if (block.status && (/\bHOLD\b/i.test(block.status) || block.status.includes('🟡'))) return null;
  const lines = block.markdown.split('\n');
  let direction: 'long' | 'short' | null = /🟢\s*BUY/.test(block.markdown)
    ? 'long'
    : /🔴\s*SELL/.test(block.markdown)
      ? 'short'
      : null;

  let triggerLine: string | null = null;
  for (const l of lines) {
    const m = l.match(/\*\*Trigger\s+(?:для\s+)?(Long|Short)\b[^:]*:\*\*\s*(.+)$/i);
    if (m) {
      triggerLine = m[2];
      if (!direction) direction = /long/i.test(m[1]) ? 'long' : 'short';
      break;
    }
  }
  if (!triggerLine) return null;
  const trigger = firstDollar(triggerLine);

  let stop: number | null = null;
  for (const l of lines) {
    if (/Альтернатив[ау]/i.test(l)) continue;
    const m = l.match(/\*\*Stop:\*\*\s*(.+)$/i);
    if (m) {
      stop = firstDollar(m[1]);
      break;
    }
  }

  let t1: number | null = null;
  let t2: number | null = null;
  let t3: number | null = null;
  for (const l of lines) {
    if (/Альтернатив[ау]/i.test(l)) continue;
    const m = l.match(/\*\*T1(?:\s*\/\s*T2)?(?:\s*\/\s*T3)?:\*\*\s*(.+)$/i);
    if (m) {
      const nums = allDollars(m[1]);
      t1 = nums[0] ?? null;
      t2 = nums[1] ?? null;
      t3 = nums[2] ?? null;
      break;
    }
  }

  let entryLow: number | null = null;
  let entryHigh: number | null = null;
  for (const l of lines) {
    if (/Альтернатив[ау]/i.test(l)) continue;
    const m = l.match(/\*\*Entry[^:]*:\*\*\s*(.+)$/i);
    if (m) {
      const nums = allDollars(m[1]);
      if (nums.length >= 1) {
        entryLow = Math.min(...nums);
        entryHigh = Math.max(...nums);
      }
      break;
    }
  }

  if (direction == null || trigger == null || stop == null || t1 == null) return null;
  return { ticker: block.ticker, date: block.date, direction, trigger, stop, t1, t2, t3, entryLow, entryHigh };
}

/** Latest analysis signal for a ticker from signals.md (or null). */
export function getAnalysisSignal(dataDir: string, ticker: string): AnalysisSignal | null {
  const file = signalsFile(dataDir);
  let text: string;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
  const blocks = parseSignalBlocks(text).filter((b) => b.ticker === ticker.toUpperCase());
  if (blocks.length === 0) return null;
  return parseSignalLevels(blocks[blocks.length - 1]);
}
