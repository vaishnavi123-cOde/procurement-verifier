import type { CheckOut } from '../api/types';

/** Human label for a case analysis status. */
export function caseStatusLabel(status: string): string {
  switch (status) {
    case 'completed':
      return 'ANALYZED';
    case 'draft':
      return 'NEW';
    case 'ready':
      return 'READY';
    case 'analyzing':
      return 'ANALYZING';
    case 'failed':
      return 'FAILED';
    default:
      return status.toUpperCase();
  }
}

/** Tone used to pick status badge color. */
export function statusTone(status: string | null | undefined): 'ok' | 'warn' | 'bad' | 'muted' {
  const s = (status ?? '').toUpperCase();
  if (s === 'COMPLETED' || s === 'PASS' || s === 'VALID' || s === 'RECOMMEND' || s === 'OK') {
    return 'ok';
  }
  if (s === 'FAIL' || s === 'FAILED' || s === 'INVALID' || s === 'NO_VALID_SUPPLIER' || s === 'BLOCK') {
    return 'bad';
  }
  if (s === 'DRAFT' || s === 'READY' || s === 'WARNING' || s === 'UNCERTAIN' || s === 'ABSTAIN' || s === 'INSUFFICIENT') {
    return 'warn';
  }
  return 'muted';
}

export function decisionStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'RECOMMEND':
      return 'RECOMMEND';
    case 'ABSTAIN':
      return 'ABSTAIN';
    case 'NO_VALID_SUPPLIER':
      return 'NO VALID SUPPLIER';
    default:
      return (status || 'PENDING').toUpperCase();
  }
}

/** Evidence role for a requirement check. */
export type EvidenceKind = 'SUPPORTS' | 'CONTRADICTS' | 'MISSING';

export function evidenceKind(check: CheckOut, hasEvidence: boolean): EvidenceKind {
  if (!hasEvidence) return 'MISSING';
  if (check.status === 'PASS') return 'SUPPORTS';
  if (check.status === 'FAIL') return 'CONTRADICTS';
  return 'SUPPORTS';
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 19).replace('T', ' ');
}

export function fmtNumber(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') {
    if (Number.isInteger(value)) return value.toLocaleString('en-IN');
    return value.toLocaleString('en-IN', { maximumFractionDigits: 4 });
  }
  if (typeof value === 'object') {
    const v = (value as Record<string, unknown>)?.v;
    if (v !== undefined) return fmtNumber(v);
  }
  return String(value);
}

export function fieldLabel(field: string): string {
  const labels: Record<string, string> = {
    material: 'Material',
    quantity: 'Quantity',
    price: 'Unit Price',
    delivery_days: 'Delivery',
    certification: 'Certification',
    payment_terms: 'Payment Terms',
    warranty_months: 'Warranty',
    bid_validity_days: 'Bid Validity',
    total_price: 'Total Price',
  };
  return labels[field] ?? field;
}

export function truncate(text: string, max = 140): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}