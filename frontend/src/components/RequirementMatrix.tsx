import { useState } from 'react';
import type { RequirementOut, SupplierEvaluationOut, EvidenceOut } from '../api/types';
import { fieldLabel, fmtNumber } from '../lib/format';
import { EvidenceViewer } from './EvidenceViewer';

interface Cell {
  requirement: RequirementOut;
  check: { status: string; expected: unknown; actual: unknown; reason: string; evidence_ids: string[] } | null;
  evidence: EvidenceOut[];
}

export default function RequirementMatrix({
  requirements,
  evaluations,
  evidenceById,
}: {
  requirements: RequirementOut[];
  evaluations: SupplierEvaluationOut[];
  evidenceById: Map<string, EvidenceOut>;
}) {
  const [active, setActive] = useState<Cell | null>(null);

  if (requirements.length === 0 || evaluations.length === 0) {
    return null;
  }

  const cellFor = (req: RequirementOut, ev: SupplierEvaluationOut): Cell => {
    const check = ev.checks.find((c) => c.field === req.field && c.requirement === req.id) ?? null;
    const ids = check?.evidence_ids ?? [];
    const evidence = ids.map((id) => evidenceById.get(id)).filter((e): e is EvidenceOut => Boolean(e));
    return { requirement: req, check, evidence };
  };

  const statusClass = (status: string | undefined): string => {
    switch (status) {
      case 'PASS':
        return 'cell-pass';
      case 'FAIL':
        return 'cell-fail';
      case 'WARNING':
        return 'cell-warn';
      default:
        return 'cell-unknown';
    }
  };

  return (
    <section className="panel">
      <h2 className="panel-title">Requirement matrix</h2>
      <p className="muted">Click any cell to inspect the evidence behind it.</p>
      <div className="table-scroll">
        <table className="table matrix">
          <thead>
            <tr>
              <th>Requirement</th>
              {evaluations.map((ev) => (
                <th key={ev.supplier_name}>{ev.supplier_name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {requirements.map((req) => (
              <tr key={req.id}>
                <td className="matrix-req">
                  <div className="muted">{fieldLabel(req.field)}</div>
                  <div className="matrix-value">{fmtNumber(req.value)}</div>
                </td>
                {evaluations.map((ev) => {
                  const cell = cellFor(req, ev);
                  return (
                    <td key={ev.supplier_name}>
                      <button
                        type="button"
                        className={`matrix-cell ${statusClass(cell.check?.status)}`}
                        onClick={() => setActive(cell)}
                      >
                        {cell.check?.status ?? 'UNVERIFIED'}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {active ? (
        <EventCellPanel cell={active} onClose={() => setActive(null)} />
      ) : (
        <p className="muted empty-hint">Select a cell to view expected value, actual value, reason and evidence.</p>
      )}
    </section>
  );
}

function EventCellPanel({ cell, onClose }: { cell: Cell; onClose: () => void }) {
  const { requirement, check, evidence } = cell;
  return (
    <div className="cell-detail">
      <div className="cell-detail-head">
        <h3>
          {fieldLabel(requirement.field)}
          {requirementsOperator(requirement.operator)} {fmtNumber(requirement.value)}
        </h3>
        <button type="button" className="linklike" onClick={onClose}>
          close
        </button>
      </div>
      <div className="kv-grid">
        <div className="kv"><span>Expected</span><strong>{fmtNumber(requirement.value)}</strong></div>
        <div className="kv"><span>Actual</span><strong>{fmtNumber(check?.actual)}</strong></div>
        <div className="kv"><span>Status</span><strong>{check?.status ?? 'UNVERIFIED'}</strong></div>
        <div className="kv"><span>Source</span><strong>{requirement.evidence_document_id ? requirement.evidence_document_id.slice(0, 8) : '—'}</strong></div>
      </div>
      {check?.reason ? <p className="cell-reason">{check.reason}</p> : null}
      <EvidenceViewer evidence={evidence} check={check} />
    </div>
  );
}

function requirementsOperator(op: string): string {
  switch (op) {
    case 'gte':
      return ' ≥ ';
    case 'lte':
      return ' ≤ ';
    case 'contains':
      return ' contains ';
    case 'in':
      return ' ∈ ';
    default:
      return ' = ';
  }
}