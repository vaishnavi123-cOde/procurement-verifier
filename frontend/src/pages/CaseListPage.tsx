import { Link } from 'react-router-dom';
import { useApi } from '../hooks/useApi';
import { getCases } from '../api/endpoints';
import { caseStatusLabel, fmtDate, statusTone } from '../lib/format';
import { EmptyState, ErrorBanner, Spinner } from '../components/common';
import type { CaseSummaryOut } from '../api/types';

function displayId(c: CaseSummaryOut): string {
  const bench = c.metadata_json?.bench_case_id;
  return bench ? String(bench) : c.id.slice(0, 8);
}

export default function CaseListPage() {
  const cases = useApi(() => getCases(), []);

  if (cases.loading) return <Spinner label="Loading cases…" />;
  if (cases.error) return <ErrorBanner title="Could not load cases" message={cases.error} />;

  const items = cases.data?.items ?? [];

  return (
    <div>
      <h1 className="page-title">Cases</h1>

      {items.length === 0 ? (
        <EmptyState
          title="No cases"
          hint="Open a benchmark case (e.g. bench-001) from the URL or analyze a case to populate this list."
        />
      ) : (
        <div className="panel">
          <table className="table">
            <thead>
              <tr>
                <th>Case ID</th>
                <th>Name</th>
                <th>Status</th>
                <th>Recommendation</th>
                <th>Score</th>
                <th>Confidence</th>
                <th>Last analyzed</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link to={`/cases/${c.id}`} className="cell-link">
                      {displayId(c)}
                    </Link>
                  </td>
                  <td>{c.name}</td>
                  <td>
                    <span className={`badge badge-${statusTone(c.status)}`}>{caseStatusLabel(c.status)}</span>
                  </td>
                  <td>
                    {c.recommendation ? (
                      <Link to={`/cases/${c.id}`} className="cell-link">
                        {c.recommendation.recommended_supplier ?? '—'}
                      </Link>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>{c.recommendation ? c.recommendation.overall_score.toFixed(0) : '—'}</td>
                  <td>{c.recommendation ? c.recommendation.confidence.toFixed(2) : '—'}</td>
                  <td className="muted">{fmtDate(c.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}