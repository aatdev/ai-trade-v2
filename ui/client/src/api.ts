import { useQuery } from '@tanstack/react-query';
import type {
  AnalysisIndexResponse,
  ApplyReconcileResponse,
  AuthActionResponse,
  AuthStatusResponse,
  AutopilotResponse,
  BottomFlowRunRequest,
  DatesResponse,
  DeleteSignalResponse,
  DocsIndexResponse,
  DocSectionResponse,
  ReconcileResult,
  ExposureResponse,
  FundamentalsResponse,
  IbHealth,
  IbSnapshot,
  JobDetail,
  JobSummary,
  MarketResponse,
  MemoryResponse,
  OhlcvResponse,
  PortfolioHeat,
  SaveWatchlistRequest,
  ScreenerPlanRequest,
  ScreenerRunRequest,
  SaveProfileResponse,
  ScreenersResponse,
  ShortScreenerRunRequest,
  SignalsResponse,
  StagedBottomFlowResponse,
  StagedScreenerResponse,
  StagedShortScreenerResponse,
  SkillDocResponse,
  Sourced,
  StartJobResponse,
  ThesesResponse,
  ThesisDetail,
  TickerAnalysisResponse,
  TickerDatesResponse,
  TradingProfile,
  VersionsResponse,
  Watchlist,
} from '@shared/types';

/** A selectable file kind for GET /api/versions and the matching `*Source` params. */
export type VersionKind =
  | 'exposure'
  | 'watchlist'
  | 'portfolio'
  | 'vcp'
  | 'swing-short'
  | 'breadth'
  | 'uptrend'
  | 'top'
  | 'macro';

export type Refetch = number | false;

/** Event name fired on any 401 so the AuthGate can bounce back to the login screen. */
export const UNAUTHORIZED_EVENT = 'auth:unauthorized';

function onUnauthorized(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (res.status === 401) {
    onUnauthorized();
    throw new Error('unauthorized');
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
  return (await res.json()) as T;
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  if (res.status === 401) onUnauthorized();
  return (await res.json()) as T;
}

async function putJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  if (res.status === 401) onUnauthorized();
  return (await res.json()) as T;
}

const dq = (date: string | null) => (date ? `?date=${encodeURIComponent(date)}` : '');

/** Build a query string from defined, non-empty params (drops null/undefined/''). */
function qs(params: Record<string, string | null | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

/* ---------------- auth ---------------- */

export const useAuthStatus = () =>
  useQuery({
    queryKey: ['auth'],
    queryFn: () => getJSON<AuthStatusResponse>('/api/auth'),
    retry: false,
    staleTime: 30_000,
  });

export async function login(username: string, password: string): Promise<AuthActionResponse> {
  return postJSON<AuthActionResponse>('/api/login', { username, password });
}

export const logout = () => postJSON<AuthActionResponse>('/api/logout', {});

/* ---------------- read hooks ---------------- */

export const useDates = (refetchInterval: Refetch = false) =>
  useQuery({ queryKey: ['dates'], queryFn: () => getJSON<DatesResponse>('/api/dates'), refetchInterval });

/** Last ~10 selectable file versions for a kind (newest first); used by SourceSelect. */
export const useVersions = (kind: VersionKind, refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['versions', kind],
    queryFn: () => getJSON<VersionsResponse>(`/api/versions?kind=${encodeURIComponent(kind)}`),
    refetchInterval,
    staleTime: 30_000,
  });

export const useExposure = (
  date: string | null,
  source: string | null = null,
  refetchInterval: Refetch = false,
) =>
  useQuery({
    queryKey: ['exposure', date, source],
    queryFn: () => getJSON<ExposureResponse>(`/api/exposure${qs({ date, source })}`),
    refetchInterval,
  });

export const useWatchlist = (
  date: string | null,
  source: string | null = null,
  refetchInterval: Refetch = false,
) =>
  useQuery({
    queryKey: ['watchlist', date, source],
    queryFn: () => getJSON<Sourced<Watchlist>>(`/api/watchlist${qs({ date, source })}`),
    refetchInterval,
  });

