import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { classifyBarSession, parseTvBars, resolveTvCli } from './ohlcv';

/** Unix seconds for a given UTC wall-clock. */
const utc = (y: number, mo: number, d: number, h: number, mi = 0) =>
  Math.floor(Date.UTC(y, mo - 1, d, h, mi, 0) / 1000);

describe('classifyBarSession', () => {
  // June → EDT (UTC-4): subtract 4h from ET to get UTC.
  it('classifies the EDT (summer) windows by New-York wall-clock', () => {
    expect(classifyBarSession(utc(2026, 6, 18, 12, 0))).toBe('pre'); // 08:00 ET
    expect(classifyBarSession(utc(2026, 6, 18, 13, 30))).toBe('rth'); // 09:30 ET (open)
    expect(classifyBarSession(utc(2026, 6, 18, 14, 0))).toBe('rth'); // 10:00 ET
    expect(classifyBarSession(utc(2026, 6, 18, 20, 0))).toBe('post'); // 16:00 ET (close)
    expect(classifyBarSession(utc(2026, 6, 18, 21, 0))).toBe('post'); // 17:00 ET
    expect(classifyBarSession(utc(2026, 6, 18, 6, 0))).toBeNull(); // 02:00 ET (overnight)
  });

  // January → EST (UTC-5): subtract 5h. Confirms the formatter is DST-aware.
  it('shifts the windows correctly under EST (winter)', () => {
    expect(classifyBarSession(utc(2026, 1, 15, 13, 0))).toBe('pre'); // 08:00 ET
    expect(classifyBarSession(utc(2026, 1, 15, 14, 30))).toBe('rth'); // 09:30 ET
    expect(classifyBarSession(utc(2026, 1, 15, 21, 30))).toBe('post'); // 16:30 ET
  });
});

describe('resolveTvCli', () => {
  const orig = { bin: process.env.TRADING_UI_TV_BIN, cli: process.env.TV_CLI, pathEnv: process.env.PATH };
  afterEach(() => {
    process.env.TRADING_UI_TV_BIN = orig.bin;
    process.env.TV_CLI = orig.cli;
    process.env.PATH = orig.pathEnv;
    if (orig.bin === undefined) delete process.env.TRADING_UI_TV_BIN;
    if (orig.cli === undefined) delete process.env.TV_CLI;
  });

  it('honors the TRADING_UI_TV_BIN override verbatim', () => {
    process.env.TRADING_UI_TV_BIN = '/custom/tv';
    expect(resolveTvCli('/repo')).toEqual({ cmd: '/custom/tv', prefixArgs: [] });
  });

  it('falls back to the vendored node CLI when tv is not on PATH', () => {
    delete process.env.TRADING_UI_TV_BIN;
    delete process.env.TV_CLI;
    process.env.PATH = ''; // no `tv` discoverable
    // Resolves against the real repo root so vendor/tradingview-mcp/src/cli/index.js exists.
    const cli = resolveTvCli();
    expect(cli).not.toBeNull();
    expect(cli?.cmd).toBe(process.execPath);
    expect(cli?.prefixArgs[0]).toContain(path.join('vendor', 'tradingview-mcp', 'src', 'cli', 'index.js'));
  });

  it('returns null when nothing resolves (no override, no PATH tv, no vendored entry)', () => {
    delete process.env.TRADING_UI_TV_BIN;
    delete process.env.TV_CLI;
    process.env.PATH = '';
    expect(resolveTvCli('/nonexistent-repo-root')).toBeNull();
  });
});

describe('parseTvBars session tagging', () => {
  const envelope = (bars: object[]) => ({
    success: true,
    results: [{ symbol: 'AAPL', success: true, resolved: 'NASDAQ:AAPL', bars }],
  });
  // 08:00 ET (pre) and 10:00 ET (rth) on a June (EDT) day.
  const bars = [
    { time: utc(2026, 6, 18, 12, 0), open: 1, high: 2, low: 1, close: 1.5, volume: 100 },
    { time: utc(2026, 6, 18, 14, 0), open: 1.5, high: 2, low: 1, close: 1.8, volume: 200 },
  ];

  it('tags each intraday bar with its session', () => {
    const res = parseTvBars(envelope(bars), 'AAPL', '5', 'fixture');
    expect(res.ok).toBe(true);
    expect(res.bars.map((b) => b.session)).toEqual(['pre', 'rth']);
  });

  it('leaves daily bars untagged (session is meaningless intraday-only)', () => {
    const res = parseTvBars(envelope(bars), 'AAPL', 'D', 'fixture');
    expect(res.ok).toBe(true);
    expect(res.bars.every((b) => b.session === undefined)).toBe(true);
  });
});
