import type { AnalysisResultOut, CitationCheckOut, CriticIssueOut } from '../api/types';
import { StatusBadge } from './common';

export function CriticPanel({ analysis }: { analysis: AnalysisResultOut }) {
  const issues = analysis.critic_results ?? [];
  const blocking = issues.filter((i) => i.severity === 'blocking');
  const warnings = issues.filter((i) => i.severity !== 'blocking');
  const citationChecks = analysis.citation_checks ?? [];

  return (
    <section className="panel">
      <h2 className="panel-title">
        Critic <span className="panel-tag">evidence grounding</span>
      </h2>

      <div className="critic-summary">
        <div className="kv"><span>Status</span><StatusBadge status={analysis.critic_status} /></div>
        <div className="kv"><span>Evidence coverage</span><strong>{(analysis.evidence_coverage * 100).toFixed(0)}%</strong></div>
        <div className="kv"><span>Blocked</span><strong>{analysis.critic_blocked ? 'YES' : 'No'}</strong></div>
        <div className="kv"><span>Unsupported claims</span><strong>{analysis.unsupported_decisions?.length ?? 0}</strong></div>
        <div className="kv"><span>Supported claims</span><strong>{analysis.supported_decisions?.length ?? 0}</strong></div>
        <div className="kv"><span>Citation checks</span><strong>{citationChecks.length}</strong></div>
      </div>

      {analysis.critic_blocked ? (
        <div className="banner banner-error">
          <strong>Recommendation blocked by critic.</strong>
          <span>The decision was downgraded to ABSTAIN because the recommendation is not fully evidence-supported.</span>
        </div>
      ) : null}

      {blocking.length === 0 && warnings.length === 0 && (analysis.citation_errors?.length ?? 0) === 0 ? (
        <p className="ok-note">No evidence-grounding issues detected.</p>
      ) : (
        <>
          {blocking.length > 0 ? (
            <IssueGroup title="Blocking issues" issues={blocking} />
          ) : null}
          {warnings.length > 0 ? (
            <IssueGroup title="Warnings" issues={warnings} />
          ) : null}
          {(analysis.citation_errors?.length ?? 0) > 0 ? (
            <IssueGroup title="Citation errors" issues={analysis.citation_errors ?? []} />
          ) : null}
        </>
      )}

      {citationChecks.length > 0 ? <CitationTable checks={citationChecks} /> : null}
    </section>
  );
}

function IssueGroup({ title, issues }: { title: string; issues: CriticIssueOut[] }) {
  return (
    <div className="issue-group">
      <h3>{title}</h3>
      <ul className="issue-list">
        {issues.map((issue, idx) => (
          <li key={`${issue.code}-${idx}`} className="issue">
            <span className="issue-code">{issue.code}</span>
            <span>{issue.message}</span>
            {issue.supplier ? <span className="muted">— {issue.supplier}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CitationTable({ checks }: { checks: CitationCheckOut[] }) {
  return (
    <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>
            <th>Field</th>
            <th>Evidence</th>
            <th>Citation status</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {checks.map((c, idx) => (
            <tr key={`${c.evidence_id}-${idx}`}>
              <td>{c.field}</td>
              <td className="mono">{c.evidence_id.slice(0, 8)}</td>
              <td><StatusBadge status={c.status} /></td>
              <td className="muted">{c.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}