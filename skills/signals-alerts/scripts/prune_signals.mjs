#!/usr/bin/env node
/**
 * prune_signals.mjs — удаляет все блоки сигналов по указанному тикеру
 * из `results/analysis/signals.md` (in-place).
 *
 * Назначение: перед дозаписью свежего сигнала по TICKER (Шаг 5 в
 * ticker-analysis) старые блоки этого же тикера должны быть удалены,
 * чтобы в журнале оставалась ровно одна актуальная запись на тикер.
 * Историческая копия отчёта по-прежнему лежит в
 * `results/analysis/TICKER/DATE/report.md`.
 *
 * Блок — это секция от заголовка `## YYYY-MM-DD — TICKER — STATUS`
 * до следующего разделителя `---`. Совпадение тикера определяется
 * по заголовку (case-sensitive — тикеры всегда uppercase).
 *
 * CLI:
 *   node prune_signals.mjs --ticker TICKER [--input results/analysis/signals.md] [--dry-run]
 *
 * Выход (stdout):
 *   { removed: N, kept: M, ticker: "TICKER", source: "...", file_missing?: true }
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '../../..');
const DEFAULT_INPUT = path.join(REPO_ROOT, 'reports/analysis/signals.md');

function parseArgs(argv) {
  const out = { input: DEFAULT_INPUT, ticker: null, dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--input' || a === '-i') out.input = argv[++i];
    else if (a === '--ticker' || a === '-t') out.ticker = (argv[++i] || '').trim().toUpperCase();
    else if (a === '--dry-run' || a === '-n') out.dryRun = true;
    else if (a === '--help' || a === '-h') {
      console.log('Usage: prune_signals.mjs --ticker TICKER [--input PATH] [--dry-run]');
      process.exit(0);
    }
  }
  if (!out.ticker) {
    console.error('Error: --ticker is required');
    process.exit(2);
  }
  return out;
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function main() {
  const args = parseArgs(process.argv.slice(2));

  if (!fs.existsSync(args.input)) {
    process.stdout.write(
      JSON.stringify({ removed: 0, kept: 0, ticker: args.ticker, source: args.input, file_missing: true }) + '\n',
    );
    return;
  }

  const raw = fs.readFileSync(args.input, 'utf8');
  const sepRe = /\n---\n/;
  const parts = raw.split(sepRe);

  // parts[0] — преамбула (заголовок журнала + описание), её всегда сохраняем.
  // parts[1..] — блоки сигналов; последний может быть пустым, если файл оканчивается на `\n---\n`.
  const head = parts[0];
  const rawBlocks = parts.slice(1);
  const blocks = rawBlocks.filter((b) => b.trim() !== '');

  const tickerRe = new RegExp(
    `^##\\s+\\d{4}-\\d{2}-\\d{2}\\s*[—\\-]\\s*${escapeRegex(args.ticker)}\\s*[—\\-]`,
    'm',
  );

  const kept = [];
  let removed = 0;
  for (const b of blocks) {
    if (tickerRe.test(b)) removed++;
    else kept.push(b);
  }

  let output;
  if (kept.length === 0) {
    output = head.replace(/\n+$/, '') + '\n';
  } else {
    output = [head, ...kept].join('\n---\n').replace(/\n+$/, '') + '\n---\n';
  }

  if (!args.dryRun && removed > 0) {
    fs.writeFileSync(args.input, output);
  }

  process.stdout.write(
    JSON.stringify({
      removed,
      kept: kept.length,
      ticker: args.ticker,
      source: args.input,
      dry_run: args.dryRun,
    }) + '\n',
  );
}

main();
