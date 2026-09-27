import { Link } from 'react-router-dom';
import { useApi } from '../hooks/useApi';
import { getCases, getHealth } from '../api/endpoints';
import { caseStatusLabel, fmtDate, statusTone } from '../lib/format';
import { EmptyState, ErrorBanner, Spinner } from '../components/common';
import type { CaseSummaryOut } from '../api/types';

function StatCard({
  label,
  value,
  tone = 'muted',
}: {
  label: string;
  value: string | number;
  tone?: 'ok' | 'warn' | 'bad' | 'muted';
}) {
  return (
    <div className="stat-card">
      <div className={`stat-value tone-${tone}`}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function RecentTable({ cases }: { cases: CaseSummaryOut[] }) {
  if (cases.length === 0) {
    return <EmptyState title="No cases yet" hint="Run an analysis from the Cases view to see results here." />;
  }
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Case</th>
          <th>Status</th>
          <th>Recommendation</th>
          <th>Score</th>
          <th>Last analyzed</th>
        </tr>
      </thead>
      <tbody>
        {cases.slice(0, 8).map((c) => (
          <tr key={c.id}>
            <td>
              <Link to={`/cases/${c.id}`} className="cell-link">
                {c.metadata_json?.bench_case_id ? String(c.metadata_json.bench_case_id) : c.id}
              </Link>
            </td>
            <td>
              <span className={`badge badge-${statusTone(c.status)}`}>{caseStatusLabel(c.status)}</span>
            </td>
            <td>{c.recommendation?.recommended_supplier ?? '—'}</td>
            <td>{c.recommendation ? c.recommendation.overall_score.toFixed(0) : '—'}</td>
            <td className="muted">{fmtDate(c.updated_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Dashboard() {
  const cases = useApi(() => getCases(), []);
  const health = useApi(() => getHealth(), []);

  if (cases.loading) return <Spinner label="Loading cases…" />;
  if (cases.error) return <ErrorBanner title="Could not load cases" message={cases.error} />;

  const items = cases.data?.items ?? [];
  const analyzed = items.filter((c) => c.status === 'completed').length;
  const failed = items.filter((c) => c.status === 'failed').length;
  const pending = items.length - analyzed - failed;
  const withRecommendation = items.filter((c) => c.recommendation).length;

  return (
    <div>
      <h1 className="page-title">Dashboard</h1>
      <div className="stat-grid">
        <StatCard label="Total cases" value={items.length} />
        <StatCard label="Analyzed" value={analyzed} tone="ok" />
        <StatCard label="Pending" value={pending} tone="warn" />
        <StatCard label="Failed" value={failed} tone="bad" />
        <StatCard label="With recommendation" value={withRecommendation} />
      </div>

      <section className="panel">
        <h2 className="panel-title">Recent cases</h2>
        <RecentTable cases={items} />
      </section>

      <section className="panel">
        <h2 className="panel-title">System status</h2>
        {health.loading ? (
          <Spinner label="Checking…" />
        ) : health.data ? (
          <div className="kv-grid">
            <div className="kv"><span>API</span><strong>{health.data.status}</strong></div>
            <div className="kv"><span>Version</span><strong>{health.data.version}</strong></div>
            <div className="kv"><span>Environment</span><strong>{health.data.environment}</strong></div>
            <div className="kv"><span>Database</span><strong>{health.data.database}</strong></div>
            <div className="kv"><span>Vector store</span><strong>{health.data.vector_store}</strong></div>
            <div className="kv"><span>LLM provider</span><strong>{health.data.llm_provider}</strong></div>
          </div>
        ) : (
          <ErrorBanner title="API unavailable" message={health.error ?? 'Unknown error'} />
        )}
      </section>
    </div>
  );
}