import path from 'node:path';

/**
 * Validates a trader-memory operation request and builds the argv forwarded to
 * `trader_memory_cli.py` (after the script path). Every value is validated and
 * passed as an argv array — never interpolated into a shell — so a request can
 * only ever invoke a whitelisted subcommand with well-formed arguments.
 *
 * Covers the full CLI surface: store (list/get/transition/open-position/
 * attach-position/close/trim/terminate/mark-reviewed/delete/rebuild-index/
 * doctor), ingest, review (review-due/postmortem/summary), heat.
 */

const THESIS_ID_RE = /^th_[a-z0-9_]+$/i;
const TICKER_RE = /^[A-Za-z0-9.\-]{1,10}$/;
const STATUSES = new Set([
  'IDEA',
  'ENTRY_READY',
  'ACTIVE',
  'PARTIALLY_CLOSED',
  'CLOSED',
  'INVALIDATED',
]);
const THESIS_TYPES = new Set([
  'dividend_income',
  'growth_momentum',
  'mean_reversion',
  'earnings_drift',
  'pivot_breakout',
]);
const EXIT_REASONS = new Set(['stop_hit', 'target_hit', 'time_stop', 'invalidated', 'manual']);
const REVIEW_OUTCOMES = new Set(['OK', 'WARN', 'REVIEW']);
const TERMINAL = new Set(['CLOSED', 'INVALIDATED']);
const SKILL_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}/;

export type BuiltMemoryArgs = { args: string[]; label: string } | { error: string };

function str(v: unknown): string {
  return typeof v === 'string' ? v.trim() : '';
}
function posNum(v: unknown): number | null {
  const n = typeof v === 'number' ? v : Number(str(v));
  return Number.isFinite(n) && n > 0 ? n : null;
}
function posInt(v: unknown): number | null {
  const n = posNum(v);
  return n != null && Number.isInteger(n) ? n : null;
}
/** Push an optional flag if a non-empty value is present; null on success, error string on bad value. */
function pushOptDate(args: string[], flag: string, v: unknown): string | null {
  const s = str(v);
  if (!s) return null;
  if (!DATE_RE.test(s)) return `${flag.replace('--', '')} must be YYYY-MM-DD`;
  args.push(flag, s);
  return null;
}
/** True when a body value should be treated as "not provided". */
function absent(v: unknown): boolean {
  return v === undefined || v === null || (typeof v === 'string' && v.trim() === '');
}
function pushOptPosNum(args: string[], flag: string, v: unknown): string | null {
  if (absent(v)) return null;
  const n = posNum(v);
  if (n == null) return `${flag.replace('--', '')} must be > 0`;
  args.push(flag, String(n));
  return null;
}
function pushOptPosInt(args: string[], flag: string, v: unknown): string | null {
  if (absent(v)) return null;
  const n = posInt(v);
  if (n == null) return `${flag.replace('--', '')} must be a positive integer`;
  args.push(flag, String(n));
  return null;
}
function pushOptText(args: string[], flag: string, v: unknown): void {
  const s = str(v);
  if (s) args.push(flag, s);
}
/** Validate an optional relative path (no traversal); '' means "absent". */
function relPathErr(s: string, mustJson: boolean): string | null {
  if (path.isAbsolute(s) || s.includes('..')) return 'path must be relative without ".."';
  if (mustJson && !s.toLowerCase().endsWith('.json')) return 'path must end with .json';
  return null;
}

