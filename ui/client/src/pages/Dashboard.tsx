import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useDates } from '../api';
import ActionsPanel from '../components/ActionsPanel';
import AutopilotCard from '../components/AutopilotCard';
import ExposureBanner from '../components/ExposureBanner';
import PositionsCard from '../components/PositionsCard';
import RegimeCard from '../components/RegimeCard';
import ScreenersCard from '../components/ScreenersCard';
import SignalsFeed from '../components/SignalsFeed';
import ThesesCard from '../components/ThesesCard';
import WatchlistCard from '../components/WatchlistCard';

export default function Dashboard() {
  const [date, setDate] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [actionsOpen, setActionsOpen] = useState(false);
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
        <button className="primary" onClick={() => setActionsOpen(true)}>
          ⚡ Actions
        </button>
      </div>

      <ExposureBanner date={date} refetch={refetch} />

      <div className="grid">
        <PositionsCard date={date} refetch={refetch} />
        <WatchlistCard date={date} refetch={refetch} />
        <RegimeCard date={date} refetch={refetch} />
        <ScreenersCard date={date} refetch={refetch} />
        <ThesesCard refetch={refetch} />
        <AutopilotCard date={date} refetch={refetch} />
        <SignalsFeed refetch={refetch} />
      </div>

      {actionsOpen ? <ActionsPanel onClose={() => setActionsOpen(false)} /> : null}
    </div>
  );
}
