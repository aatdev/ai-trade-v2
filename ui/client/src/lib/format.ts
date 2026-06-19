import type { JobLane } from '@shared/types';

/** RU display label for each job resource lane (used in chips + busy messages). */
export const LANE_LABEL_RU: Record<JobLane, string> = {
  scheduler: 'Планировщик',
  tradingview: 'TradingView',
  ib: 'IB',
  screener: 'Скринер',
};

/** Canonical "this lane is busy" message shared by every job-launching hook. */
export function busyMessage(lane: JobLane | undefined, activeJobId: string | undefined): string {
  return `ресурс «${lane ? LANE_LABEL_RU[lane] : '?'}» занят другой задачей (${activeJobId ?? '?'})`;
}

export function fmtMoney(v: number | null | undefined, dp = 0): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `$${v.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;
}

export function fmtNum(v: number | null | undefined, dp = 2): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toLocaleString('en-US', { maximumFractionDigits: dp });
}

export function fmtPct(v: number | null | undefined, dp = 1): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${v.toFixed(dp)}%`;
}

export function fmtSignedPct(v: number | null | undefined, dp = 2): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${v > 0 ? '+' : ''}${v.toFixed(dp)}%`;
}

export function fmtScore(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(1);
}

export function fmtClock(ms: number | null | undefined): string {
  if (ms == null) return '—';
  return new Date(ms).toLocaleTimeString('en-GB');
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('en-GB');
}
