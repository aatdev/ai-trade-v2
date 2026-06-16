import { useState } from 'react';
import { useExposure, type Refetch } from '../api';
import { decisionColor, decisionLabel, term } from '../lib/zones';
import { fmtPct } from '../lib/format';
import { useVersionedSource } from '../lib/useVersionedSource';
import SourceSelect from './SourceSelect';
import { ErrorNote, Loading, Modal } from './ui';

/** TraderMonty's S&P 500 market breadth dashboard — the breadth backdrop behind the exposure decision. */
const BREADTH_URL = 'https://tradermonty.github.io/market-breadth-analysis/';

export default function ExposureBanner({ date, refetch }: { date: string | null; refetch: Refetch }) {
  const [source, setSource] = useVersionedSource(date);
  const { data, isLoading, error } = useExposure(date, source, refetch);
  const [showBreadth, setShowBreadth] = useState(false);
  if (isLoading)
    return (
      <div className="banner">
        <Loading />
      </div>
    );
  if (error)
    return (
      <div className="banner">
        <ErrorNote error={error} />
      </div>
    );

  const gate = data?.gate.data;
  const posture = data?.posture.data;
  if (!gate) return <div className="banner muted">No exposure decision recorded for this date.</div>;

  const color = decisionColor(gate.decision);
  const dl = decisionLabel(gate.decision);
  return (
    <div className="banner" style={{ borderLeftColor: color }}>
      <div className="row">
        <span className="decision" style={{ color }}>
          {dl.label}
        </span>
        <span className="pill" title="raw exposure_decision">{gate.decision}</span>
        <span className="ceiling">потолок экспозиции {fmtPct(gate.net_exposure_ceiling_pct, 0)}</span>
        {posture ? (
          <span className="ceiling">
            · уклон: {term(posture.bias)} · участие: {term(posture.participation)} · уверенность:{' '}
            {term(posture.confidence)}
          </span>
        ) : null}
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="breadth-link"
          onClick={() => setShowBreadth(true)}
          title="Открыть дашборд рыночной широты (TraderMonty)"
        >
          📊 Market Breadth
        </button>
        <SourceSelect
          kind="exposure"
          value={source}
          latest={data?.gate.source ?? null}
          onChange={setSource}
          refetch={refetch}
        />
      </div>
      {dl.hint ? <div className="muted" style={{ marginTop: 4 }}>{dl.hint}</div> : null}
      {gate.rationale ? <p className="rationale">{gate.rationale}</p> : null}
      {gate.key_signals.length > 0 ? (
        <div className="chips">
          {gate.key_signals.map((s, i) => (
            <span className="chip" key={i}>
              {s}
            </span>
          ))}
        </div>
      ) : null}
      {showBreadth ? (
        <Modal
          title={
            <>
              📊 Market Breadth{' '}
              <a href={BREADTH_URL} target="_blank" rel="noreferrer" className="breadth-ext" title="Открыть в новой вкладке">
                ↗
              </a>
            </>
          }
          onClose={() => setShowBreadth(false)}
          fullscreen
          footer={<button onClick={() => setShowBreadth(false)}>Закрыть</button>}
        >
          <iframe
            src={BREADTH_URL}
            title="Market Breadth Analysis"
            style={{ flex: 1, minHeight: 0, width: '100%', border: 'none', borderRadius: 8, background: '#fff' }}
          />
        </Modal>
      ) : null}
    </div>
  );
}
