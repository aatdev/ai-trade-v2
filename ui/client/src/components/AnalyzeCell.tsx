import type { AnalysisIndexEntry } from '@shared/types';
import AnalyzeButton from './AnalyzeButton';
import { AnalysisLink } from './ui';

/**
 * Last-column cell shared by every screener result table — mirrors the
 * watchlist's analyze-cell: a "🔍 Analyze" launcher (ticker-analysis skill) plus
 * a 📄 link to the saved analysis that opens the in-place {@link AnalysisModal}
 * via `onOpen`. Renders its own `<td>` and stops row-click propagation so using
 * the controls never toggles the row's expandable drilldown.
 */
export default function AnalyzeCell({
  ticker,
  date,
  entry,
  onOpen,
}: {
  ticker: string;
  date: string | null;
  entry?: AnalysisIndexEntry;
  onOpen: (ticker: string) => void;
}) {
  const sym = ticker.toUpperCase();
  return (
    <td style={{ textAlign: 'left' }} onClick={(e) => e.stopPropagation()}>
      <div className="analyze-cell">
        <AnalyzeButton ticker={sym} date={date} />
        <AnalysisLink ticker={sym} entry={entry} onOpen={onOpen} />
      </div>
    </td>
  );
}
