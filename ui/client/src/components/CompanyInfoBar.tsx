import type { FundamentalsResponse } from '@shared/types';
import { fmtNum, fmtPct, fmtSignedPct } from '../lib/format';
import { localizeCountry, localizeIndustry, localizeSector } from '../lib/tvLocale';

/** Compact USD with Russian magnitude suffixes (трлн / млрд / млн). */
function fmtCap(v: number | null): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 1e12) return `$${(v / 1e12).toFixed(2)} трлн`;
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)} млрд`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(1)} млн`;
  return `$${fmtNum(v, 0)}`;
}

const fmtUsd = (v: number | null): string =>
  v == null || !Number.isFinite(v) ? '—' : `$${fmtNum(v, 2)}`;

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <span className="ci-metric">
      <span className="ci-label">{label}</span>
      <span className="ci-value" style={color ? { color } : undefined}>
        {value}
      </span>
    </span>
  );
}

function Perf({ label, v }: { label: string; v: number | null }) {
  const color = v == null ? undefined : v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : undefined;
  return <Metric label={label} value={fmtSignedPct(v)} color={color} />;
}

/** Optional `Сектор • Отрасль • Страна • Сотрудники` profile tags (RU). */
function tags(d: NonNullable<FundamentalsResponse['data']>): string[] {
  const out: string[] = [];
  const sector = localizeSector(d.sector);
  const industry = localizeIndustry(d.industry);
  const country = localizeCountry(d.country);
  if (sector) out.push(sector);
  if (industry && industry !== sector) out.push(industry);
  if (country) out.push(country);
  if (d.employees != null) out.push(`${d.employees.toLocaleString('ru-RU')} сотр.`);
  return out;
}

/**
 * Company profile + key metrics strip shown above the candle chart. Sourced from
 * TradingView (scanner /symbol). Renders nothing until data arrives, so the chart
 * still works when fundamentals are unavailable (e.g. an unresolved symbol).
 */
export default function CompanyInfoBar({ funda }: { funda?: FundamentalsResponse }) {
  if (!funda?.ok || !funda.data) return null;
  const d = funda.data;
  const range =
    d.low52w != null && d.high52w != null ? `${fmtUsd(d.low52w)} – ${fmtUsd(d.high52w)}` : '—';

  return (
    <div className="company-info">
      <div className="ci-profile">
        {d.name ? <span className="ci-name">{d.name}</span> : null}
        {tags(d).length ? <span className="ci-tags">{tags(d).join(' · ')}</span> : null}
      </div>

      <div className="ci-metrics">
        <Metric label="Рын. кап." value={fmtCap(d.marketCap)} />
        <Metric label="P/E (TTM)" value={fmtNum(d.peTtm)} />
        <Metric label="EPS (TTM)" value={fmtUsd(d.epsTtm)} />
        <Metric label="P/S" value={fmtNum(d.priceToSales)} />
        <Metric label="P/B" value={fmtNum(d.priceToBook)} />
        <Metric label="Див. дох." value={fmtPct(d.dividendYield, 2)} />
        <Metric label="52 нед." value={range} />
      </div>

      <div className="ci-perf">
        <span className="ci-perf-title">Динамика:</span>
        <Perf label="Нед." v={d.perfW} />
        <Perf label="Мес." v={d.perfM} />
        <Perf label="3 мес." v={d.perf3M} />
        <Perf label="С нач. года" v={d.perfYtd} />
        <Perf label="Год" v={d.perfY} />
      </div>
    </div>
  );
}