export function buildMemoryArgs(body: Record<string, unknown>, stateDir: string): BuiltMemoryArgs {
  const op = str(body.op);
  const id = str(body.thesisId);
  const sd = ['--state-dir', stateDir];
  const needId = (): string | null => (THESIS_ID_RE.test(id) ? null : 'invalid thesisId');
  const store = (sub: string, ...rest: string[]) => ['store', ...sd, sub, ...rest];
  const review = (sub: string, ...rest: string[]) => ['review', ...sd, sub, ...rest];

  switch (op) {
    /* ---- read ---- */
    case 'summary':
      return { args: review('summary'), label: 'memory: summary' };
    case 'doctor':
      return { args: store('doctor'), label: 'memory: doctor' };
    case 'rebuild-index':
      return { args: store('rebuild-index'), label: 'memory: rebuild-index' };
    case 'review-due': {
      const args = review('review-due');
      const e = pushOptDate(args, '--as-of', body.asOf);
      if (e) return { error: e };
      return { args, label: 'memory: review-due' };
    }
    case 'list': {
      const args = store('list');
      const ticker = str(body.ticker);
      if (ticker && !TICKER_RE.test(ticker.toUpperCase())) return { error: 'invalid ticker' };
      if (ticker) args.push('--ticker', ticker.toUpperCase());
      const status = str(body.status);
      if (status && !STATUSES.has(status.toUpperCase())) return { error: 'invalid status' };
      if (status) args.push('--status', status.toUpperCase());
      const type = str(body.type);
      if (type && !THESIS_TYPES.has(type)) return { error: 'invalid type' };
      if (type) args.push('--type', type);
      let e = pushOptDate(args, '--date-from', body.dateFrom);
      if (e) return { error: e };
      e = pushOptDate(args, '--date-to', body.dateTo);
      if (e) return { error: e };
      return { args, label: 'memory: list' };
    }
    case 'get': {
      const e = needId();
      if (e) return { error: e };
      return { args: store('get', id), label: `memory: get ${id}` };
    }
    case 'postmortem': {
      const e = needId();
      if (e) return { error: e };
      const args = review('postmortem', id);
      const jd = str(body.journalDir);
      if (jd) {
        const pe = relPathErr(jd, false);
        if (pe) return { error: `journalDir: ${pe}` };
        args.push('--journal-dir', jd);
      }
      return { args, label: `memory: postmortem ${id}` };
    }

    /* ---- lifecycle (mutating) ---- */
    case 'delete': {
      const e = needId();
      if (e) return { error: e };
      return { args: store('delete', id), label: `memory: delete ${id}` };
    }
    case 'mark-reviewed': {
      const e = needId();
      if (e) return { error: e };
      const outcome = str(body.outcome) || 'OK';
      if (!REVIEW_OUTCOMES.has(outcome)) return { error: 'invalid outcome' };
      const args = store('mark-reviewed', id, '--outcome', outcome);
      const de = pushOptDate(args, '--review-date', body.reviewDate);
      if (de) return { error: de };
      pushOptText(args, '--notes', body.notes);
      return { args, label: `memory: mark-reviewed ${id}` };
    }
    case 'transition': {
      const e = needId();
      if (e) return { error: e };
      const ns = str(body.newStatus).toUpperCase();
      if (!STATUSES.has(ns)) return { error: 'invalid newStatus' };
      const reason = str(body.reason);
      if (!reason) return { error: 'reason required' };
      const args = store('transition', id, ns, '--reason', reason);
      const de = pushOptDate(args, '--event-date', body.eventDate);
      if (de) return { error: de };
      return { args, label: `memory: ${id} → ${ns}` };
    }
    case 'open-position': {
      const e = needId();
      if (e) return { error: e };
      const price = posNum(body.price);
      const date = str(body.date);
      if (price == null) return { error: 'price must be > 0' };
      if (!DATE_RE.test(date)) return { error: 'invalid date (YYYY-MM-DD)' };
      const args = store('open-position', id, '--actual-price', String(price), '--actual-date', date);
      const se = pushOptPosNum(args, '--shares', body.shares);
      if (se) return { error: se };
      pushOptText(args, '--reason', body.reason);
      const de = pushOptDate(args, '--event-date', body.eventDate);
      if (de) return { error: de };
      return { args, label: `memory: open ${id}` };
    }
    case 'attach-position': {
      const e = needId();
      if (e) return { error: e };
      const report = str(body.report);
      if (!report) return { error: 'report path required' };
      const pe = relPathErr(report, true);
      if (pe) return { error: `report: ${pe}` };
      const args = store('attach-position', id, '--report', report);
      let ne = pushOptPosNum(args, '--expected-entry', body.expectedEntry);
      if (ne) return { error: ne };
      ne = pushOptPosNum(args, '--expected-stop', body.expectedStop);
      if (ne) return { error: ne };
      return { args, label: `memory: attach-position ${id}` };
    }
    case 'close': {
      const e = needId();
      if (e) return { error: e };
      const reason = str(body.exitReason);
      if (!EXIT_REASONS.has(reason)) return { error: 'invalid exitReason' };
      const price = posNum(body.price);
      const date = str(body.date);
      if (price == null) return { error: 'price must be > 0' };
      if (!DATE_RE.test(date)) return { error: 'invalid date (YYYY-MM-DD)' };
      const args = store(
        'close', id,
        '--exit-reason', reason,
        '--actual-price', String(price),
        '--actual-date', date,
      );
      const de = pushOptDate(args, '--event-date', body.eventDate);
      if (de) return { error: de };
      return { args, label: `memory: close ${id}` };
    }
    case 'trim': {
      const e = needId();
      if (e) return { error: e };
      const sharesSold = posNum(body.sharesSold);
      const price = posNum(body.price);
      const date = str(body.date);
      if (sharesSold == null) return { error: 'sharesSold must be > 0' };
      if (price == null) return { error: 'price must be > 0' };
      if (!DATE_RE.test(date)) return { error: 'invalid date (YYYY-MM-DD)' };
      const args = store(
        'trim', id,
        '--shares-sold', String(sharesSold),
        '--price', String(price),
        '--date', date,
      );
      pushOptText(args, '--reason', body.reason);
      const exitReason = str(body.exitReason);
      if (exitReason) {
        if (!EXIT_REASONS.has(exitReason)) return { error: 'invalid exitReason' };
        args.push('--exit-reason', exitReason);
      }
      const de = pushOptDate(args, '--event-date', body.eventDate);
      if (de) return { error: de };
      return { args, label: `memory: trim ${id}` };
    }
    case 'terminate': {
      const e = needId();
      if (e) return { error: e };
      const ts = str(body.terminalStatus).toUpperCase();
      if (!TERMINAL.has(ts)) return { error: 'invalid terminalStatus' };
      const reason = str(body.exitReason) || str(body.reason);
      if (!reason) return { error: 'exitReason required' };
      const args = store('terminate', id, '--terminal-status', ts, '--exit-reason', reason);
      let ne = pushOptPosNum(args, '--actual-price', body.price);
      if (ne) return { error: ne };
      const de = pushOptDate(args, '--actual-date', body.date);
      if (de) return { error: de };
      ne = pushOptDate(args, '--event-date', body.eventDate);
      if (ne) return { error: ne };
      return { args, label: `memory: terminate ${id} (${ts})` };
    }

    /* ---- ingest ---- */
    case 'ingest': {
      const source = str(body.source).toLowerCase();
      if (!SKILL_RE.test(source)) return { error: 'invalid source skill name' };
      // The signal → thesis source reads a signals.md journal (default
      // $TRADING_DATE_DIR/analysis/signals.md), so --input is optional and may
      // be a .md file; an optional --ticker registers one symbol's latest signal.
      const isSignal = source === 'ticker-analysis';
      const args = ['ingest', ...sd, '--source', source];
      const input = str(body.input);
      if (input) {
        const pe = relPathErr(input, !isSignal); // .json required except for signals
        if (pe) return { error: `input: ${pe}` };
        args.push('--input', input);
      } else if (!isSignal) {
        return { error: 'input path required' };
      }
      if (isSignal) {
        const ticker = str(body.ticker);
        if (ticker) {
          if (!TICKER_RE.test(ticker.toUpperCase())) return { error: 'invalid ticker' };
          args.push('--ticker', ticker.toUpperCase());
        }
      }
      return { args, label: `memory: ingest ${source}` };
    }

    /* ---- heat ---- */
    case 'heat': {
      const args = ['heat', ...sd];
      let ne = pushOptPosNum(args, '--account-size', body.accountSize);
      if (ne) return { error: ne };
      ne = pushOptPosNum(args, '--max-portfolio-heat-pct', body.maxHeatPct);
      if (ne) return { error: ne };
      ne = pushOptPosInt(args, '--max-positions', body.maxPositions);
      if (ne) return { error: ne };
      if (body.jsonOnly === true) args.push('--json-only');
      return { args, label: 'memory: heat' };
    }

    default:
      return { error: `unknown op: ${op || '(none)'}` };
  }
}

