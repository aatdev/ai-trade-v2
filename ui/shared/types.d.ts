/**
 * Shared API response types for the trading-state dashboard.
 *
 * These are TYPE-ONLY declarations (no runtime code), imported by both the
 * Express server and the React client via the `@shared/*` path alias. Because
 * they are consumed through `import type { ... }`, the imports are erased at
 * runtime, so no bundling/compilation of this file is required.
 */

export type ExposureDecision = 'allow' | 'restrict' | 'cash-priority' | string;
export type Side = 'long' | 'short' | string;
export type ThesisStatus = 'IDEA' | 'OPEN' | 'CLOSED' | string;

/** GET /api/dates */
export interface DatesResponse {
  dates: string[]; // descending (newest first)
  latest: string | null;
}

/** Wraps every state response so the client knows which date/file it got. */
export interface Sourced<T> {
  date: string | null; // the date actually resolved
  source: string | null; // basename of the file read (null if missing)
  data: T | null;
}

/* ---------------- Exposure gate ---------------- */

export interface ExposureGate {
  workflow?: string;
  date?: string;
  decision: ExposureDecision;
  net_exposure_ceiling_pct: number | null;
  rationale: string | null;
  key_signals: string[];
}

export interface ExposurePosture {
  generated_at?: string;
  exposure_ceiling_pct: number | null;
  bias: string | null;
  participation: string | null;
  recommendation: string | null;
  confidence: string | null;
  composite_score: number | null;
  component_scores: Record<string, number>;
  inputs_provided: string[];
  inputs_missing: string[];
  rationale: string | null;
}

export interface ExposureResponse {
  gate: Sourced<ExposureGate>;
  posture: Sourced<ExposurePosture>;
}

/* ---------------- Watchlist ---------------- */

export interface WatchlistCandidate {
  ticker: string;
  side: Side;
  setup: string | null;
  pivot: number | null;
  worst_entry: number | null;
  stop: number | null;
  target: number | null;
  shares: number | null;
  risk_dollars: number | null;
  score: number | null;
  plan_type: string | null;
  validation_note: string | null;
  validated: boolean | null;
}

export interface Watchlist {
  workflow?: string;
  date?: string;
  exposure_decision: ExposureDecision | null;
  candidates: WatchlistCandidate[];
  rejected_by_validation: WatchlistCandidate[];
  notes: string | null;
  source_plan: string | null;
}

/* ---------------- Portfolio heat / open positions ---------------- */

export interface Position {
  ticker: string;
  side: Side;
  entry_price: number | null;
  stop_loss: number | null;
  current_price: number | null;
  shares: number | null;
  position_size_dollars: number | null;
  risk_dollars: number | null;
  sector: string | null;
  entry_date: string | null;
  days_held: number | null;
  pnl_pct: number | null;
  pnl_dollars: number | null;
  mae_pct: number | null;
  mfe_pct: number | null;
}

export interface PortfolioHeat {
  generated_at: string | null;
  account_size: number | null;
  open_risk_pct: number | null;
  open_risk_dollars: number | null;
  positions_count: number | null;
  max_positions: number | null;
  remaining_position_slots: number | null;
  max_portfolio_heat_pct: number | null;
  remaining_heat_pct: number | null;
  remaining_heat_dollars: number | null;
  sector_exposure: Record<string, number>;
  positions: Position[];
  warnings: string[];
}

/* ---------------- Market regime ---------------- */

export interface RegimeComponent {
  key: string;
  label: string;
  score: number | null;
  weight: number | null;
}

export interface RegimeComposite {
  composite_score: number | null;
  zone: string | null;
  zone_color: string | null;
  guidance: string | null;
  components: RegimeComponent[];
  generated_at: string | null;
}

export interface MarketResponse {
  breadth: Sourced<RegimeComposite>;
  uptrend: Sourced<RegimeComposite>;
  top: Sourced<RegimeComposite>;
  macro: Sourced<RegimeComposite>;
  posture: Sourced<ExposurePosture>;
}

/* ---------------- Screeners ---------------- */

export interface ScreenerCandidate {
  symbol: string;
  name: string | null;
  sector: string | null;
  composite_score: number | null;
  grade: string | null;
  strongest_signal: string | null;
  components: Record<string, number>;
  /** Normalized entry/stop/target regardless of screener-specific key names. */
  entry: number | null;
  stop: number | null;
  target: number | null;
  metrics: Record<string, number | boolean | null>;
}

