import { describe, expect, it } from 'vitest';
import { buildAnalyzeTickerArgs, buildAnalyzeTickerPrompt } from './analyzeTicker';

const BASE = {
  ticker: 'AAPL',
  createAlerts: false,
  saveToNotes: false,
  permissionMode: 'bypassPermissions',
  model: 'claude-opus-4-8',
  mcpConfig: '/tmp/tv-mcp.json',
  timeoutSec: 1800,
  usesClaudeP: false, // default: plain `claude -p`
};

describe('buildAnalyzeTickerArgs (plain claude -p, default)', () => {
  it('adds -p (plain claude needs it) before the prompt', () => {
    const args = buildAnalyzeTickerArgs(BASE);
    expect(args[0]).toBe('-p');
    expect(args.indexOf('-p')).toBeLessThan(args.length - 1);
  });

  it('omits --timeout (plain claude has no such flag)', () => {
    const args = buildAnalyzeTickerArgs(BASE);
    expect(args).not.toContain('--timeout');
  });

  it('passes the prompt as the trailing positional, not after a flag', () => {
    const args = buildAnalyzeTickerArgs(BASE);
    const prompt = args[args.length - 1];
    expect(prompt).toContain('AAPL');
    expect(prompt).toContain('ticker-analysis');
    expect(args[args.length - 2]).toBe('--strict-mcp-config');
  });

  it('streams events and carries permission-mode + model', () => {
    const args = buildAnalyzeTickerArgs(BASE);
    expect(args).toEqual(
      expect.arrayContaining([
        '--permission-mode',
        'bypassPermissions',
        '--model',
        'claude-opus-4-8',
        '--output-format',
        'stream-json',
        '--verbose',
      ]),
    );
  });

  it('wires the TradingView MCP config and keeps the prompt out of the variadic', () => {
    const args = buildAnalyzeTickerArgs(BASE);
    const i = args.indexOf('--mcp-config');
    expect(i).toBeGreaterThanOrEqual(0);
    expect(args[i + 1]).toBe('/tmp/tv-mcp.json');
    // --strict-mcp-config (a boolean flag) terminates the variadic --mcp-config
    // so the trailing prompt is not absorbed as another config value.
    expect(args[i + 2]).toBe('--strict-mcp-config');
    expect(args[i + 3]).toBe(args[args.length - 1]); // the prompt
  });

  it('omits MCP flags when no config is resolved', () => {
    const args = buildAnalyzeTickerArgs({ ...BASE, mcpConfig: null });
    expect(args).not.toContain('--mcp-config');
    expect(args).not.toContain('--strict-mcp-config');
    expect(args[args.length - 1]).toContain('AAPL'); // prompt still last
  });
});

describe('buildAnalyzeTickerArgs (claude-p wrapper convention)', () => {
  const CP = { ...BASE, usesClaudeP: true };

  it('never uses -p/--print (claude-p rejects them)', () => {
    const args = buildAnalyzeTickerArgs(CP);
    expect(args).not.toContain('-p');
    expect(args).not.toContain('--print');
  });

  it('carries the claude-p --timeout wall-time cap', () => {
    const args = buildAnalyzeTickerArgs(CP);
    expect(args).toEqual(expect.arrayContaining(['--timeout', '1800']));
  });

  it('still passes the prompt as the trailing positional after --strict-mcp-config', () => {
    const args = buildAnalyzeTickerArgs(CP);
    expect(args[args.length - 2]).toBe('--strict-mcp-config');
    expect(args[args.length - 1]).toContain('AAPL');
  });
});

describe('buildAnalyzeTickerPrompt', () => {
  it('instructs NOT to create alerts by default', () => {
    const p = buildAnalyzeTickerPrompt('TSLA', false, false);
    expect(p).toContain('Алерты в TradingView НЕ создавай');
    expect(p).not.toContain('save-note');
  });

  it('asks to create alerts when requested', () => {
    const p = buildAnalyzeTickerPrompt('TSLA', true, false);
    expect(p).toContain('СОЗДАЙ алерты');
    expect(p).toContain('signals-alerts');
  });

  it('asks to save to MyNotes when requested', () => {
    const p = buildAnalyzeTickerPrompt('NVDA', false, true);
    expect(p).toContain('save-note');
    expect(p).toContain('Анализ-тикеров/NVDA');
  });
});
