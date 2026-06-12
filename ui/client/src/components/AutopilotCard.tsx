import { useAutopilot, type Refetch } from '../api';
import { decisionColor } from '../lib/zones';
import { Card, Collapsible, ErrorNote, Loading } from './ui';

export default function AutopilotCard({ date, refetch }: { date: string | null; refetch: Refetch }) {
  const { data, isLoading, error } = useAutopilot(date, refetch);
  if (isLoading)
    return (
      <Card title="Autopilot & Schedule" className="full">
        <Loading />
      </Card>
    );
  if (error)
    return (
      <Card title="Autopilot & Schedule" className="full">
        <ErrorNote error={error} />
      </Card>
    );

  const state = data?.state;
  const slotKeys = state ? Object.keys(state.slots ?? {}) : [];

  return (
    <Card title="Autopilot & Schedule" className="full">
      <div className="stats" style={{ marginBottom: 12 }}>
        <div className="stat">
          <div className="k">Last Gate Decision</div>
          <div className="v" style={{ color: decisionColor(state?.last_gate_decision) }}>
            {state?.last_gate_decision ?? '—'}
          </div>
        </div>
        <div className="stat">
          <div className="k">Autopilot Date</div>
          <div className="v">{state?.date ?? '—'}</div>
        </div>
        <div className="stat">
          <div className="k">Slots Recorded</div>
          <div className="v">{slotKeys.length > 0 ? slotKeys.join(', ') : '—'}</div>
        </div>
      </div>

      {data?.weeklyReview.data ? (
        <Collapsible label={`Weekly review — ${data.weeklyReview.source ?? ''}`}>
          <pre className="joblog">{JSON.stringify(data.weeklyReview.data, null, 2)}</pre>
        </Collapsible>
      ) : null}
      {data?.monthlyReview.data ? (
        <Collapsible label={`Monthly review — ${data.monthlyReview.source ?? ''}`}>
          <pre className="joblog">{JSON.stringify(data.monthlyReview.data, null, 2)}</pre>
        </Collapsible>
      ) : null}

      <Collapsible label="Scheduler log (last 200 lines)" count={data?.logTail.length}>
        <pre className="joblog">{(data?.logTail ?? []).join('\n') || '(empty)'}</pre>
      </Collapsible>
    </Card>
  );
}
