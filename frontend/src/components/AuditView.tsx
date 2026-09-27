import { StatusBadge } from './common';

const NODE_LABELS: Record<string, string> = {
  case_loader: 'Case Loader',
  document_discovery: 'Document Discovery',
  requirement_analyzer: 'Requirement Analyzer',
  extraction: 'Extraction',
  evidence: 'Evidence',
  memory_retrieval: 'Memory Retrieval',
  supplier_evaluation: 'Supplier Evaluation',
  deterministic_verification: 'Deterministic Verification',
  evidence_retrieval: 'Evidence Retrieval (RAG)',
  critic: 'Critic',
  decision: 'Decision',
  memory_write: 'Memory Write',
};

export interface AuditedRun {
  node: string;
  status: string;
  duration_ms: number | null;
  error?: string | null;
}

/** LangGraph node execution timeline with real timings from the API. */
export function AuditView({ runs, totalMs }: { runs: AuditedRun[]; totalMs: number }) {
  const total = totalMs;
  const totalNode = runs.reduce((acc, r) => acc + (r.duration_ms ?? 0), 0);

  if (runs.length === 0) {
    return <p className="muted">No execution recorded for this analysis.</p>;
  }

  const maxMs = Math.max(1, ...runs.map((r) => r.duration_ms ?? 0));

  return (
    <section className="panel">
      <h2 className="panel-title">
        Execution <span className="panel-tag">{runs.length} graph nodes</span>
      </h2>
      <div className="bar-line">
        <span className="bar-fill" style={{ width: `${Math.min(100, (totalNode / Math.max(1, total)) * 100)}%` }} />
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Step</th>
            <th>Status</th>
            <th style={{ width: 220 }}>Duration</th>
            <th className="mono">ms</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.node}>
              <td className="node-name">{NODE_LABELS[r.node] ?? r.node}</td>
              <td><StatusBadge status={r.status === 'ok' ? 'ok' : 'error'} /></td>
              <td>
                <div className="dur-bar">
                  <div
                    className={r.status === 'ok' ? 'dur-fill fill-ok' : 'dur-fill fill-bad'}
                    style={{ width: `${((r.duration_ms ?? 0) / maxMs) * 100}%` }}
                  />
                </div>
              </td>
              <td className="mono">{r.duration_ms ?? 0}</td>
              <td className="muted">{r.error ?? '—'}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={3}>Total pipeline wall time</td>
            <td className="mono">{total}</td>
            <td />
          </tr>
          <tr className="muted">
            <td colSpan={3}>Sum of node durations</td>
            <td className="mono">{totalNode}</td>
            <td />
          </tr>
        </tfoot>
      </table>
    </section>
  );
}