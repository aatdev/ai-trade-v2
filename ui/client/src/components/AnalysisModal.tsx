import { useState } from 'react';
import { Link } from 'react-router-dom';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { chartUrl, useTickerAnalysis, useTickerDates } from '../api';
import { Empty, Loading, Modal } from './ui';

/**
 * Modal view of a ticker's saved analysis (markdown docs + daily/weekly charts).
 * Reused by the watchlist, screeners and the analyses tab so a click never has
 * to navigate away from the dashboard. When opened without an explicit `date`
 * (e.g. from the watchlist) it falls back to the most recent saved analysis and
 * lets the user switch between dates in place.
 */
export default function AnalysisModal({
  symbol,
  date: initialDate,
  onClose,
}: {
  symbol: string;
  date?: string | null;
  onClose: () => void;
}) {
  const { data: datesData } = useTickerDates(symbol);
  const dates = datesData?.dates ?? []; // descending (latest first)
  const [picked, setPicked] = useState<string | null>(initialDate ?? null);
  const date = picked ?? initialDate ?? dates[0] ?? null;

  const { data, isLoading } = useTickerAnalysis(symbol, date);
  const [tab, setTab] = useState<string | null>(null);
  const docs = data?.docs ?? [];
  const activeName = tab ?? docs[0]?.name ?? null;
  const activeDoc = docs.find((d) => d.name === activeName);

  return (
    <Modal
      title={`${symbol}${date ? ` — ${date}` : ''}`}
      onClose={onClose}
      wide
      footer={
        <>
          {date ? (
            <Link to={`/ticker/${symbol}/${date}`} className="back-link">
              Открыть страницу ↗
            </Link>
          ) : null}
          <button onClick={onClose}>Закрыть</button>
        </>
      }
    >
      {dates.length > 1 ? (
        <div className="date-chips">
          {dates.map((d) => (
            <button
              key={d}
              className={`date-chip ${d === date ? 'active' : ''}`}
              onClick={() => {
                setPicked(d);
                setTab(null);
              }}
            >
              {d}
            </button>
          ))}
        </div>
      ) : null}

      {!date ? (
        <Empty>Нет сохранённых анализов для {symbol}.</Empty>
      ) : isLoading ? (
        <Loading />
      ) : docs.length === 0 ? (
        <Empty>
          Нет отчётов для {symbol} на {date}.
        </Empty>
      ) : (
        <>
          {docs.length > 1 ? (
            <div className="tabs">
              {docs.map((d) => (
                <button
                  key={d.name}
                  className={`tab ${d.name === activeName ? 'active' : ''}`}
                  onClick={() => setTab(d.name)}
                >
                  {d.name}
                </button>
              ))}
            </div>
          ) : null}
          <div className="md">
            {activeDoc ? <Markdown remarkPlugins={[remarkGfm]}>{activeDoc.content}</Markdown> : null}
          </div>
          {data && data.charts.length > 0 ? (
            <div style={{ display: 'grid', gap: 16, marginTop: 16 }}>
              {data.charts.map((tf) => (
                <figure key={tf} style={{ margin: 0 }}>
                  <figcaption className="muted" style={{ marginBottom: 6 }}>
                    {tf}
                  </figcaption>
                  <img
                    src={chartUrl(symbol, date, tf)}
                    alt={`${symbol} ${tf}`}
                    style={{ width: '100%', borderRadius: 8, border: '1px solid var(--border)' }}
                  />
                </figure>
              ))}
            </div>
          ) : null}
        </>
      )}
    </Modal>
  );
}
