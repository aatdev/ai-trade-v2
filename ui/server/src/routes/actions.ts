import path from 'node:path';
import { Router } from 'express';
import { ANALYZE_MODEL, resolveMcpConfigPath } from '../config';
import { buildMemoryArgs } from '../lib/memoryOps';
import type { JobManager } from '../lib/jobs';
import type { SchedulerSlot, StartJobResponse } from '@shared/types';

const SLOTS = new Set<SchedulerSlot>(['premarket', 'evening-prep', 'intraday', 'weekly', 'monthly']);
const TICKER_RE = /^[A-Z0-9.\-]{1,10}$/;
const TRADER_MEMORY_CLI = 'skills/trader-memory-core/scripts/trader_memory_cli.py';

function resolveClaudeBin(): string {
  return process.env.CLAUDE_BIN || 'claude';
}

function resolvePythonBin(): string {
  return process.env.PYTHON_BIN || 'python3';
}

export function actionsRouter(projectRoot: string, dataDir: string, jobs: JobManager): Router {
  const r = Router();

  function startAndRespond(
    res: import('express').Response,
    opts: Parameters<JobManager['start']>[0],
  ): void {
    const result = jobs.start(opts);
    if (result.busy) {
      const body: StartJobResponse = { ok: false, busy: true, activeJobId: result.activeJobId };
      res.status(409).json(body);
      return;
    }
    const body: StartJobResponse = {
      ok: true,
      job: {
        id: result.job.id,
        label: result.job.label,
        status: result.job.status,
        startedAt: result.job.startedAt,
        endedAt: result.job.endedAt,
        exitCode: result.job.exitCode,
      },
    };
    res.json(body);
  }

  r.post('/actions/run-slot', (req, res) => {
    const slot = req.body?.slot as SchedulerSlot;
    if (!SLOTS.has(slot)) {
      const body: StartJobResponse = { ok: false, error: `unknown slot: ${String(slot)}` };
      return res.status(400).json(body);
    }
    // Default to the SAFE option: dry-run unless explicitly disabled.
    const dryRun = req.body?.dryRun !== false;
    const args = ['scripts/run_trading_schedule.sh', '--slot', slot];
    if (dryRun) args.push('--dry-run');
    if (req.body?.force === true) args.push('--force');
    if (req.body?.noTelegram === true) args.push('--no-telegram');
    return startAndRespond(res, {
      label: `run-slot ${slot}${dryRun ? ' (dry-run)' : ''}`,
      cmd: 'bash',
      args,
      cwd: projectRoot,
    });
  });

  r.post('/actions/sync-alerts', (_req, res) => {
    // Fixed, input-free pipeline: parse signals.md -> create missing alerts (diff).
    const pipeline =
      'set -a; [ -f .env ] && . ./.env; set +a; ' +
      'node skills/signals-alerts/scripts/parse_signals.mjs | ' +
      'node skills/signals-alerts/scripts/create_alerts.mjs';
    return startAndRespond(res, {
      label: 'sync-alerts (signals.md → TradingView)',
      cmd: pipeline,
      args: [],
      cwd: projectRoot,
      shell: true,
    });
  });

  r.post('/actions/delete-alerts', (req, res) => {
    const tickers = Array.isArray(req.body?.tickers) ? (req.body.tickers as unknown[]) : [];
    const clean = tickers.map((t) => String(t).toUpperCase().trim()).filter((t) => TICKER_RE.test(t));
    if (clean.length === 0) {
      const body: StartJobResponse = { ok: false, error: 'no valid tickers' };
      return res.status(400).json(body);
    }
    return startAndRespond(res, {
      label: `delete-alerts ${clean.join(',')}`,
      cmd: 'node',
      args: ['skills/signals-alerts/scripts/delete_alerts.mjs', '--tickers', clean.join(',')],
      cwd: projectRoot,
    });
  });

  r.post('/actions/analyze-ticker', (req, res) => {
    const ticker = String(req.body?.ticker ?? '').toUpperCase().trim();
    if (!TICKER_RE.test(ticker)) {
      const body: StartJobResponse = { ok: false, error: `invalid ticker: ${String(req.body?.ticker)}` };
      return res.status(400).json(body);
    }
    const perm = process.env.TRADING_SCHEDULE_PERMISSION_MODE || 'bypassPermissions';
    const createAlerts = req.body?.createAlerts === true;
    const saveToNotes = req.body?.saveToNotes === true;
    const parts = [
      `Проанализируй тикер ${ticker}: запусти скил ticker-analysis — полный комплексный анализ`,
      `(новости, фундаментал, технический анализ через TradingView MCP). Сохрани четыре markdown-файла`,
      `и daily/weekly скриншоты в trading-data/analysis/${ticker}/.`,
      createAlerts
        ? `После анализа СОЗДАЙ алерты в TradingView по приоритетному сценарию (Trigger / Stop / T1 / T2 / T3) — используй скил signals-alerts.`
        : `Алерты в TradingView НЕ создавай.`,
    ];
    if (saveToNotes) {
      parts.push(
        `Также СОХРАНИ итоговый отчёт в личную базу MyNotes через скил save-note (подкаталог «Анализ-тикеров/${ticker}»).`,
      );
    }
    const prompt = parts.join(' ');
    // Run on Opus 4.8; stream events so the UI shows live progress (each step).
    const args = [
      '-p',
      prompt,
      '--permission-mode',
      perm,
      '--model',
      ANALYZE_MODEL,
      '--output-format',
      'stream-json',
      '--verbose',
    ];
    // Load the vendored TradingView MCP server so the skill gets mcp__tradingview__*.
    // --strict-mcp-config => only this server (deterministic; ignores whatever
    // other MCP servers the user has configured). Built-in tools/skills are
    // unaffected. The tools surface as deferred tools (found via ToolSearch).
    const mcpConfig = resolveMcpConfigPath(projectRoot);
    if (mcpConfig) args.push('--mcp-config', mcpConfig, '--strict-mcp-config');

    return startAndRespond(res, {
      label: `analyze ${ticker} (${ANALYZE_MODEL})`,
      cmd: resolveClaudeBin(),
      args,
      cwd: projectRoot,
      env: { TV_NO_CACHE: '1' },
      meta: { kind: 'analyze-ticker', ticker, model: ANALYZE_MODEL, createAlerts, saveToNotes },
    });
  });

  r.post('/actions/memory', (req, res) => {
    const stateDir = path.join(dataDir, 'journal', 'theses');
    const built = buildMemoryArgs((req.body ?? {}) as Record<string, unknown>, stateDir);
    if ('error' in built) {
      const body: StartJobResponse = { ok: false, error: built.error };
      return res.status(400).json(body);
    }
    return startAndRespond(res, {
      label: built.label,
      cmd: resolvePythonBin(),
      args: [TRADER_MEMORY_CLI, ...built.args],
      cwd: projectRoot,
      env: { TRADING_DATE_DIR: dataDir, CLAUDE_TRADING_SKILLS_REPO: projectRoot },
      meta: { kind: 'memory', op: String((req.body as Record<string, unknown>)?.op ?? '') },
    });
  });

  r.get('/actions/jobs', (_req, res) => {
    res.json({ jobs: jobs.list(), active: jobs.active });
  });

  r.post('/actions/jobs/:id/cancel', (req, res) => {
    const ok = jobs.cancel(req.params.id);
    return res.status(ok ? 200 : 409).json({ ok });
  });

  r.get('/actions/jobs/:id', (req, res) => {
    const job = jobs.get(req.params.id);
    if (!job) return res.status(404).json({ error: 'job not found' });
    return res.json(job);
  });

  r.get('/actions/jobs/:id/stream', (req, res) => {
    const id = req.params.id;
    const job = jobs.get(id);
    if (!job) return res.status(404).json({ error: 'job not found' });

    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no');
    res.flushHeaders?.();

    const send = (event: string, data: unknown) => {
      res.write(`event: ${event}\n`);
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    };

    // Replay buffered output, then stream live.
    for (const line of job.lines) send('log', line);
    if (job.status !== 'running') {
      send('end', { id: job.id, status: job.status, exitCode: job.exitCode });
      return res.end();
    }
    const unsubscribe = jobs.subscribe(
      id,
      (line) => send('log', line),
      (summary) => {
        send('end', { id: summary.id, status: summary.status, exitCode: summary.exitCode });
        res.end();
      },
    );
    req.on('close', unsubscribe);
    return undefined;
  });

  return r;
}
