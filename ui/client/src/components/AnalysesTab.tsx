import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAnalysisIndex, type Refetch } from '../api';
import AnalysisModal from './AnalysisModal';
import { Card, Empty, ErrorNote, Loading } from './ui';

export default function AnalysesTab({ refetch }: { refetch: Refetch }) {
  const { data, isLoading, error } = useAnalysisIndex(refetch);
  const [filter, setFilter] = useState('');
  const [sel, setSel] = useState<{ symbol: string; date: string } | null>(null);

  const entries = useMemo(() => {
    const all = Object.entries(data?.tickers ?? {}).sort(([a], [b]) => a.localeCompare(b));
    const q = filter.trim().toUpperCase();
    return q ? all.filter(([sym]) => sym.includes(q)) : all;
  }, [data, filter]);

  if (isLoading)
    return (
      <Card title="Анализы акций" className="full">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Анализы акций" className="full">
        <ErrorNote error={error} />
      </Card>
    );

  const total = Object.keys(data?.tickers ?? {}).length;

  return (
    <Card title={`Анализы акций (${total})`} className="full">
      {total === 0 ? (
        <Empty>Сохранённых анализов пока нет.</Empty>
      ) : (
        <>
          <div className="control" style={{ marginBottom: 12 }}>
            Тикер
            <input
              placeholder="фильтр…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              style={{ width: 120 }}
            />
            {filter ? (
              <button className="link-btn" onClick={() => setFilter('')}>
                clear
              </button>
            ) : null}
          </div>

          <div className="analyses-list">
            {entries.map(([sym, e]) => (
              <div className="analysis-row" key={sym}>
                <div className="analysis-sym">
                  <Link to={`/ticker/${sym}`}>{sym}</Link>
                  <span className="muted"> · {e.count}</span>
                </div>
                <div className="analysis-dates">
                  {[...e.dates].reverse().map((d) => (
                    <button
                      key={d}
                      className="date-chip"
                      onClick={() => setSel({ symbol: sym, date: d })}
                      title={`Открыть отчёт ${sym} за ${d}`}
                    >
                      {d}
                    </button>
                  ))}
                </div>
              </div>
            ))}
            {entries.length === 0 ? <Empty>Ничего не найдено.</Empty> : null}
          </div>
        </>
      )}

      {sel ? (
        <AnalysisModal
          key={`${sel.symbol}/${sel.date}`}
          symbol={sel.symbol}
          date={sel.date}
          onClose={() => setSel(null)}
        />
      ) : null}
    </Card>
  );
}
