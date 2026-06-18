import type { ThesisDetail, WatchlistCandidate } from '@shared/types';

/**
 * Build the argv forwarded to `watchlist_orders.py` (after the script path) for
 * the UI's IB-bracket buttons. The geometry comes from the thesis the user is
 * acting on (entry.target_price / exit.stop_loss / exit.take_profit /
 * position.shares), with optional per-request overrides. Every value is
 * validated and passed as an argv array — never interpolated into a shell — so
 * a request can only ever invoke `open-now` / `cancel` with well-formed args.
 *
 * Placement is always launched with `--live`; the real safety gate is the
 * second lock (`IB_ALLOW_ORDER_PLACEMENT`) the launcher loads from `.env`. With
 * the env flag unset, `open-now` returns a preview without posting anything.
 */

const THESIS_ID_RE = /^th_[a-z0-9_]+$/i;
const TICKER_RE = /^[A-Za-z0-9.\-]{1,10}$/;

export type BuiltBracketArgs = { args: string[]; label: string } | { error: string };

function str(v: unknown): string {
  return typeof v === 'string' ? v.trim() : '';
}
function posNum(v: unknown): number | null {
  if (v === undefined || v === null || (typeof v === 'string' && v.trim() === '')) return null;
  const n = typeof v === 'number' ? v : Number(str(v));
  return Number.isFinite(n) && n > 0 ? n : null;
}
function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
function present(v: unknown): boolean {
  return !(v === undefined || v === null || (typeof v === 'string' && v.trim() === ''));
}
/**
 * Resolve a price/size: an explicit (present) override must itself be a positive
 * number — a supplied-but-invalid value is an error, never a silent fall-back to
 * the thesis. Only an absent override falls back to the thesis level.
 */
function resolveNum(
  override: unknown,
  fallback: number | null,
  name: string,
): { value: number } | { error: string } {
  if (present(override)) {
    const n = posNum(override);
    if (n == null) return { error: `${name} must be > 0` };
    return { value: n };
  }
  if (fallback == null) return { error: `${name} missing — set it on the thesis or supply it` };
  return { value: fallback };
}
/** Optional variant: a present override must be valid; absence yields the fallback (may be null). */
function optNum(
  override: unknown,
  fallback: number | null,
  name: string,
): { value: number | null } | { error: string } {
  if (present(override)) {
    const n = posNum(override);
    if (n == null) return { error: `${name} must be > 0` };
    return { value: n };
  }
  return { value: fallback };
}

/**
 * Build `open-now ...` argv for placing a native bracket for one ENTRY_READY
 * thesis. The caller (route) confirms the thesis exists and is ENTRY_READY, and
 * passes the matching watchlist candidate (the planner-sized entry — the same
 * source the Telegram flow uses).
 *
 * Each value resolves: explicit override → thesis level → watchlist candidate →
 * error. Shares in particular almost always come from the candidate, since an
 * ENTRY_READY thesis usually has no `position.shares` until it's sized.
 */
export function buildPlaceIbBracketArgs(
  body: Record<string, unknown>,
  detail: ThesisDetail,
  candidate?: WatchlistCandidate | null,
): BuiltBracketArgs {
  const id = detail.id;
  if (!THESIS_ID_RE.test(id)) return { error: 'invalid thesisId' };

  const ticker = detail.ticker.toUpperCase();
  if (!TICKER_RE.test(ticker)) return { error: 'thesis has no usable ticker' };

  const cand = candidate ?? null;
  const raw = rec(detail.raw);
  const side = (str(raw.side) || cand?.side || 'long').toLowerCase();
  if (side !== 'long' && side !== 'short') return { error: `invalid side: ${side}` };

  const entry = rec(detail.entry);
  const exit = rec(detail.exit);
  const position = rec(raw.position);

  // Shares are OPTIONAL: a present override must be valid; otherwise fall back to
  // the thesis position, then the watchlist candidate. If still unknown (e.g. a
  // signal-derived thesis with no sizing), leave `--shares` off entirely and let
  // open-now auto-size from the trading profile. Levels below stay required.
  let shares: number | null;
  if (present(body.quantity)) {
    shares = posNum(body.quantity);
    if (shares == null) return { error: 'quantity (shares) must be > 0' };
  } else {
    shares = posNum(position.shares) ?? posNum(cand?.shares);
  }
  const rPivot = resolveNum(
    body.entryPrice,
    posNum(entry.target_price) ?? posNum(cand?.pivot),
    'entry price (pivot)',
  );
  if ('error' in rPivot) return rPivot;
  const rStop = resolveNum(body.stopPrice, posNum(exit.stop_loss) ?? posNum(cand?.stop), 'stop');
  if ('error' in rStop) return rStop;
  const rTarget = resolveNum(
    body.targetPrice,
    posNum(exit.take_profit) ?? posNum(cand?.target),
    'target',
  );
  if ('error' in rTarget) return rTarget;
  // Optional scale-out targets: override → thesis exit.take_profit_2/3 → candidate t2/t3.
  const r2 = optNum(body.target2, posNum(exit.take_profit_2) ?? posNum(cand?.t2), 'T2');
  if ('error' in r2) return r2;
  const r3 = optNum(body.target3, posNum(exit.take_profit_3) ?? posNum(cand?.t3), 'T3');
  if ('error' in r3) return r3;
  const { value: pivot } = rPivot;
  const { value: stop } = rStop;
  const { value: target } = rTarget;
  const { value: t2 } = r2;
  const { value: t3 } = r3;

  const args = [
    'open-now',
    '--thesis-id', id,
    '--ticker', ticker,
    '--side', side,
    '--pivot', String(pivot),
    '--stop', String(stop),
    '--target', String(target),
    '--live',
  ];
  if (shares != null) args.push('--shares', String(shares));
  // Scale-out targets: only a 50/25/25 split (both present) reaches open-now as
  // T2+T3; a lone T2 is passed through but open-now falls back to a single target.
  if (t2 != null) args.push('--target2', String(t2));
  if (t3 != null) args.push('--target3', String(t3));
  const risk = posNum(position.risk_dollars) ?? posNum(cand?.risk_dollars);
  if (risk != null) args.push('--risk-dollars', String(risk));

  const scale = t2 != null && t3 != null ? ' [3-target 50/25/25]' : '';
  return {
    args,
    label: `place IB bracket ${ticker} x${shares ?? 'auto'} (${side})${scale}`,
  };
}

/** Build `cancel --thesis-id <id>` argv (the UI delete button). */
export function buildCancelIbBracketArgs(body: Record<string, unknown>): BuiltBracketArgs {
  const id = str(body.thesisId);
  if (!THESIS_ID_RE.test(id)) return { error: 'invalid thesisId' };
  return { args: ['cancel', '--thesis-id', id], label: `cancel IB bracket ${id}` };
}
