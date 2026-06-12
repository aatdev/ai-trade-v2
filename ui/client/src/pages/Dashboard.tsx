import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useDates } from '../api';
import ActionsPanel from '../components/ActionsPanel';
import AnalysesTab from '../components/AnalysesTab';
import AnalyzeDialog from '../components/AnalyzeDialog';
import AutopilotCard from '../components/AutopilotCard';
import ExposureBanner from '../components/ExposureBanner';
import PositionsCard from '../components/PositionsCard';
import RegimeCard from '../components/RegimeCard';
import ScreenersCard from '../components/ScreenersCard';
import SignalsFeed from '../components/SignalsFeed';
import ThemeToggle from '../components/ThemeToggle';
import ThesesCard from '../components/ThesesCard';
import WatchlistCard from '../components/WatchlistCard';

type Tab = 'overview' | 'signals' | 'analyses';

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Обзор' },
  { key: 'signals', label: 'Сигналы' },
  { key: 'analyses', label: 'Анализы' },
];

export default function Dashboard() {
  const [date, setDate] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [tab, setTab] = useState<Tab>('overview');
  const qc = useQueryClient();

  const { data: dates } = useDates(autoRefresh ? 60_000 : false);
  const refetch = autoRefresh ? 30_000 : false;
  const latest = dates?.latest ?? null;

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
        <button className="primary" onClick={() => setActionsOpen(true)}>
          ⚡ Actions
        </button>
      </div>

      <div className="tabs" style={{ marginBottom: 16 }}>
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'overview' ? (
        <>
          <ExposureBanner date={date} refetch={refetch} />
          <div className="grid">
            <PositionsCard date={date} refetch={refetch} />
            <WatchlistCard date={date} refetch={refetch} />
            <RegimeCard date={date} refetch={refetch} />
            <ScreenersCard date={date} refetch={refetch} />
            <ThesesCard refetch={refetch} />
            <AutopilotCard date={date} refetch={refetch} />
          </div>
        </>
      ) : null}

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

      {actionsOpen ? <ActionsPanel onClose={() => setActionsOpen(false)} /> : null}
    </div>
  );
}
