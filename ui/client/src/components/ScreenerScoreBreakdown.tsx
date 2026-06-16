import type { ReactNode } from 'react';
import type { StagedScreenerCandidate, VcpComponentKey } from '@shared/types';
import { fmtNum, fmtPct } from '../lib/format';
import { scoreColor } from '../lib/zones';
import { Collapsible, ScoreBar } from './ui';

/** Mirror scorer.COMPONENT_WEIGHTS (sum 1.0). */
const COMPONENTS: { key: VcpComponentKey; label: string; weight: number }[] = [
  { key: 'trend_template', label: 'Trend Template', weight: 0.25 },
  { key: 'vcp_pattern', label: 'Сжатие (VCP)', weight: 0.25 },
  { key: 'volume_pattern', label: 'Объём (dry-up)', weight: 0.2 },
  { key: 'pivot_proximity', label: 'Близость к pivot', weight: 0.15 },
  { key: 'relative_strength', label: 'Relative Strength', weight: 0.15 },
];

function Fact({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}

/** "How the composite was computed": weighted components + per-component drill-down. */
export default function ScreenerScoreBreakdown({ c }: { c: StagedScreenerCandidate }) {
  const t = c.components.trend_template;
  const vp = c.components.vcp_pattern;
  const vol = c.components.volume_pattern;
  const pp = c.components.pivot_proximity;
  const rs = c.components.relative_strength;

  return (
    <div style={{ padding: '4px 2px' }}>
      <table>
        <thead>
          <tr>
            <th style={{ textAlign: 'left' }}>Компонент</th>
            <th>Вес</th>
            <th>Score</th>
            <th>Вклад</th>
            <th style={{ width: 120 }} />
          </tr>
        </thead>
        <tbody>
          {COMPONENTS.map(({ key, label, weight }) => {
            const score = c.components[key].score;
            return (
              <tr key={key}>
                <td style={{ textAlign: 'left' }}>{label}</td>
                <td className="muted">{Math.round(weight * 100)}%</td>
                <td style={{ color: scoreColor(score) }}>{fmtNum(score, 0)}</td>
                <td>{fmtNum(score != null ? score * weight : null, 1)}</td>
                <td>
                  <ScoreBar score={score} />
                </td>
              </tr>
            );
          })}
          <tr style={{ fontWeight: 600 }}>
            <td style={{ textAlign: 'left' }}>Composite</td>
            <td />
            <td style={{ color: scoreColor(c.composite_score) }}>{fmtNum(c.composite_score, 1)}</td>
            <td />
            <td />
          </tr>
        </tbody>
      </table>

      {c.state_cap_applied && c.cap_reason ? (
        <div className="muted" style={{ marginTop: 6 }}>
          ⚠ {c.cap_reason}
        </div>
      ) : null}
      {c.execution_state_reasons.length ? (
        <div className="muted" style={{ marginTop: 4 }}>
          Состояние «{c.execution_state}»: {c.execution_state_reasons.join('; ')}
        </div>
      ) : null}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
          gap: 6,
          marginTop: 10,
        }}
      >
        <Fact k="Цена" v={fmtNum(c.price)} />
        <Fact k="SMA50/150/200" v={`${fmtNum(t.sma50, 0)} / ${fmtNum(t.sma150, 0)} / ${fmtNum(t.sma200, 0)}`} />
        <Fact k="Pivot" v={fmtNum(vp.pivot_price)} />
        <Fact k="До pivot" v={fmtPct(pp.distance_from_pivot_pct)} />
        <Fact k="Стоп / риск" v={`${fmtNum(pp.stop_loss_price)} (${fmtPct(pp.risk_pct)})`} />
        <Fact k="Dry-up объёма" v={fmtNum(vol.dry_up_ratio, 2)} />
        <Fact k="Сжатий" v={fmtNum(vp.num_contractions, 0)} />
        <Fact k="RS %ile" v={fmtNum(rs.rs_percentile, 0)} />
        <Fact k="Weighted RS" v={fmtPct(rs.weighted_rs)} />
        <Fact k="valid VCP" v={c.valid_vcp == null ? '—' : c.valid_vcp ? 'да' : 'нет'} />
      </div>

      <div style={{ marginTop: 10 }}>
        <Collapsible label="Trend Template — критерии" count={t.criteria_passed ?? undefined}>
          <ul style={{ margin: 0, paddingLeft: 18, listStyle: 'none' }}>
            {Object.entries(t.criteria).map(([k, cr]) => (
              <li key={k}>
                <span style={{ color: cr.passed ? 'var(--green)' : 'var(--red)' }}>
                  {cr.passed ? '✓' : '✗'}
                </span>{' '}
                <span className="muted">{cr.detail ?? k}</span>
              </li>
            ))}
          </ul>
        </Collapsible>
      </div>

      {vp.contractions.length ? (
        <div style={{ marginTop: 6 }}>
          <Collapsible label="Сжатия (contractions)" count={vp.contractions.length}>
            <div className="scroll-x">
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Глубина</th>
                    <th>Дней</th>
                    <th>Low</th>
                    <th>High</th>
                  </tr>
                </thead>
                <tbody>
                  {vp.contractions.map((ct, i) => (
                    <tr key={i}>
                      <td>{ct.label ?? `T${i + 1}`}</td>
                      <td>{fmtPct(ct.depth_pct)}</td>
                      <td>{fmtNum(ct.duration_days, 0)}</td>
                      <td>{fmtNum(ct.low_price)}</td>
                      <td>{fmtNum(ct.high_price)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {vp.contraction_ratios.length ? (
              <div className="muted" style={{ marginTop: 4 }}>
                Коэффициенты сжатия: {vp.contraction_ratios.map((r) => r.toFixed(2)).join(' · ')}
              </div>
            ) : null}
          </Collapsible>
        </div>
      ) : null}

      {rs.period_details.length ? (
        <div style={{ marginTop: 6 }}>
          <Collapsible label="Relative Strength по периодам" count={rs.period_details.length}>
            <div className="scroll-x">
              <table>
                <thead>
                  <tr>
                    <th>Период (дн.)</th>
                    <th>Вес</th>
                    <th>vs S&amp;P 500</th>
                  </tr>
                </thead>
                <tbody>
                  {rs.period_details.map((p, i) => (
                    <tr key={i}>
                      <td>{fmtNum(p.period_days, 0)}</td>
                      <td className="muted">{p.weight != null ? `${Math.round(p.weight * 100)}%` : '—'}</td>
                      <td>{fmtPct(p.relative_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Collapsible>
        </div>
      ) : null}
    </div>
  );
}
