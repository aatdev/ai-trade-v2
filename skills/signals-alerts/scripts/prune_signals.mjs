#!/usr/bin/env node
/**
 * prune_signals.mjs — удаляет все блоки сигналов по указанному тикеру
 * из журнала `signals.md` (по умолчанию `$TRADING_DATE_DIR/analysis/signals.md`,
 * in-place).
 *
 * Назначение: перед дозаписью свежего сигнала по TICKER (Шаг 5 в
 * ticker-analysis) старые блоки этого же тикера должны быть удалены,
 * чтобы в журнале оставалась ровно одна актуальная запись на тикер.
 * Историческая копия отчёта по-прежнему лежит в
 * `$TRADING_DATE_DIR/analysis/TICKER/DATE/report.md`.
 *
 * Блок — это секция от заголовка `## YYYY-MM-DD — TICKER — STATUS`
 * до следующего разделителя `---`. Совпадение тикера определяется
 * по заголовку (case-sensitive — тикеры всегда uppercase).
 *
 * CLI:
 *   node prune_signals.mjs --ticker TICKER [--input trading-data/analysis/signals.md] [--dry-run]
 *
 * Выход (stdout):
 *   { removed: N, kept: M, ticker: "TICKER", source: "...", file_missing?: true }
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '../../..');

// Корень торговых артефактов: $TRADING_DATE_DIR (env или строка в repo .env).
function tradingDataDir() {
  let base = process.env.TRADING_DATE_DIR;
  if (!base) {
    try {
      for (let line of fs.readFileSync(path.join(REPO_ROOT, '.env'), 'utf8').split('\n')) {
        line = line.trim().replace(/^export\s+/, '');
        if (line.startsWith('TRADING_DATE_DIR=')) {
          base = line.slice('TRADING_DATE_DIR='.length).trim().replace(/^['"]|['"]$/g, '');
          break;
        }
      }
    } catch { /* нет .env — используем старый путь */ }
  }
  if (!base) return null;
  return path.isAbsolute(base) ? base : path.join(REPO_ROOT, base);
}

const TRADING_DATA_DIR = tradingDataDir();
const DEFAULT_INPUT = TRADING_DATA_DIR
  ? path.join(TRADING_DATA_DIR, 'analysis/signals.md')
  : path.join(REPO_ROOT, 'reports/analysis/signals.md');

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
