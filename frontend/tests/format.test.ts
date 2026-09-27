import { describe, expect, it } from 'vitest';
import {
  caseStatusLabel,
  decisionStatusLabel,
  evidenceKind,
  fieldLabel,
  fmtNumber,
  statusTone,
} from '../src/lib/format';

describe('caseStatusLabel', () => {
  it('maps backend statuses to display labels', () => {
    expect(caseStatusLabel('completed')).toBe('ANALYZED');
    expect(caseStatusLabel('draft')).toBe('NEW');
    expect(caseStatusLabel('failed')).toBe('FAILED');
    expect(caseStatusLabel('analyzing')).toBe('ANALYZING');
  });
});

describe('statusTone', () => {
  it('classifies ok/warn/bad/muted', () => {
    expect(statusTone('completed')).toBe('ok');
    expect(statusTone('PASS')).toBe('ok');
    expect(statusTone('FAIL')).toBe('bad');
    expect(statusTone('WARNING')).toBe('warn');
    expect(statusTone('ABSTAIN')).toBe('warn');
    expect(statusTone('UNCERTAIN')).toBe('warn');
    expect(statusTone('something')).toBe('muted');
  });
});

describe('decisionStatusLabel', () => {
  it('labels the three decision outcomes', () => {
    expect(decisionStatusLabel('RECOMMEND')).toBe('RECOMMEND');
    expect(decisionStatusLabel('ABSTAIN')).toBe('ABSTAIN');
    expect(decisionStatusLabel('NO_VALID_SUPPLIER')).toBe('NO VALID SUPPLIER');
    expect(decisionStatusLabel(null)).toBe('PENDING');
  });
});

describe('evidenceKind', () => {
  const base = { requirement: 'r1', field: 'material', expected: null, actual: null, reason: '', evidence_ids: [] };
  it('reports MISSING before anything else', () => {
    expect(evidenceKind({ ...base, status: 'PASS' }, false)).toBe('MISSING');
  });
  it('classifies PASS as SUPPORTS', () => {
    expect(evidenceKind({ ...base, status: 'PASS' }, true)).toBe('SUPPORTS');
  });
  it('classifies FAIL as CONTRADICTS', () => {
    expect(evidenceKind({ ...base, status: 'FAIL' }, true)).toBe('CONTRADICTS');
  });
});

describe('fmtNumber', () => {
  it('formats integers', () => {
    expect(fmtNumber(1096650)).toBe('10,96,650');
  });
  it('unwraps value dicts', () => {
    expect(fmtNumber({ v: 'SS316L' })).toBe('SS316L');
  });
  it('renders null as a placeholder', () => {
    expect(fmtNumber(null)).toBe('—');
  });
});

describe('fieldLabel', () => {
  it('maps known fields and falls back to the raw field', () => {
    expect(fieldLabel('delivery_days')).toBe('Delivery');
    expect(fieldLabel('something_else')).toBe('something_else');
  });
});