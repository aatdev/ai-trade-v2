import { describe, expect, it } from 'vitest';
import type { ThesisDetail } from '@shared/types';
import { buildPlaceIbBracketArgs, buildCancelIbBracketArgs } from './ibBracketOps';

function detail(over: Partial<ThesisDetail> = {}): ThesisDetail {
  return {
    id: 'th_nvda_pvt_20260612_aaaa',
    ticker: 'NVDA',
    status: 'ENTRY_READY',
    thesis_type: 'pivot_breakout',
    setup_type: null,
    thesis_statement: 'test',
    entry: { target_price: 100.0 },
    exit: { stop_loss: 95.0, take_profit: 110.0 },
    monitoring: null,
    origin: null,
    outcome: null,
    raw: { side: 'long', position: { shares: 10, risk_dollars: 50 } },
    ...over,
  } as ThesisDetail;
}

function ok(r: ReturnType<typeof buildPlaceIbBracketArgs>): string[] {
  if ('error' in r) throw new Error(`expected args, got error: ${r.error}`);
  return r.args;
}

describe('buildPlaceIbBracketArgs', () => {
  it('builds open-now argv from thesis levels + form quantity, always --live', () => {
    const args = ok(buildPlaceIbBracketArgs({ quantity: 25 }, detail()));
    expect(args).toEqual([
      'open-now',
      '--thesis-id', 'th_nvda_pvt_20260612_aaaa',
      '--ticker', 'NVDA',
      '--side', 'long',
      '--pivot', '100',
      '--stop', '95',
      '--target', '110',
      '--live',
      '--shares', '25',
      '--risk-dollars', '50',
    ]);
  });

  it('falls back to position.shares when no quantity is given', () => {
    const args = ok(buildPlaceIbBracketArgs({}, detail()));
    expect(args).toContain('--shares');
    expect(args[args.indexOf('--shares') + 1]).toBe('10');
  });

  it('honours explicit level overrides over the thesis', () => {
    const args = ok(
      buildPlaceIbBracketArgs(
        { quantity: 5, entryPrice: 101, stopPrice: 96, targetPrice: 120 },
        detail(),
      ),
    );
    expect(args[args.indexOf('--pivot') + 1]).toBe('101');
    expect(args[args.indexOf('--stop') + 1]).toBe('96');
    expect(args[args.indexOf('--target') + 1]).toBe('120');
  });

  it('passes T2/T3 from the thesis exit and tags the label as a 50/25/25 scale-out', () => {
    const d = detail({
      exit: { stop_loss: 95, take_profit: 110, take_profit_2: 120, take_profit_3: 130 },
    });
    const r = buildPlaceIbBracketArgs({ quantity: 100 }, d);
    if ('error' in r) throw new Error(r.error);
    expect(r.args[r.args.indexOf('--target2') + 1]).toBe('120');
    expect(r.args[r.args.indexOf('--target3') + 1]).toBe('130');
    expect(r.label).toContain('50/25/25');
  });

  it('falls back to the watchlist candidate for T2/T3 when the thesis lacks them', () => {
    const d = detail({ exit: { stop_loss: 95, take_profit: 110 } }); // no T2/T3 on thesis
    const args = ok(
      buildPlaceIbBracketArgs({ quantity: 100 }, d, {
        ticker: 'NVDA',
        side: 'long',
        t2: 121,
        t3: 131,
      } as WatchlistCandidate),
    );
    expect(args[args.indexOf('--target2') + 1]).toBe('121');
    expect(args[args.indexOf('--target3') + 1]).toBe('131');
  });

  it('omits T2/T3 entirely when neither thesis nor candidate has them', () => {
    const args = ok(buildPlaceIbBracketArgs({ quantity: 100 }, detail({ exit: { stop_loss: 95, take_profit: 110 } })));
    expect(args).not.toContain('--target2');
    expect(args).not.toContain('--target3');
  });

  it('carries side=short through', () => {
    const args = ok(
      buildPlaceIbBracketArgs(
        { quantity: 5 },
        detail({
          raw: { side: 'short', position: { shares: 5 } },
          entry: { target_price: 145 },
          exit: { stop_loss: 153, take_profit: 130 },
        }),
      ),
    );
    expect(args[args.indexOf('--side') + 1]).toBe('short');
  });

  it('omits --shares when none is resolvable (open-now auto-sizes from the profile)', () => {
    const args = ok(buildPlaceIbBracketArgs({}, detail({ raw: { side: 'long' } })));
    expect(args).not.toContain('--shares');
    // levels are still required + present, so the command is still well-formed
    expect(args).toContain('--pivot');
    expect(args).toContain('--target');
  });

  it('takes shares (and missing levels) from the watchlist candidate when the thesis lacks them', () => {
    // ENTRY_READY thesis with no position sizing and no take_profit — the
    // common case; the planner-sized candidate fills shares + target.
    const d = detail({ raw: { side: 'long' }, entry: { target_price: 100 }, exit: { stop_loss: 95 } });
    const args = ok(
      buildPlaceIbBracketArgs({}, d, {
        ticker: 'NVDA',
        side: 'long',
        pivot: 100,
        stop: 95,
        target: 110,
        shares: 12,
        risk_dollars: 60,
      } as WatchlistCandidate),
    );
    expect(args[args.indexOf('--shares') + 1]).toBe('12');
    expect(args[args.indexOf('--target') + 1]).toBe('110');
    expect(args[args.indexOf('--risk-dollars') + 1]).toBe('60');
  });

  it('prefers the thesis level over the candidate, and an override over both', () => {
    const d = detail({ raw: { side: 'long', position: { shares: 10 } } }); // thesis target_price=100
    const cand = { ticker: 'NVDA', side: 'long', pivot: 200, stop: 190, target: 230, shares: 99 } as WatchlistCandidate;
    const args = ok(buildPlaceIbBracketArgs({ entryPrice: 105 }, d, cand));
    expect(args[args.indexOf('--pivot') + 1]).toBe('105'); // override wins
    expect(args[args.indexOf('--shares') + 1]).toBe('10'); // thesis position over candidate
  });

  it('errors when the thesis lacks a stop level and none is supplied', () => {
    const r = buildPlaceIbBracketArgs({ quantity: 5 }, detail({ exit: { take_profit: 110 } }));
    expect(r).toHaveProperty('error');
  });

  it('errors on a non-positive quantity', () => {
    expect(buildPlaceIbBracketArgs({ quantity: 0 }, detail())).toHaveProperty('error');
    expect(buildPlaceIbBracketArgs({ quantity: -3 }, detail())).toHaveProperty('error');
  });

  it('omits --risk-dollars when the thesis has no sized risk', () => {
    const args = ok(buildPlaceIbBracketArgs({ quantity: 5 }, detail({ raw: { side: 'long', position: { shares: 5 } } })));
    expect(args).not.toContain('--risk-dollars');
  });
});

describe('buildCancelIbBracketArgs', () => {
  it('builds cancel argv for a valid id', () => {
    const r = buildCancelIbBracketArgs({ thesisId: 'th_nvda_pvt_20260612_aaaa' });
    expect(ok(r)).toEqual(['cancel', '--thesis-id', 'th_nvda_pvt_20260612_aaaa']);
  });

  it('rejects a malformed id', () => {
    expect(buildCancelIbBracketArgs({ thesisId: 'not!an!id' })).toHaveProperty('error');
    expect(buildCancelIbBracketArgs({})).toHaveProperty('error');
  });
});
