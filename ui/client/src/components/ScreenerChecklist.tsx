import type { CheckState, ChecklistResult } from '@shared/types';

const ICON: Record<CheckState, string> = { pass: '✓', fail: '✗', unknown: '?' };
const COLOR: Record<CheckState, string> = {
  pass: 'var(--green)',
  fail: 'var(--red)',
  unknown: 'var(--muted)',
};

/** The 8-point 5.3 "беру / не беру" checklist. `?` = not yet determinable. */
export default function ScreenerChecklist({ checklist }: { checklist: ChecklistResult }) {
  return (
    <div className="field" style={{ marginBottom: 8 }}>
      <strong>
        Чек-лист 5.3 — ДА: {checklist.knownPass}/{checklist.total}
        {checklist.allPass ? (
          <span style={{ color: 'var(--green)' }}> · все пункты пройдены</span>
        ) : null}
      </strong>
      <ul style={{ margin: '6px 0 0', paddingLeft: 18, listStyle: 'none' }}>
        {checklist.points.map((p) => (
          <li key={p.key} style={{ margin: '2px 0' }}>
            <span style={{ color: COLOR[p.state], fontWeight: 600, width: 14, display: 'inline-block' }}>
              {ICON[p.state]}
            </span>{' '}
            {p.label}
            {p.detail ? <span className="muted"> — {p.detail}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