/** Statuses whose theses may be hard-deleted in bulk from the UI. Position-backed
 * (ACTIVE / PARTIALLY_CLOSED) and CLOSED theses are never bulk-deletable. */
export const DELETABLE_STATES = new Set(['IDEA', 'ENTRY_READY', 'INVALIDATED']);

/**
 * Validate a bulk thesis-delete request and build the `store delete <ids...>`
 * argv. `statusById` is the authoritative current status of each thesis (read
 * from the index by the caller) — a request may only delete ids that exist AND
 * are in a {@link DELETABLE_STATES} state, so a crafted body can never remove a
 * position-backed or closed thesis.
 */
export function buildDeleteThesesArgs(
  ids: unknown,
  statusById: Record<string, string>,
  stateDir: string,
): BuiltMemoryArgs {
  if (!Array.isArray(ids) || ids.length === 0) return { error: 'ids must be a non-empty array' };
  const uniq: string[] = [];
  for (const raw of ids) {
    const id = str(raw);
    if (!THESIS_ID_RE.test(id)) return { error: `invalid thesisId: ${String(raw)}` };
    const status = String(statusById[id] ?? '').toUpperCase();
    if (!status) return { error: `unknown thesis: ${id}` };
    if (!DELETABLE_STATES.has(status)) {
      return { error: `cannot delete ${id}: status ${status} (only IDEA / ENTRY_READY / INVALIDATED)` };
    }
    if (!uniq.includes(id)) uniq.push(id);
  }
  return {
    args: ['store', '--state-dir', stateDir, 'delete', ...uniq],
    label: `memory: delete ${uniq.length} thesis/theses`,
  };
}
