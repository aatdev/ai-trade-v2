import { useExposure, type Refetch } from '../api';
import { decisionColor } from '../lib/zones';
import { fmtPct } from '../lib/format';
import { ErrorNote, Loading } from './ui';

export default function ExposureBanner({ date, refetch }: { date: string | null; refetch: Refetch }) {
  const { data, isLoading, error } = useExposure(date, refetch);
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
  return (
    <div className="banner" style={{ borderLeftColor: color }}>
      <div className="row">
        <span className="decision" style={{ color }}>
          {gate.decision}
        </span>
        <span className="ceiling">ceiling {fmtPct(gate.net_exposure_ceiling_pct, 0)}</span>
        {posture ? (
          <span className="ceiling">
            · {posture.bias} bias · {posture.participation} participation · {posture.confidence}{' '}
            confidence
          </span>
        ) : null}
        <span style={{ flex: 1 }} />
        {data?.gate.source ? <span className="src" style={{ fontFamily: 'var(--mono)', fontSize: 11, color: '#586069' }}>{data.gate.source}</span> : null}
      </div>
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
    </div>
  );
}
