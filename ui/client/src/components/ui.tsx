import { type ReactNode, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { AnalysisIndexEntry } from '@shared/types';
import { gradeColor, scoreColor, sideColor, zoneColor } from '../lib/zones';
import { fmtScore } from '../lib/format';

/** Modal dialog. Closes on Escape and on backdrop click. */
export function Modal({
  title,
  onClose,
  children,
  footer,
  fullscreen,
  wide,
}: {
  title?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  fullscreen?: boolean;
  /** Roomier variant for content-heavy dialogs (reports, charts). */
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className={`modal-backdrop${fullscreen ? ' modal-backdrop-full' : ''}`} onClick={onClose}>
      <div
        className={`modal${fullscreen ? ' modal-full' : ''}${wide ? ' modal-wide' : ''}`}
        onClick={(e) => e.stopPropagation()}
      >
        {title != null ? (
          <div className="modal-head">
            <h3>{title}</h3>
            <button className="modal-x" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        ) : null}
        {children}
        {footer != null ? (
          <div className="btn-row" style={{ justifyContent: 'flex-end', marginTop: 16 }}>
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function Card({
  title,
  source,
  sourceSelect,
  children,
  className,
}: {
  title: string;
  source?: string | null;
  /** Renders in the header in place of the plain `source` badge (e.g. a version picker). */
  sourceSelect?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className ?? ''}`}>
      <h2>
        {title}
        {sourceSelect ?? (source ? <span className="src">{source}</span> : null)}
      </h2>
      {children}
    </section>
  );
}

export function Loading() {
  return <div className="spinner">Loading…</div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  return <div className="err">Error: {error instanceof Error ? error.message : String(error)}</div>;
}

export function Empty({ children }: { children?: ReactNode }) {
  return <div className="empty">{children ?? 'No data for this date.'}</div>;
}

export function ZoneBadge({ zone, color }: { zone?: string | null; color?: string | null }) {
  if (!zone) return <span className="muted">—</span>;
  return (
    <span className="badge" style={{ color: zoneColor(color || zone) }}>
      {zone}
    </span>
  );
}

export function GradeBadge({ grade }: { grade?: string | null }) {
  if (!grade) return <span className="muted">—</span>;
  return (
    <span className="grade" style={{ background: gradeColor(grade) }}>
      {grade}
    </span>
  );
}

/**
 * Pointer to a ticker's saved analysis; renders nothing if none exists. With
 * `onOpen` it renders a button that opens the analysis modal in place; without
 * it, a link through to the standalone ticker page.
 */
export function AnalysisLink({
  ticker,
  entry,
  compact = false,
  onOpen,
}: {
  ticker: string;
  entry?: AnalysisIndexEntry;
  compact?: boolean;
  onOpen?: (ticker: string) => void;
}) {
  if (!entry) return null;
  const label = compact ? '📄' : '📄 analysis →';
  const title = `Analysis available — latest ${entry.latest} (${entry.count} day${entry.count === 1 ? '' : 's'})`;
  if (onOpen) {
    return (
      <button type="button" className="analysis-link" title={title} onClick={() => onOpen(ticker)}>
        {label}
      </button>
    );
  }
  return (
    <Link to={`/ticker/${ticker}`} className="analysis-link" title={title}>
      {label}
    </Link>
  );
}

export function SideBadge({ side }: { side?: string | null }) {
  const s = (side || '').toLowerCase();
  return (
    <span className="badge" style={{ color: sideColor(side) }}>
      {s === 'short' ? 'SHORT' : 'LONG'}
    </span>
  );
}

export function ScoreBar({ score, height = 7 }: { score: number | null; height?: number }) {
  const pct = score == null ? 0 : Math.max(0, Math.min(100, score));
  return (
    <div className="scorebar" style={{ height }}>
      <span style={{ width: `${pct}%`, background: scoreColor(score) }} />
    </div>
  );
}

export function Gauge({ label, score }: { label: string; score: number | null }) {
  return (
    <div className="gauge">
      <div className="top">
        <span className="muted">{label}</span>
        <span className="score" style={{ color: scoreColor(score) }}>
          {fmtScore(score)}
        </span>
      </div>
      <ScoreBar score={score} />
    </div>
  );
}

export function Stat({ k, v, color }: { k: string; v: ReactNode; color?: string }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v" style={color ? { color } : undefined}>
        {v}
      </div>
    </div>
  );
}

export function Collapsible({
  label,
  count,
  children,
  defaultOpen = false,
}: {
  label: string;
  count?: number;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <div className="collapse-head" onClick={() => setOpen((o) => !o)}>
        {open ? '▾' : '▸'} {label}
        {count != null ? ` (${count})` : ''}
      </div>
      {open ? <div style={{ marginTop: 8 }}>{children}</div> : null}
    </div>
  );
}