export const usePortfolio = (
  date: string | null,
  source: string | null = null,
  refetchInterval: Refetch = false,
) =>
  useQuery({
    queryKey: ['portfolio', date, source],
    queryFn: () => getJSON<Sourced<PortfolioHeat>>(`/api/portfolio${qs({ date, source })}`),
    refetchInterval,
  });

export const useIbSnapshot = (refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['ib'],
    queryFn: () => getJSON<IbSnapshot>('/api/ib'),
    refetchInterval,
  });

/**
 * Lightweight IB Gateway liveness probe. Cheap enough to poll on an interval
 * (independent of the IB tab) so the "Счёт IB" tab can flag a downed Gateway.
 * Keeps polling in the background so the indicator stays live.
 */
export const useIbHealth = (refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['ibHealth'],
    queryFn: () => getJSON<IbHealth>('/api/ib/health'),
    refetchInterval,
    refetchIntervalInBackground: true,
    retry: false,
    staleTime: 10_000,
  });

export interface MarketSources {
  breadth?: string | null;
  uptrend?: string | null;
  top?: string | null;
  macro?: string | null;
}

export const useMarket = (
  date: string | null,
  sources: MarketSources = {},
  refetchInterval: Refetch = false,
) =>
  useQuery({
    queryKey: ['market', date, sources.breadth, sources.uptrend, sources.top, sources.macro],
    queryFn: () =>
      getJSON<MarketResponse>(
        `/api/market${qs({
          date,
          breadthSource: sources.breadth,
          uptrendSource: sources.uptrend,
          topSource: sources.top,
          macroSource: sources.macro,
        })}`,
      ),
    refetchInterval,
  });

export interface ScreenerSources {
  vcp?: string | null;
  swing?: string | null;
}

export const useScreeners = (
  date: string | null,
  sources: ScreenerSources = {},
  refetchInterval: Refetch = false,
) =>
  useQuery({
    queryKey: ['screeners', date, sources.vcp, sources.swing],
    queryFn: () =>
      getJSON<ScreenersResponse>(
        `/api/screeners${qs({ date, vcpSource: sources.vcp, swingSource: sources.swing })}`,
      ),
    refetchInterval,
  });

/* ---------------- interactive screener ("Скринер" tab) ---------------- */

export interface StagedSources {
  vcp?: string | null;
  plan?: string | null;
}

/** Latest (or pinned) staged VCP run + plan + gate/heat context, with the 5.3 checklist. */
export const useStagedScreener = (sources: StagedSources = {}, refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['stagedScreener', sources.vcp, sources.plan],
    queryFn: () =>
      getJSON<StagedScreenerResponse>(
        `/api/screener/staged${qs({ vcpSource: sources.vcp, planSource: sources.plan })}`,
      ),
    refetchInterval,
  });

export const runScreener = (body: ScreenerRunRequest) =>
  postJSON<StartJobResponse>('/api/screener/run', body);

export const runScreenerPlan = (body: ScreenerPlanRequest = {}) =>
  postJSON<StartJobResponse>('/api/screener/plan', body);

export const saveWatchlist = (body: SaveWatchlistRequest) =>
  postJSON<StartJobResponse>('/api/screener/save-watchlist', body);

export interface StagedShortSources {
  swing?: string | null;
}

/** Latest (or pinned) staged swing-short run + gate context (shorts sub-tab). */
export const useStagedShortScreener = (
  sources: StagedShortSources = {},
  refetchInterval: Refetch = false,
) =>
  useQuery({
    queryKey: ['stagedShortScreener', sources.swing],
    queryFn: () =>
      getJSON<StagedShortScreenerResponse>(
        `/api/screener/shorts/staged${qs({ swingSource: sources.swing })}`,
      ),
    refetchInterval,
  });

export const runShortScreener = (body: ShortScreenerRunRequest) =>
  postJSON<StartJobResponse>('/api/screener/shorts/run', body);

export interface StagedBottomFlowSources {
  bottomFlow?: string | null;
}

/** Latest (or pinned) staged bottom-flow-divergence run + gate context (дно sub-tab). */
export const useStagedBottomFlow = (
  sources: StagedBottomFlowSources = {},
  refetchInterval: Refetch = false,
) =>
  useQuery({
    queryKey: ['stagedBottomFlow', sources.bottomFlow],
    queryFn: () =>
      getJSON<StagedBottomFlowResponse>(
        `/api/screener/bottom-flow/staged${qs({ bottomFlowSource: sources.bottomFlow })}`,
      ),
    refetchInterval,
  });

