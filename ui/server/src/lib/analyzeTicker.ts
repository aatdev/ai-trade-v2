/**
 * Builds the prompt + argv for a headless ticker-analysis run.
 *
 * Two calling conventions, selected by `usesClaudeP`:
 *   - plain `claude` (default): needs `-p`/`--print` to run headless, and has no
 *     `--timeout` flag (the wall-time cap is enforced by the JobManager timer).
 *   - `claude-p`/`claude-pee` wrapper: REJECTS `-p` (emulates that mode itself)
 *     and instead accepts `--timeout`. It takes the prompt as a trailing
 *     positional either way.
 *
 * The argv mirrors the scheduler pattern in `scripts/run_trading_schedule.py`
 * (`run_claude` + `_print_flags`): print flag (plain claude only), then all
 * flags, `--mcp-config <path> --strict-mcp-config` (the boolean flag terminates
 * the variadic `--mcp-config`), then the prompt LAST as the positional `PROMPT`.
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
  /** claude-p --timeout wall-time cap, in seconds (only emitted for a wrapper). */
  timeoutSec: number;
  /**
   * True when the resolved binary is a claude-p/claude-pee wrapper (rejects `-p`,
   * accepts `--timeout`); false → a plain `claude -p` invocation (the default).
   */
  usesClaudeP: boolean;
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
  const args: string[] = [];
  // Plain `claude` needs -p/--print to run headless; a claude-p wrapper emulates
  // that mode itself and REJECTS -p. Print flag goes first (before the prompt).
  if (!opts.usesClaudeP) args.push('-p');
  // Run on the configured model; stream events so the UI shows live progress.
  args.push(
    '--permission-mode',
    opts.permissionMode,
    '--model',
    opts.model,
    '--output-format',
    'stream-json',
    '--verbose',
  );
  // --timeout is a claude-p wrapper flag (its 300s default is too short for a
  // full run); plain claude has no such flag, so the JobManager timer enforces
  // the wall-time cap instead.
  if (opts.usesClaudeP) args.push('--timeout', String(opts.timeoutSec));
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
