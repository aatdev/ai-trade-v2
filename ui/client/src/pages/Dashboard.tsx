import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { logout, useAuthStatus, useDates, useIbHealth, useJobs } from '../api';
import ActionsPanel from '../components/ActionsPanel';
import AnalysesTab from '../components/AnalysesTab';
import JobsTab, { isVisibleJob } from '../components/JobsTab';
import AnalyzeDialog from '../components/AnalyzeDialog';
import AutopilotCard from '../components/AutopilotCard';
import ExposureBanner from '../components/ExposureBanner';
import IbTab from '../components/IbTab';
import PositionsCard from '../components/PositionsCard';
import ProfileTab from '../components/ProfileTab';
import RegimeCard from '../components/RegimeCard';
import ScreenersCard from '../components/ScreenersCard';
import ScreenerTab from '../components/ScreenerTab';
import SignalsFeed from '../components/SignalsFeed';
import ThemeToggle from '../components/ThemeToggle';
import DocsModal from '../components/DocsModal';
import TraderMemoryCard from '../components/TraderMemoryCard';
import WatchlistCard from '../components/WatchlistCard';

type Tab = 'overview' | 'screener' | 'signals' | 'analyses' | 'jobs' | 'memory' | 'ib' | 'profile';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Обзор' },
  { key: 'screener', label: 'Скринер' },
  { key: 'signals', label: 'Сигналы' },
  { key: 'analyses', label: 'Анализы' },
  { key: 'memory', label: 'Память' },
  { key: 'ib', label: 'Счёт IB' },
  { key: 'profile', label: 'Профиль' },
  { key: 'jobs', label: 'Задания' },
];

export default function Dashboard() {
  const [date, setDate] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [docsOpen, setDocsOpen] = useState(false);
  const [tab, setTab] = useState<Tab>('overview');
  const qc = useQueryClient();
  const { data: auth } = useAuthStatus();

  async function onLogout() {
    await logout().catch(() => undefined);
    await qc.invalidateQueries();
  }

  const { data: dates } = useDates(autoRefresh ? 60_000 : false);
  const refetch = autoRefresh ? 30_000 : false;
  const latest = dates?.latest ?? null;

  // Poll the IB Gateway liveness on its own interval (independent of the IB
  // tab) so the "Счёт IB" tab turns red whenever the Gateway is down/logged out.
  const { data: ibHealth } = useIbHealth(autoRefresh ? 30_000 : false);
  const ibDown = ibHealth ? !ibHealth.ok : false;

  // Live count of running jobs for the "Задания" tab badge (polled app-wide so
  // it ticks even while another tab is open).
  const { data: jobsData } = useJobs(autoRefresh ? 5_000 : false);
  const runningJobs =
    jobsData?.jobs.filter((j) => j.status === 'running' && isVisibleJob(j)).length ?? 0;

  return (
    <div className="app">
      <div className="topbar">
        <h1>📊 Trading State</h1>
        <label className="control">
          Date
          <select value={date ?? ''} onChange={(e) => setDate(e.target.value || null)}>
            <option value="">latest{latest ? ` (${latest})` : ''}</option>
            {dates?.dates.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <label className="control check">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          auto-refresh
        </label>
        <button onClick={() => void qc.invalidateQueries()}>↻ Refresh</button>
        <span style={{ flex: 1 }} />
        <AnalyzeDialog date={date} />
        <ThemeToggle />
        <button onClick={() => setDocsOpen(true)}>📚 Документация</button>
        <button className="primary" onClick={() => setActionsOpen(true)}>
          ⚡ Actions
        </button>
        {auth?.authRequired && auth.authenticated ? (
          <button onClick={onLogout} title={`Выйти${auth.user ? ` (${auth.user})` : ''}`}>
            🚪 Выйти
          </button>
        ) : null}
      </div>

      <div className="tabs" style={{ marginBottom: 16 }}>
        {TABS.map((t) => {
          const down = t.key === 'ib' && ibDown;
          const jobsBadge = t.key === 'jobs' && runningJobs > 0;
          return (
            <button
              key={t.key}
              className={`tab ${tab === t.key ? 'active' : ''}${down ? ' tab-alert' : ''}`}
              onClick={() => setTab(t.key)}
              title={down ? ibHealth?.error ?? 'IB Gateway недоступен' : undefined}
            >
              {t.label}
              {down ? ' ●' : ''}
              {jobsBadge ? <span className="tab-count">{runningJobs}</span> : ''}
            </button>
          );
        })}
      </div>

      {tab === 'overview' ? (
        <>
          <ExposureBanner date={date} refetch={refetch} />
          <div className="grid">
            <PositionsCard date={date} refetch={refetch} />
            <WatchlistCard date={date} refetch={refetch} />
            <RegimeCard date={date} refetch={refetch} />
            <ScreenersCard date={date} refetch={refetch} />
            <AutopilotCard date={date} refetch={refetch} />
          </div>
        </>
      ) : null}

      {tab === 'screener' ? <ScreenerTab date={date} refetch={refetch} /> : null}

      {tab === 'signals' ? (
        <div className="grid">
          <SignalsFeed refetch={refetch} />
        </div>
      ) : null}

      {tab === 'analyses' ? (
        <div className="grid">
          <AnalysesTab refetch={refetch} />
        </div>
      ) : null}

      {tab === 'jobs' ? (
        <div className="grid">
          <JobsTab onNavigateTab={(t) => setTab(t as Tab)} />
        </div>
      ) : null}

      {tab === 'memory' ? (
        <div className="grid">
          <TraderMemoryCard refetch={refetch} />
        </div>
      ) : null}

      {tab === 'ib' ? (
        <div className="grid">
          <IbTab refetch={refetch} />
        </div>
      ) : null}

      {tab === 'profile' ? <ProfileTab /> : null}

      {docsOpen ? <DocsModal onClose={() => setDocsOpen(false)} /> : null}
      {actionsOpen ? <ActionsPanel onClose={() => setActionsOpen(false)} /> : null}
    </div>
  );
}
