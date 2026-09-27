import { useCallback, useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { analyzeCase, getCase, getCaseAudit, getCaseEvidence, getCaseHistoricalContext } from '../api/endpoints';
import type {
  AnalysisResultOut,
  CaseOut,
  EvidenceOut,
  HistoricalContextOut,
  RecommendationOut,
  SupplierEvaluationOut,
} from '../api/types';
import { caseStatusLabel, decisionStatusLabel, fieldLabel, statusTone } from '../lib/format';
import { EmptyState, ErrorBanner, Spinner, StatusBadge } from '../components/common';
import { CriticPanel } from '../components/CriticPanel';
import { AuditView, AuditedRun } from '../components/AuditView';
import { HistoricalContextPanel } from '../components/HistoricalContextPanel';
import RequirementMatrix from '../components/RequirementMatrix';

export default function CaseAnalysisPage() {
  const { caseId = '' } = useParams();
  const [caseInfo, setCaseInfo] = useState<CaseOut | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResultOut | null>(null);
  const [evidenceById, setEvidenceById] = useState<Map<string, EvidenceOut>>(new Map());
  const [historicalContext, setHistoricalContext] = useState<HistoricalContextOut | null>(null);
  const [auditRuns, setAuditRuns] = useState<AuditedRun[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resolving, setResolving] = useState(true);

  const loadStored = useCallback(async (dbCaseId: string, status: string) => {
    if (status !== 'completed') return;
    try {
      const [evidence, audit, history] = await Promise.all([
        getCaseEvidence(dbCaseId),
        getCaseAudit(dbCaseId),
        getCaseHistoricalContext(dbCaseId),
      ]);
      setEvidenceById(new Map(evidence.items.map((e) => [e.id, e])));
      setHistoricalContext(history);
      const exec = (audit.executions ?? []).filter((x) => x.status === 'ok' || x.status === 'error');
      setAuditRuns(
        exec.map((x) => ({
          node: x.agent,
          status: x.status,
          duration_ms: x.duration_ms,
          error: x.error,
        })),
      );
      setAuditTotal(exec.reduce((acc, x) => acc + (x.duration_ms ?? 0), 0));
    } catch {
      /* non-blocking: stored evidence/audit unavailable */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setResolving(true);
    setError(null);
    getCase(caseId)
      .then((c) => {
        if (cancelled) return;
        setCaseInfo(c);
        void loadStored(c.id, c.status);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err && err.status === 404) {
          // not a stored DB case — resolvable as a dataset/bench case via analyze
          setCaseInfo(null);
        } else {
          setError(String(err?.message ?? err));
        }
      })
      .finally(() => {
        if (!cancelled) setResolving(false);
      });
    return () => {
      cancelled = true;
    };
  }, [caseId, loadStored]);

  const run = useCallback(async () => {
    setAnalyzing(true);
    setError(null);
    try {
      const result = await analyzeCase(caseId);
      setAnalysis(result);
      setHistoricalContext(result.historical_context ?? null);
      setCaseInfo({
        id: result.db_case_id,
        name: caseInfo?.name ?? result.case_id,
        description: caseInfo?.description ?? null,
        status: result.status as CaseOut['status'],
        budget: caseInfo?.budget ?? null,
        currency: caseInfo?.currency ?? 'INR',
        metadata_json: caseInfo?.metadata_json ?? {},
        created_at: caseInfo?.created_at ?? new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
      setAuditRuns((result.node_runs ?? []).map((n) => ({ node: n.node, status: n.status, duration_ms: n.duration_ms, error: n.error })));
      setAuditTotal(result.duration_ms ?? 0);
      if (result.db_case_id) {
        try {
          const [evidence, history] = await Promise.all([
            getCaseEvidence(result.db_case_id),
            getCaseHistoricalContext(result.db_case_id),
          ]);
          setEvidenceById(new Map(evidence.items.map((e) => [e.id, e])));
          setHistoricalContext(history);
        } catch {
          setEvidenceById(new Map());
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(err && (err as { status?: number }).status === 422 ? `Analysis failed: ${msg}` : msg);
      setAnalysis(null);
    } finally {
      setAnalyzing(false);
    }
  }, [caseId, caseInfo]);

  if (resolving) return <Spinner label="Resolving case…" />;

  return (
    <div>
      <div className="case-header">
        <div>
          <Link to="/cases" className="back-link">← Cases</Link>
          <h1 className="page-title">
            {analysis ? (
              <span className="mono">{analysis.case_id}</span>
            ) : caseInfo ? (
              <span className="mono">{String(caseInfo.metadata_json?.bench_case_id ?? caseInfo.id)}</span>
            ) : (
              <span className="mono">{caseId}</span>
            )}
          </h1>
        </div>
        <div className="case-header-right">
          <StatusBadge status={caseInfo ? caseStatusLabel(caseInfo.status) : '—'} />
          {analysis ? <StatusBadge status={decisionStatusLabel(analysis.decision_status)} /> : null}
          <button
            type="button"
            className="btn-primary"
            onClick={() => void run()}
            disabled={analyzing || caseInfo?.status === 'analyzing'}
          >
            {analyzing ? 'Analyzing…' : 'Analyze Case'}
          </button>
        </div>
      </div>

      {error ? <ErrorBanner title="Analysis error" message={error} /> : null}

      {analyzing ? (
        <div className="panel">
          <Spinner label={`Running 12-node LangGraph pipeline on ${caseId}…`} />
        </div>
      ) : null}

      {analysis ? (
        <AnalysisView
          analysis={analysis}
          evidenceById={evidenceById}
          auditRuns={auditRuns}
          auditTotal={auditTotal}
          historicalContext={historicalContext}
        />
      ) : caseInfo && caseInfo.status === 'completed' ? (
        <StoredResultsView
          evidenceById={evidenceById}
          auditRuns={auditRuns}
          auditTotal={auditTotal}
          historicalContext={historicalContext}
        />
      ) : (
        <EmptyState
          title={caseInfo ? `Case ${caseInfo.id.slice(0, 8)} is not analyzed yet` : `Case ${caseId} not found locally`}
          hint={
            error
              ? error
              : 'Click “Analyze Case” to run the verification pipeline. Dataset cases (e.g. bench-001) are resolved on demand.'
          }
        />
      )}
    </div>
  );
}

function AnalysisView({
  analysis,
  evidenceById,
  auditRuns,
  auditTotal,
  historicalContext,
}: {
  analysis: AnalysisResultOut;
  evidenceById: Map<string, EvidenceOut>;
  auditRuns: AuditedRun[];
  auditTotal: number;
  historicalContext: HistoricalContextOut | null;
}) {
  const rec = analysis.recommendation ?? {};
  const recommended = rec.recommended_supplier ?? null;
  const evaluations = analysis.supplier_evaluations ?? [];

  return (
    <>
      {recommended ? (
        <RecommendationPanel rec={rec} />
      ) : (
        <NoRecommendation decisionStatus={analysis.decision_status} rec={rec} />
      )}

      <RequirementSummary analysis={analysis} />
      <SupplierComparison evaluations={evaluations} />
      <RequirementMatrix
        requirements={analysis.requirements ?? []}
        evaluations={evaluations}
        evidenceById={evidenceById}
      />
      <WhyThisDecision analysis={analysis} />
      <CriticPanel analysis={analysis} />
      <HistoricalContextPanel context={historicalContext} />
      <AuditView runs={auditRuns.length ? auditRuns : analysis.node_runs ?? []} totalMs={auditTotal || analysis.duration_ms || 0} />
    </>
  );
}

function RecommendationPanel({ rec }: { rec: RecommendationOut }) {
  const supplier = String(rec.recommended_supplier ?? '');
  const score = Number(rec.overall_score ?? 0);
  const confidence = Number(rec.confidence ?? 0);
  return (
    <section className="panel rec-panel">
      <div className="rec-left">
        <h2 className="panel-title">Recommendation</h2>
        <div className="rec-supplier">{supplier}</div>
        <div className="rec-meta">
          <span className="muted">Score</span>
          <strong>{score.toFixed(0)}</strong>
          <span className="muted">Confidence</span>
          <strong>{confidence.toFixed(2)}</strong>
        </div>
      </div>
      {rec.summary ? (
        <div className="rec-summary">
          <p>{String(rec.summary)}</p>
        </div>
      ) : null}
    </section>
  );
}

function NoRecommendation({
  decisionStatus,
  rec,
}: {
  decisionStatus: string;
  rec: RecommendationOut;
}) {
  const reason = decisionStatus === 'ABSTAIN' ? rec.summary : null;
  return (
    <section className="panel">
      <h2 className="panel-title">Decision: {decisionStatus}</h2>
      <p>
        {decisionStatus === 'ABSTAIN'
          ? 'No supplier recommendation was issued — the analysis abstained.'
          : 'No valid supplier satisfies the mandatory requirements for this case.'}
      </p>
      {reason ? <p className="cell-reason">{String(reason)}</p> : null}
    </section>
  );
}

function RequirementSummary({ analysis }: { analysis: AnalysisResultOut }) {
  const map = new Map<string, string>();
  for (const ev of analysis.supplier_evaluations ?? []) {
    for (const c of ev.checks ?? []) {
      map.set(c.field, c.status);
    }
  }
  const fields = [...map.keys()];
  if (fields.length === 0) return null;
  return (
    <section className="panel">
      <h2 className="panel-title">Requirements</h2>
      <div className="req-chips">
        {fields.map((f) => (
          <span key={f} className="req-chip">
            <span className="muted">{fieldLabel(f)}</span>
            <StatusBadge status={map.get(f)} />
          </span>
        ))}
      </div>
    </section>
  );
}

function WhyThisDecision({ analysis }: { analysis: AnalysisResultOut }) {
  const rec = analysis.recommendation ?? {};
  const reasons: string[] = rec.reasons ?? [];
  const mandatorySatisfied = (analysis.supplier_evaluations ?? []).filter((e) => e.passed).length > 0;
  if (reasons.length === 0) {
    return (
      <section className="panel">
        <h2 className="panel-title">Why this decision?</h2>
        <p className="muted">No explicit decision reasoning recorded.</p>
      </section>
    );
  }
  return (
    <section className="panel">
      <h2 className="panel-title">Why this decision?</h2>
      <ul className="reason-list">
        {reasons.map((r, idx) => (
          <li key={idx}>
            <span className="checkmark">{mandatorySatisfied ? '✓' : '·'}</span>
            {r}
          </li>
        ))}
      </ul>
    </section>
  );
}

function SupplierComparison({ evaluations }: { evaluations: SupplierEvaluationOut[] }) {
  if (evaluations.length === 0) return null;
  return (
    <section className="panel">
      <h2 className="panel-title">Supplier comparison</h2>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Supplier</th>
              <th>Overall score</th>
              <th>Compliance</th>
              <th>Price</th>
              <th>Delivery</th>
              <th>Certification</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {evaluations.map((ev) => {
              const byField = new Map(ev.checks.map((c) => [c.field, c.status]));
              return (
                <tr key={ev.supplier_name}>
                  <td className="cell-link">{ev.supplier_name}</td>
                  <td>{ev.score.toFixed(1)}</td>
                  <td><StatusBadge status={byField.get('material') ?? 'UNVERIFIED'} /></td>
                  <td><StatusBadge status={byField.get('price') ?? 'UNVERIFIED'} /></td>
                  <td><StatusBadge status={byField.get('delivery_days') ?? 'UNVERIFIED'} /></td>
                  <td><StatusBadge status={byField.get('certification') ?? 'UNVERIFIED'} /></td>
                  <td>
                    <span className={`badge badge-${statusTone(ev.status)}`}>{ev.status}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function StoredResultsView({
  evidenceById,
  auditRuns,
  auditTotal,
  historicalContext,
}: {
  evidenceById: Map<string, EvidenceOut>;
  auditRuns: AuditedRun[];
  auditTotal: number;
  historicalContext: HistoricalContextOut | null;
}) {
  return (
    <>
      <div className="banner banner-info">
        <strong>Previously analyzed.</strong>
        <span> Run analysis again to refresh the recommendation, critic verdict and execution audit.</span>
      </div>
      {evidenceById.size > 0 ? (
        <section className="panel">
          <h2 className="panel-title">Evidence (<span className="mono">{evidenceById.size}</span>)</h2>
          {[...evidenceById.values()].map((ev) => (
            <article key={ev.id} className="evidence-snippet">
              <div className="snippet-meta">
                <span className="doc-chip">{ev.document_name}</span>
                {ev.page != null && <span className="muted">Page {ev.page}</span>}
                {ev.section && <span className="muted">{ev.section}</span>}
              </div>
              <blockquote className="snippet-text">{ev.text}</blockquote>
            </article>
          ))}
        </section>
      ) : null}
      <HistoricalContextPanel context={historicalContext} />
      {auditRuns.length > 0 ? <AuditView runs={auditRuns} totalMs={auditTotal} /> : null}
    </>
  );
}
