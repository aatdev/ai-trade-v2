import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { chartUrl, useTickerAnalysis, useTickerDates } from '../api';
import ThemeToggle from '../components/ThemeToggle';
import { Card, Empty, Loading } from '../components/ui';

export default function TickerDetail() {
  const params = useParams();
  const symbol = (params.symbol ?? '').toUpperCase();
  const { data: datesData } = useTickerDates(symbol);
  const date = params.date ?? datesData?.dates[0] ?? null;
  const { data, isLoading } = useTickerAnalysis(symbol, date);
  const [tab, setTab] = useState<string | null>(null);

  const docs = data?.docs ?? [];
  const activeName = tab ?? docs[0]?.name ?? null;
  const activeDoc = docs.find((d) => d.name === activeName);

  return (
    <div className="app">
      <div className="topbar">
        <h1>{symbol}</h1>
        <span className="meta">{date ?? '—'}</span>
        <span style={{ flex: 1 }} />
        <ThemeToggle />
        <Link className="back-link" to="/">
          ← Dashboard
        </Link>
      </div>

      {datesData && datesData.dates.length > 0 ? (
        <div className="tabs" style={{ marginBottom: 16 }}>
          {datesData.dates.map((d) => (
            <Link key={d} to={`/ticker/${symbol}/${d}`} className={`tab ${d === date ? 'active' : ''}`}>
              {d}
            </Link>
          ))}
        </div>
      ) : null}

      {isLoading ? (
        <Loading />
      ) : !date ? (
        <Empty>No analysis found for {symbol}.</Empty>
      ) : (
        <div className="grid">
          <Card title="Analysis" className="full">
            {docs.length === 0 ? (
              <Empty>No markdown docs for {symbol} on {date}.</Empty>
            ) : (
              <>
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
                <div className="md">
                  {activeDoc ? (
                    <Markdown remarkPlugins={[remarkGfm]}>{activeDoc.content}</Markdown>
                  ) : null}
                </div>
              </>
            )}
          </Card>

          {data && data.charts.length > 0 ? (
            <Card title="Charts" className="full">
              <div style={{ display: 'grid', gap: 16 }}>
                {data.charts.map((tf) => (
                  <figure key={tf} style={{ margin: 0 }}>
                    <figcaption className="muted" style={{ marginBottom: 6 }}>
                      {tf}
                    </figcaption>
                    <img
                      src={chartUrl(symbol, date, tf)}
                      alt={`${symbol} ${tf} chart`}
                      style={{ width: '100%', borderRadius: 8, border: '1px solid var(--border)' }}
                    />
                  </figure>
                ))}
              </div>
            </Card>
          ) : null}
        </div>
      )}
    </div>
  );
}