export const runBottomFlowScreener = (body: BottomFlowRunRequest) =>
  postJSON<StartJobResponse>('/api/screener/bottom-flow/run', body);

export const useTheses = (refetchInterval: Refetch = false) =>
  useQuery({ queryKey: ['theses'], queryFn: () => getJSON<ThesesResponse>('/api/theses'), refetchInterval });

export const useThesis = (id: string | null) =>
  useQuery({
    queryKey: ['thesis', id],
    queryFn: () => getJSON<ThesisDetail>(`/api/theses/${id}`),
    enabled: !!id,
  });

export const useMemory = (refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['memory'],
    queryFn: () => getJSON<MemoryResponse>('/api/memory'),
    refetchInterval,
  });

export const useSkillDoc = (skill: string | null) =>
  useQuery({
    queryKey: ['skillDoc', skill],
    queryFn: () => getJSON<SkillDocResponse>(`/api/skill-doc/${skill}`),
    enabled: !!skill,
    staleTime: 5 * 60_000,
  });

export const useSignals = (refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['signals'],
    queryFn: () => getJSON<SignalsResponse>('/api/signals'),
    refetchInterval,
  });

export const useProfile = () =>
  useQuery({ queryKey: ['profile'], queryFn: () => getJSON<TradingProfile | null>('/api/profile') });

/** Write the trading profile (partial patch merged server-side). */
export const saveProfile = (patch: Partial<TradingProfile>) =>
  putJSON<SaveProfileResponse>('/api/profile', patch);

/** Re-plan latest screener + rebuild watchlist + refresh non-active theses. */
export const recalcProfile = (date?: string | null) =>
  postJSON<StartJobResponse>('/api/actions/recalc-profile', date ? { date } : {});

export const useDocsIndex = () =>
  useQuery({
    queryKey: ['docsIndex'],
    queryFn: () => getJSON<DocsIndexResponse>('/api/docs'),
    staleTime: 5 * 60_000,
  });

export const useDocSection = (id: string | null) =>
  useQuery({
    queryKey: ['docSection', id],
    queryFn: () => getJSON<DocSectionResponse>(`/api/docs/${id}`),
    enabled: !!id,
    staleTime: 5 * 60_000,
  });

export const useAutopilot = (date: string | null, refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['autopilot', date],
    queryFn: () => getJSON<AutopilotResponse>(`/api/autopilot${dq(date)}`),
    refetchInterval,
  });

export const useTickerDates = (symbol: string) =>
  useQuery({
    queryKey: ['tickerDates', symbol],
    queryFn: () => getJSON<TickerDatesResponse>(`/api/ticker/${encodeURIComponent(symbol)}`),
    enabled: !!symbol,
  });

export const useTickerAnalysis = (symbol: string, date: string | null) =>
  useQuery({
    queryKey: ['tickerAnalysis', symbol, date],
    queryFn: () =>
      getJSON<TickerAnalysisResponse>(`/api/ticker/${encodeURIComponent(symbol)}/${date}`),
    enabled: !!symbol && !!date,
  });

export const chartUrl = (symbol: string, date: string, tf: string) =>
  `/api/ticker/${encodeURIComponent(symbol)}/${date}/chart/${tf}`;

/** Live OHLCV bars from the vendored `tv` CLI (TradingView Desktop). */
export const useOhlcv = (symbol: string, tf: string, count = 300, enabled = true, extended = false) =>
  useQuery({
    queryKey: ['ohlcv', symbol, tf, count, extended],
    queryFn: () =>
      getJSON<OhlcvResponse>(
        `/api/ohlcv/${encodeURIComponent(symbol)}?tf=${encodeURIComponent(tf)}&n=${count}` +
          (extended ? '&ext=1' : ''),
      ),
    enabled: enabled && !!symbol,
    staleTime: 60_000,
  });

/**
 * Company profile + key metrics for the chart header (TradingView scanner).
 * `symbol` must be exchange-qualified (e.g. the resolved "NASDAQ:AAPL" from
 * useOhlcv); the query is disabled until such a symbol is available.
 */
