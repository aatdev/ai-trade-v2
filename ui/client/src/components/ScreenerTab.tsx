import { useState } from 'react';
import type { Refetch } from '../api';
import ShortScreenerPanel from './ShortScreenerPanel';
import VcpScreenerPanel from './VcpScreenerPanel';

type Sub = 'vcp' | 'short';

/**
 * The "Скринер" tab is split into two sub-tabs that share the same staging dir
 * but run independent screeners: VCP (long-side, with plan + save-watchlist) and
 * swing-short (short-side, detection-only). The server enforces a single job at
 * a time, so only one panel can run a screen at once.
 */
export default function ScreenerTab({ date, refetch }: { date: string | null; refetch: Refetch }) {
  const [sub, setSub] = useState<Sub>('vcp');
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="tabs">
        <button className={`tab ${sub === 'vcp' ? 'active' : ''}`} onClick={() => setSub('vcp')}>
          VCP — лонги
        </button>
        <button className={`tab ${sub === 'short' ? 'active' : ''}`} onClick={() => setSub('short')}>
          Swing-шорты
        </button>
      </div>
      {sub === 'vcp' ? (
        <VcpScreenerPanel date={date} refetch={refetch} />
      ) : (
        <ShortScreenerPanel date={date} refetch={refetch} />
      )}
    </div>
  );
}
