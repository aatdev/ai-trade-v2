import { describe, expect, it } from 'vitest';
import { buildMemoryArgs } from './memoryOps';

const SD = '/data/journal/theses';

function ok(r: ReturnType<typeof buildMemoryArgs>): string[] {
  if ('error' in r) throw new Error(`expected args, got error: ${r.error}`);
  return r.args;
}

describe('buildMemoryArgs — ingest', () => {
  it('ticker-analysis with only a ticker needs no input path', () => {
    const args = ok(
      buildMemoryArgs({ op: 'ingest', source: 'ticker-analysis', ticker: 'aapl' }, SD),
    );
    expect(args).toEqual([
      'ingest',
      '--state-dir',
      SD,
      '--source',
      'ticker-analysis',
      '--ticker',
      'AAPL',
    ]);
  });

  it('ticker-analysis accepts a relative .md journal input', () => {
    const args = ok(
      buildMemoryArgs(
        { op: 'ingest', source: 'ticker-analysis', input: 'analysis/signals.md', ticker: 'ALB' },
        SD,
      ),
    );
    expect(args).toContain('--input');
    expect(args).toContain('analysis/signals.md');
    expect(args).toContain('--ticker');
    expect(args).toContain('ALB');
  });

  it('rejects a bad ticker for the signal source', () => {
    const r = buildMemoryArgs(
      { op: 'ingest', source: 'ticker-analysis', ticker: 'not a ticker!' },
      SD,
    );
    expect(r).toHaveProperty('error');
  });

  it('rejects an absolute / traversal input path', () => {
    const r = buildMemoryArgs(
      { op: 'ingest', source: 'ticker-analysis', input: '../etc/signals.md' },
      SD,
    );
    expect(r).toHaveProperty('error');
  });

  it('non-signal source still requires a .json input', () => {
    expect(buildMemoryArgs({ op: 'ingest', source: 'vcp-screener' }, SD)).toHaveProperty('error');
    expect(
      buildMemoryArgs({ op: 'ingest', source: 'vcp-screener', input: 'reports/vcp.md' }, SD),
    ).toHaveProperty('error');
    const args = ok(
      buildMemoryArgs({ op: 'ingest', source: 'vcp-screener', input: 'reports/vcp.json' }, SD),
    );
    expect(args).toEqual([
      'ingest',
      '--state-dir',
      SD,
      '--source',
      'vcp-screener',
      '--input',
      'reports/vcp.json',
    ]);
  });

  it('rejects an unknown op', () => {
    expect(buildMemoryArgs({ op: 'nope' }, SD)).toHaveProperty('error');
  });
});
