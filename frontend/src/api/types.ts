/* ------------------------------------------------------------------ */
/* TypeScript types mirroring backend/app/schemas/api.py               */
/* ------------------------------------------------------------------ */

export interface CaseOut {
  id: string;
  name: string;
  description: string | null;
  status: 'draft' | 'ready' | 'analyzing' | 'completed' | 'failed';
  budget: number | null;
  currency: string;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface RecommendationOut {
  id?: string;
  case_id?: string;
  recommended_supplier: string | null;
  overall_score: number;
  confidence: number;
  status: 'recommended' | 'insufficient' | 'no_valid';
  summary: string | null;
  reasons: string[];
  rejections: Array<Record<string, unknown>>;
  unknowns: string[];
  risks: string[];
  ranked_suppliers: Array<Record<string, unknown>>;
}

export interface CaseSummaryOut extends CaseOut {
  recommendation: RecommendationOut | null;
}

export interface CaseSummaryList {
  items: CaseSummaryOut[];
  total: number;
}

export interface RequirementOut {
  id: string;
  field: string;
  operator: string;
  value: unknown;
  unit: string | null;
  currency: string | null;
  mandatory: boolean;
  label: string;
  raw_text: string | null;
  evidence_document_id: string | null;
  page: number | null;
}

export interface CheckOut {
  requirement: string;
  field: string;
  status: 'PASS' | 'FAIL' | 'WARNING' | 'UNVERIFIED';
  expected: unknown;
  actual: unknown;
  reason: string;
  evidence_ids: string[];
}

export interface SupplierEvaluationOut {
  supplier_id: string | null;
  supplier_name: string;
  passed: boolean;
  score: number;
  status: string;
  checks: CheckOut[];
  rejection_reasons: string[];
  unknowns: string[];
  risks: string[];
}

export interface EvidenceOut {
  id: string;
  case_id: string;
  document_id: string;
  document_name: string;
  supplier_id: string | null;
  doc_type: string;
  page: number | null;
  section: string | null;
  text: string;
  field: string;
  value: unknown;
  confidence: number;
  source: string;
}

export interface NodeRunOut {
  node: string;
  status: 'ok' | 'error';
  started_at_ms: number;
  duration_ms: number;
  error: string | null;
}

export interface CriticIssueOut {
  code: string;
  severity: string;
  supplier: string | null;
  supplier_id: string | null;
  field: string | null;
  requirement: string | null;
  expected: unknown;
  actual: unknown;
  evidence_ids: string[];
  source_documents: string[];
  message: string;
}

export interface CitationCheckOut {
  field: string;
  evidence_id: string;
  status: 'VALID' | 'INVALID' | 'UNCERTAIN';
  reason: string;
}

export interface HistoricalContextOut {
  case_id: string;
  context_type: 'HISTORICAL_CONTEXT';
  current_evidence_label: string;
  final_decision_label: string;
  authority: Record<string, unknown>;
  previous_cases: Array<Record<string, unknown>>;
  supplier_historical_performance: Array<Record<string, unknown>>;
  historical_price_range: Record<string, unknown>;
  repeated_compliance_issues: Array<Record<string, unknown>>;
  similar_cases: Array<Record<string, unknown>>;
  provenance: Array<Record<string, unknown>>;
}

export interface AnalysisResultOut {
  case_id: string;
  db_case_id: string;
  request_id: string;
  status: string;
  decision_status: string;
  duration_ms: number;
  requirements: RequirementOut[];
  supplier_evaluations: SupplierEvaluationOut[];
  critic_results: CriticIssueOut[];
  critic_blocked: boolean;
  critic_status: string;
  evidence_coverage: number;
  supported_decisions: Array<Record<string, unknown>>;
  unsupported_decisions: Array<Record<string, unknown>>;
  citation_errors: CriticIssueOut[];
  citation_checks: CitationCheckOut[];
  recommendation: RecommendationOut;
  evidence_count: number;
  retrieval_count: number;
  historical_context?: HistoricalContextOut;
  memory_write_count: number;
  semantic_reasoning: string;
  node_runs: NodeRunOut[];
  warnings: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
  graph: {
    entry_point: string;
    nodes: string[];
    edges: string[][];
    end: string;
  };
}

export interface AuditExecution {
  id: string;
  case_id: string;
  request_id: string;
  agent: string;
  run_id: string;
  status: string;
  task: Record<string, unknown>;
  output: Record<string, unknown>;
  error: string | null;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
}

export interface AuditSpan {
  id: string;
  case_id: string;
  request_id: string;
  span_id: string;
  parent_span_id: string | null;
  name: string;
  kind: string;
  attributes: Record<string, unknown>;
  status: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
}

export interface HealthOut {
  status: string;
  version: string;
  environment: string;
  database: string;
  vector_store: string;
  llm_provider: string;
}
