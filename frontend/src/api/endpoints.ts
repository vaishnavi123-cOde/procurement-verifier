import { request } from './client';
import type {
  AnalysisResultOut,
  AuditExecution,
  AuditSpan,
  CaseOut,
  CaseSummaryList,
  EvidenceOut,
  HealthOut,
  HistoricalContextOut,
} from './types';

export async function getHealth(): Promise<HealthOut> {
  return request<HealthOut>('/health');
}

export async function getCases(): Promise<CaseSummaryList> {
  return request<CaseSummaryList>('/api/cases');
}

export async function getCase(id: string): Promise<CaseOut> {
  return request<CaseOut>(`/api/cases/${encodeURIComponent(id)}`);
}

export async function analyzeCase(id: string): Promise<AnalysisResultOut> {
  return request<AnalysisResultOut>(`/api/cases/${encodeURIComponent(id)}/analyze`, {
    method: 'POST',
  });
}

export async function getCaseResults(id: string): Promise<{
  case: CaseOut;
  requirements: unknown[];
  suppliers: unknown[];
  evaluations: unknown[];
  recommendation: unknown;
}> {
  return request(`/api/cases/${encodeURIComponent(id)}/results`);
}

export async function getCaseEvidence(
  id: string,
): Promise<{ items: EvidenceOut[]; total: number }> {
  return request(`/api/cases/${encodeURIComponent(id)}/evidence`);
}

export async function getCaseAudit(id: string): Promise<{
  executions: AuditExecution[];
  tool_calls: unknown[];
  spans: AuditSpan[];
}> {
  return request(`/api/cases/${encodeURIComponent(id)}/audit`);
}

export async function getCaseHistoricalContext(id: string): Promise<HistoricalContextOut> {
  return request(`/api/cases/${encodeURIComponent(id)}/historical-context`);
}
