import { useState } from 'react';
import type { Refetch } from '../api';
import BottomFlowPanel from './BottomFlowPanel';
import ShortScreenerPanel from './ShortScreenerPanel';
import VcpScreenerPanel from './VcpScreenerPanel';

type Sub = 'vcp' | 'short' | 'bottom';

/**
 * The "Скринер" tab splits into sub-tabs that share the same staging dir but run
 * independent screeners: VCP (long-side, with plan + save-watchlist), swing-short
 * (short-side, detection-only) and bottom-flow divergence (beaten-down reversal
 * candidates, detection-only). The server enforces a single job at a time, so
 * only one panel can run a screen at once.
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
        <button className={`tab ${sub === 'bottom' ? 'active' : ''}`} onClick={() => setSub('bottom')}>
          Дно — дивергенция
        </button>
      </div>
      {sub === 'vcp' ? (
        <VcpScreenerPanel date={date} refetch={refetch} />
      ) : sub === 'short' ? (
        <ShortScreenerPanel date={date} refetch={refetch} />
      ) : (
        <BottomFlowPanel date={date} refetch={refetch} />
      )}
    </div>
  );
}
