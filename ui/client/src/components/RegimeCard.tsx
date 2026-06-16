import { useState } from 'react';
import type { RegimeComposite, Sourced } from '@shared/types';
import { useMarket, type Refetch, type VersionKind } from '../api';
import { fmtScore } from '../lib/format';
import { useVersionedSource } from '../lib/useVersionedSource';
import { scoreColor, term } from '../lib/zones';
import SourceSelect from './SourceSelect';
import { Card, Empty, ErrorNote, Gauge, Loading, ScoreBar, Stat, ZoneBadge } from './ui';

type PanelKey = 'breadth' | 'uptrend' | 'top' | 'macro';

const PANELS: { key: PanelKey; label: string }[] = [
  { key: 'breadth', label: 'Breadth' },
  { key: 'uptrend', label: 'Uptrend' },
  { key: 'top', label: 'Top Risk' },
  { key: 'macro', label: 'Macro' },
];

function Components({ regime }: { regime: RegimeComposite }) {
  if (regime.components.length === 0) return <div className="muted">No component breakdown.</div>;
  return (
    <div className="components">
      {regime.components.map((c) => (
        <div className="comp-row" key={c.key}>
          <div>
            <div className="lbl">{c.label}</div>
            <ScoreBar score={c.score} height={5} />
          </div>
          <div className="val" style={{ color: scoreColor(c.score) }}>
            {fmtScore(c.score)}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function RegimeCard({ date, refetch }: { date: string | null; refetch: Refetch }) {
  // One pinned version per regime panel; each resets to latest on a date change.
  const [breadth, setBreadth] = useVersionedSource(date);
  const [uptrend, setUptrend] = useVersionedSource(date);
  const [top, setTop] = useVersionedSource(date);
  const [macro, setMacro] = useVersionedSource(date);
  const sources: Record<PanelKey, string> = { breadth, uptrend, top, macro };
  const setSource: Record<PanelKey, (s: string) => void> = {
    breadth: setBreadth,
    uptrend: setUptrend,
    top: setTop,
    macro: setMacro,
  };
  const { data, isLoading, error } = useMarket(date, sources, refetch);
  const [expanded, setExpanded] = useState<string | null>(null);

  if (isLoading)
    return (
      <Card title="Market Regime">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Market Regime">
        <ErrorNote error={error} />
      </Card>
    );
  if (!data)
    return (
      <Card title="Market Regime">
        <Empty />
      </Card>
    );

  const posture = data.posture.data;
  const anyData = PANELS.some((p) => (data[p.key] as Sourced<RegimeComposite>).data);

  return (
    <Card title="Market Regime">
      {posture ? (
        <div className="stats" style={{ marginBottom: 14 }}>
          <Stat k="Posture" v={term(posture.recommendation)} />
          <Stat
            k="Composite"
            v={fmtScore(posture.composite_score)}
            color={scoreColor(posture.composite_score)}
          />
          <Stat k="Participation" v={term(posture.participation)} />
        </div>
      ) : null}

      {!anyData ? (
        <Empty>No regime reads for this date.</Empty>
      ) : (
        <div className="gauges">
          {PANELS.map((p) => {
            const sourced = data[p.key] as Sourced<RegimeComposite>;
            const regime = sourced.data;
            const open = expanded === p.key;
            return (
              <div
                key={p.key}
                style={{ cursor: 'pointer' }}
                onClick={() => setExpanded(open ? null : p.key)}
              >
                <Gauge label={p.label} score={regime?.composite_score ?? null} />
                <div
                  style={{
                    marginTop: 4,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 6,
                  }}
                >
                  <ZoneBadge zone={regime?.zone} color={regime?.zone_color} />
                  <SourceSelect
                    kind={p.key as VersionKind}
                    value={sources[p.key]}
                    latest={sourced.source}
                    onChange={setSource[p.key]}
                    refetch={refetch}
                  />
                </div>
                {open && regime ? <Components regime={regime} /> : null}
              </div>
            );
          })}
        </div>
      )}
      {posture?.rationale ? (
        <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>
          {posture.rationale}
        </p>
      ) : null}
    </Card>
  );
}
