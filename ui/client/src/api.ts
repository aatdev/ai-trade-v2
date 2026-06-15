import { useQuery } from '@tanstack/react-query';
import type {
  AnalysisIndexResponse,
  ApplyReconcileResponse,
  AuthActionResponse,
  AuthStatusResponse,
  AutopilotResponse,
  DatesResponse,
  DeleteSignalResponse,
  DocsIndexResponse,
  DocSectionResponse,
  ReconcileResult,
  ExposureResponse,
  IbSnapshot,
  JobDetail,
  JobSummary,
  MarketResponse,
  MemoryResponse,
  OhlcvResponse,
  PortfolioHeat,
  ScreenersResponse,
  SignalsResponse,
  SkillDocResponse,
  Sourced,
  StartJobResponse,
  ThesesResponse,
  ThesisDetail,
  TickerAnalysisResponse,
  TickerDatesResponse,
  TradingProfile,
  Watchlist,
} from '@shared/types';

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

const dq = (date: string | null) => (date ? `?date=${encodeURIComponent(date)}` : '');

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

export const useExposure = (date: string | null, refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['exposure', date],
    queryFn: () => getJSON<ExposureResponse>(`/api/exposure${dq(date)}`),
    refetchInterval,
  });

export const useWatchlist = (date: string | null, refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['watchlist', date],
    queryFn: () => getJSON<Sourced<Watchlist>>(`/api/watchlist${dq(date)}`),
    refetchInterval,
  });

export const usePortfolio = (date: string | null, refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['portfolio', date],
    queryFn: () => getJSON<Sourced<PortfolioHeat>>(`/api/portfolio${dq(date)}`),
    refetchInterval,
  });

export const useIbSnapshot = (refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['ib'],
    queryFn: () => getJSON<IbSnapshot>('/api/ib'),
    refetchInterval,
  });

export const useMarket = (date: string | null, refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['market', date],
    queryFn: () => getJSON<MarketResponse>(`/api/market${dq(date)}`),
    refetchInterval,
  });

export const useScreeners = (date: string | null, refetchInterval: Refetch = false) =>
  useQuery({
    queryKey: ['screeners', date],
    queryFn: () => getJSON<ScreenersResponse>(`/api/screeners${dq(date)}`),
    refetchInterval,
  });

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
export const useOhlcv = (symbol: string, tf: string, count = 300, enabled = true) =>
  useQuery({
    queryKey: ['ohlcv', symbol, tf, count],
    queryFn: () =>
      getJSON<OhlcvResponse>(
        `/api/ohlcv/${encodeURIComponent(symbol)}?tf=${encodeURIComponent(tf)}&n=${count}`,
      ),
    enabled: enabled && !!symbol,
    staleTime: 60_000,
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

export const memoryOp = (body: Record<string, unknown>) =>
  postJSON<StartJobResponse>('/api/actions/memory', body);

export const deleteTheses = (ids: string[]) =>
  postJSON<StartJobResponse>('/api/actions/delete-theses', { ids });

export const analyzeTicker = (
  ticker: string,
  opts?: { createAlerts?: boolean; saveToNotes?: boolean },
) => postJSON<StartJobResponse>('/api/actions/analyze-ticker', { ticker, ...opts });

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
