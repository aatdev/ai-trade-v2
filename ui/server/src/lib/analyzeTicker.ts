/**
 * Builds the prompt + argv for a headless ticker-analysis run driven by the
 * `claude-p` wrapper (a drop-in `claude -p` emulator).
 *
 * `claude-p` differs from `claude` in two ways that matter here:
 *   1. It REJECTS `-p`/`--print` (it emulates that mode itself).
 *   2. It takes the prompt as a trailing POSITIONAL argument, not after `-p`.
 *
 * The argv therefore mirrors the proven scheduler pattern in
 * `scripts/run_trading_schedule.py` (`_run_ticker_analysis`): all flags first,
 * `--mcp-config <path> --strict-mcp-config` (the boolean flag terminates the
 * variadic `--mcp-config`), then the prompt LAST as the positional `PROMPT`.
 *
 * Kept pure (no process spawn, no env reads) so the command shape is unit
 * testable, matching the `buildMemoryArgs` pattern.
 */

export interface AnalyzeTickerOpts {
  /** Validated, upper-cased ticker (caller enforces the format). */
  ticker: string;
  createAlerts: boolean;
  saveToNotes: boolean;
  /** --permission-mode value (e.g. bypassPermissions). */
  permissionMode: string;
  /** --model value. */
  model: string;
  /** Resolved --mcp-config path, or null to skip MCP wiring. */
  mcpConfig: string | null;
  /** claude-p --timeout wall-time cap, in seconds. */
  timeoutSec: number;
}

/** Compose the Russian ticker-analysis prompt sent to the `ticker-analysis` skill. */
export function buildAnalyzeTickerPrompt(
  ticker: string,
  createAlerts: boolean,
  saveToNotes: boolean,
): string {
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
  return parts.join(' ');
}

/**
 * Build the argv passed to `claude-p` (after the binary). The prompt is the
 * final element — claude-p captures the trailing positional as `PROMPT`.
 */
export function buildAnalyzeTickerArgs(opts: AnalyzeTickerOpts): string[] {
  const prompt = buildAnalyzeTickerPrompt(opts.ticker, opts.createAlerts, opts.saveToNotes);
  // Run on the configured model; stream events so the UI shows live progress.
  const args = [
    '--permission-mode',
    opts.permissionMode,
    '--model',
    opts.model,
    '--output-format',
    'stream-json',
    '--verbose',
    // claude-p's own wall-time cap (default 300s is too short for a full run).
    '--timeout',
    String(opts.timeoutSec),
  ];
  // Load the vendored TradingView MCP server so the skill gets mcp__tradingview__*.
  // --strict-mcp-config => only this server (deterministic; ignores whatever
  // other MCP servers the user has configured). Built-in tools/skills are
  // unaffected. The tools surface as deferred tools (found via ToolSearch).
  // --strict-mcp-config (a boolean flag) also terminates the variadic
  // --mcp-config, so the trailing prompt is NOT swallowed into its value list.
  if (opts.mcpConfig) args.push('--mcp-config', opts.mcpConfig, '--strict-mcp-config');
  // Prompt LAST: claude-p takes it as the positional PROMPT (no `-p`).
  args.push(prompt);
  return args;
}
