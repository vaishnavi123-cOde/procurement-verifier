import type { EvidenceOut, CheckOut } from '../api/types';
import { evidenceKind, fieldLabel, fmtNumber, truncate } from '../lib/format';
import { StatusBadge } from './common';

/**
 * The Evidence Viewer — renders source snippets (document, page, section,
 * text, extracted value, confidence) and classifies each citation as
 * SUPPORTS / CONTRADICTS / MISSING.
 */
export function EvidenceViewer({
  evidence,
  check,
}: {
  evidence: EvidenceOut[];
  check: { status: string } | null;
}) {
  if (evidence.length === 0) {
    return (
      <div className="evidence">
        <div className="evidence-head">
          <span className="evidence-kind kind-missing">MISSING</span>
          <span className="muted">No evidence attached to this result.</span>
        </div>
      </div>
    );
  }

  const kind = evidenceKind(check as CheckOut, evidence.length > 0);

  return (
    <div className="evidence">
      <div className="evidence-head">
        <span className={`evidence-kind kind-${kind.toLowerCase()}`}>{kind}</span>
        <span className="muted">{evidence.length} source item{evidence.length > 1 ? 's' : ''}</span>
      </div>
      {evidence.map((ev) => (
        <EvidenceSnippet key={ev.id} ev={ev} />
      ))}
    </div>
  );
}

function EvidenceSnippet({ ev }: { ev: EvidenceOut }) {
  return (
    <article className="evidence-snippet">
      <div className="snippet-meta">
        <span className="doc-chip">{ev.document_name}</span>
        {ev.page != null && <span className="muted">Page {ev.page}</span>}
        {ev.section && <span className="muted">{ev.section}</span>}
        {ev.doc_type && <span className="muted doc-type">{ev.doc_type}</span>}
      </div>
      <blockquote className="snippet-text">{truncate(ev.text, 420)}</blockquote>
      <div className="kv-grid inset">
        <div className="kv"><span>Field</span><strong>{fieldLabel(ev.field)}</strong></div>
        <div className="kv"><span>Extracted value</span><strong>{fmtNumber(ev.value)}</strong></div>
        <div className="kv"><span>Confidence</span><strong>{ev.confidence.toFixed(2)}</strong></div>
        <div className="kv"><span>Source</span><StatusBadge status={ev.source} /></div>
      </div>
    </article>
  );
}