export interface ScreenerResult {
  kind: 'vcp' | 'swing-short';
  meta: Record<string, unknown>;
  candidates: ScreenerCandidate[];
}

export interface ScreenersResponse {
  vcp: Sourced<ScreenerResult>;
  swingShort: Sourced<ScreenerResult>;
}

/* ---------------- Theses ---------------- */

export interface ThesisIndexEntry {
  id: string;
  ticker: string;
  status: ThesisStatus;
  thesis_type: string | null;
  created_at: string | null;
  updated_at: string | null;
  next_review_date: string | null;
  review_status: string | null;
  review_due: boolean;
}

export interface ThesesResponse {
  theses: ThesisIndexEntry[];
}

export interface ThesisDetail {
  id: string;
  ticker: string;
  status: ThesisStatus;
  thesis_type: string | null;
  setup_type: string | null;
  thesis_statement: string | null;
  entry: Record<string, unknown> | null;
  exit: Record<string, unknown> | null;
  monitoring: Record<string, unknown> | null;
  origin: Record<string, unknown> | null;
  outcome: Record<string, unknown> | null;
  raw: Record<string, unknown>;
}

/* ---------------- Signals journal ---------------- */

export interface SignalBlock {
  id: string;
  date: string;
  ticker: string;
  heading: string; // heading line without the leading "## "
  status: string | null;
  markdown: string; // full block markdown (includes its own "## …" heading)
}

export interface SignalsResponse {
  content: string;
  present: boolean;
  signals: SignalBlock[];
}

export interface DeleteSignalResponse {
  removed: number;
  kept: number;
  ticker: string;
  date: string;
  found: boolean;
}

/* ---------------- Autopilot / schedule ---------------- */

export interface AutopilotState {
  date: string | null;
  last_gate_decision: ExposureDecision | null;
  slots: Record<string, unknown>;
  weekly: Record<string, unknown>;
  monthly: Record<string, unknown>;
  intraday: Record<string, unknown>;
}

export interface AutopilotResponse {
  state: AutopilotState | null;
  weeklyReview: Sourced<Record<string, unknown>>;
  monthlyReview: Sourced<Record<string, unknown>>;
  logTail: string[];
}

/* ---------------- Profile ---------------- */

export interface TradingProfile {
  account_size: number;
  risk_pct: number;
  max_position_pct: number;
  max_sector_pct: number;
  max_portfolio_heat_pct: number;
  max_positions: number;
  target_r_multiple: number;
  earnings_gate_days: number;
  time_stop_trading_days: number;
  atr_multiplier: number;
  [key: string]: number;
}

/* ---------------- Ticker analysis ---------------- */

export interface TickerDatesResponse {
  symbol: string;
  dates: string[]; // descending
}

export interface TickerDoc {
  name: string; // report | technical | fundamental | news
  content: string;
}

export interface TickerAnalysisResponse {
  symbol: string;
  date: string;
  docs: TickerDoc[];
  charts: string[]; // timeframes available, e.g. ["daily","weekly"]
}

/** GET /api/analysis/tickers — which tickers already have saved analysis. */
export interface AnalysisIndexEntry {
  latest: string | null;
  count: number;
}

export interface AnalysisIndexResponse {
  tickers: Record<string, AnalysisIndexEntry>;
}

/* ---------------- Actions / jobs ---------------- */

export type SchedulerSlot = 'premarket' | 'evening-prep' | 'intraday' | 'weekly' | 'monthly';
export type JobStatus = 'running' | 'done' | 'error' | 'busy';

export interface JobLogLine {
  t: number;
  stream: 'stdout' | 'stderr' | 'system';
  line: string;
}

export interface JobSummary {
  id: string;
  label: string;
  status: JobStatus;
  startedAt: number;
  endedAt: number | null;
  exitCode: number | null;
  meta?: Record<string, unknown>;
}

export interface JobDetail extends JobSummary {
  cmd: string;
  args: string[];
  lines: JobLogLine[];
}

export interface StartJobResponse {
  ok: boolean;
  job?: JobSummary;
  busy?: boolean;
  activeJobId?: string;
  error?: string;
}
