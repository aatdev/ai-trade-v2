/** Map a zone label / zone_color string to a CSS color variable. */
export function zoneColor(input?: string | null): string {
  const s = (input || '').toLowerCase();
  if (s.includes('very healthy') || s.includes('green')) return 'var(--green)';
  if (s.includes('healthy')) return 'var(--green)';
  if (s.includes('pink')) return 'var(--pink)';
  if (s.includes('yellow') || s.includes('neutral') || s.includes('transition')) return 'var(--yellow)';
  if (s.includes('orange') || s.includes('elevated') || s.includes('caution')) return 'var(--orange)';
  if (s.includes('red') || s.includes('breakdown') || s.includes('severe') || s.includes('bearish'))
    return 'var(--red)';
  return 'var(--muted)';
}

/** 0–100 score → traffic-light color. */
export function scoreColor(score?: number | null): string {
  if (score == null) return 'var(--muted)';
  if (score >= 60) return 'var(--green)';
  if (score >= 40) return 'var(--yellow)';
  if (score >= 20) return 'var(--orange)';
  return 'var(--red)';
}

export function decisionColor(d?: string | null): string {
  switch ((d || '').toLowerCase()) {
    case 'allow':
      return 'var(--green)';
    case 'restrict':
      return 'var(--orange)';
    case 'cash-priority':
      return 'var(--red)';
    default:
      return 'var(--muted)';
  }
}

export function gradeColor(g?: string | null): string {
  switch ((g || '').toUpperCase()) {
    case 'A':
      return 'var(--green)';
    case 'B':
      return 'var(--yellow)';
    case 'C':
      return 'var(--orange)';
    case 'D':
      return 'var(--red)';
    default:
      return 'var(--muted)';
  }
}

export function sideColor(side?: string | null): string {
  return (side || '').toLowerCase() === 'short' ? 'var(--red)' : 'var(--green)';
}

export function pnlColor(v?: number | null): string {
  if (v == null) return 'var(--text)';
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text)';
}