export const useFundamentals = (symbol: string | null | undefined, enabled = true) =>
  useQuery({
    queryKey: ['fundamentals', symbol],
    queryFn: () => getJSON<FundamentalsResponse>(`/api/fundamentals/${encodeURIComponent(symbol!)}`),
    enabled: enabled && !!symbol,
    staleTime: 5 * 60_000,
  });

export const useAnalysisIndex = (refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['analysisIndex'],
    queryFn: () => getJSON<AnalysisIndexResponse>('/api/analysis/tickers'),
    refetchInterval,
  });

/* ---------------- actions ---------------- */

export const runSlot = (body: { slot: string; dryRun: boolean; force: boolean; noTelegram: boolean }) =>
  postJSON<StartJobResponse>('/api/actions/run-slot', body);

export const syncAlerts = () => postJSON<StartJobResponse>('/api/actions/sync-alerts', {});

export const syncThesisAlerts = () =>
  postJSON<StartJobResponse>('/api/actions/sync-thesis-alerts', {});

export const memoryOp = (body: Record<string, unknown>) =>
  postJSON<StartJobResponse>('/api/actions/memory', body);

export const deleteTheses = (ids: string[]) =>
  postJSON<StartJobResponse>('/api/actions/delete-theses', { ids });

export const analyzeTicker = (
  ticker: string,
  opts?: { createAlerts?: boolean; saveToNotes?: boolean },
) => postJSON<StartJobResponse>('/api/actions/analyze-ticker', { ticker, ...opts });

/* ---- IB bracket actions (place / cancel / fill-sync) ---- */
export const placeIbBracket = (body: Record<string, unknown>) =>
  postJSON<StartJobResponse>('/api/actions/place-ib-bracket', body);

export const cancelIbBracket = (body: Record<string, unknown>) =>
  postJSON<StartJobResponse>('/api/actions/cancel-ib-bracket', body);

export const syncIbFills = () => postJSON<StartJobResponse>('/api/actions/sync-ib-fills', {});

export const cancelIbOrder = (orderIds: string[]) =>
  postJSON<StartJobResponse>('/api/actions/cancel-ib-order', { orderIds });

/**
 * Dispatch an IB-bracket op to its endpoint by `op` — lets the generic OpRunner
 * (which only knows how to call one submitter) drive place / cancel / sync.
 */
export const ibBracketOp = (body: Record<string, unknown>): Promise<StartJobResponse> => {
  switch (body.op) {
    case 'place-ib-bracket':
      return placeIbBracket(body);
    case 'cancel-ib-bracket':
      return cancelIbBracket(body);
    case 'sync-ib-fills':
      return syncIbFills();
    case 'cancel-ib-order':
      return cancelIbOrder((body.orderIds as string[]) ?? []);
    default:
      return Promise.resolve({ ok: false, error: `unknown IB op: ${String(body.op)}` });
  }
};

export const cancelJob = (id: string) =>
  postJSON<{ ok: boolean }>(`/api/actions/jobs/${id}/cancel`, {});

export const fetchReconcile = (ticker: string, date: string | null) =>
  getJSON<ReconcileResult>(`/api/watchlist/reconcile/${encodeURIComponent(ticker)}${dq(date)}`);

export const applyReconcile = (ticker: string, date: string | null) =>
  postJSON<ApplyReconcileResponse>(
    `/api/watchlist/reconcile/${encodeURIComponent(ticker)}${dq(date)}`,
    {},
  );

export const deleteAlerts = (tickers: string[]) =>
  postJSON<StartJobResponse>('/api/actions/delete-alerts', { tickers });

export const fetchJobs = () =>
  getJSON<{ jobs: JobSummary[]; active: string | null }>('/api/actions/jobs');

export const fetchJob = (id: string) => getJSON<JobDetail>(`/api/actions/jobs/${id}`);

export async function deleteSignal(ticker: string, date: string): Promise<DeleteSignalResponse> {
  const res = await fetch(`/api/signals/${encodeURIComponent(ticker)}/${encodeURIComponent(date)}`, {
    method: 'DELETE',
  });
  const body = (await res.json()) as DeleteSignalResponse & { error?: string };
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}
