import { useVersions, type Refetch, type VersionKind } from '../api';

/** Strip the kind prefix and `.json`, leaving the `YYYY-MM-DD[_HHMMSS]` stamp. */
function shortLabel(name: string): string {
  const m = name.match(/(\d{4}-\d{2}-\d{2}(?:_\d+)?)/);
  return m ? m[1] : name.replace(/\.json$/, '');
}

/**
 * The filename badge in a card header, upgraded to a version picker. Lists the
 * last ~10 files for `kind` (newest first); the empty value means "latest"
 * (date-driven) and shows the currently-resolved basename. Selecting a file
 * pins that historical snapshot via the kind's `*Source` query param.
 */
export default function SourceSelect({
  kind,
  value,
  latest,
  onChange,
  refetch = false,
}: {
  kind: VersionKind;
  value: string;
  /** Basename actually resolved for the default option label (from the data response). */
  latest: string | null;
  onChange: (source: string) => void;
  refetch?: Refetch;
}) {
  const { data } = useVersions(kind, refetch);
  const versions = data?.versions ?? [];
  // Keep an out-of-window pin visible rather than silently dropping it.
  const options = value && !versions.includes(value) ? [value, ...versions] : versions;

  return (
    <select
      className="src src-select"
      value={value}
      title="Версия файла — последние 10"
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{latest ? `latest · ${shortLabel(latest)}` : 'latest'}</option>
      {options.map((v) => (
        <option key={v} value={v}>
          {shortLabel(v)}
        </option>
      ))}
    </select>
  );
}
