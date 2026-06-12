import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { chartUrl, useAnalysisIndex, useTickerAnalysis, type Refetch } from '../api';
import { Card, Empty, ErrorNote, Loading, Modal } from './ui';

function AnalysisModal({
  symbol,
  date,
  onClose,
}: {
  symbol: string;
  date: string;
  onClose: () => void;
}) {
  const { data, isLoading } = useTickerAnalysis(symbol, date);
  const [tab, setTab] = useState<string | null>(null);
  const docs = data?.docs ?? [];
  const activeName = tab ?? docs[0]?.name ?? null;
  const activeDoc = docs.find((d) => d.name === activeName);

  return (
    <Modal
      title={`${symbol} — ${date}`}
      onClose={onClose}
      footer={
        <>
          <Link to={`/ticker/${symbol}/${date}`} className="back-link">
            Открыть страницу ↗
          </Link>
          <button onClick={onClose}>Закрыть</button>
        </>
      }
    >
      {isLoading ? (
        <Loading />
      ) : docs.length === 0 ? (
        <Empty>Нет отчётов для {symbol} на {date}.</Empty>
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
