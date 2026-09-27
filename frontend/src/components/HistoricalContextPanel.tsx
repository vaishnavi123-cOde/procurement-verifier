import type { HistoricalContextOut } from '../api/types';

function hasData(ctx: HistoricalContextOut | null | undefined): boolean {
  if (!ctx) return false;
  return (
    (ctx.previous_cases?.length ?? 0) > 0 ||
    (ctx.supplier_historical_performance?.length ?? 0) > 0 ||
    (ctx.similar_cases?.length ?? 0) > 0 ||
    (ctx.repeated_compliance_issues?.length ?? 0) > 0 ||
    Object.keys(ctx.historical_price_range ?? {}).length > 0
  );
}

function renderRecords(
  label: string,
  items: Array<Record<string, unknown>>,
): React.ReactNode | null {
  if (!items || items.length === 0) return null;
  return (
    <div className="hist-block">
      <h3 className="hist-label">{label}</h3>
      {items.map((item, idx) => (
        <details key={idx} className="hist-item">
          <summary className="hist-summary">
            {String(item.label ?? item.supplier_name ?? item.case_id ?? `${label} #${idx + 1}`)}
          </summary>
          <pre className="hist-raw">{JSON.stringify(item, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

function renderPriceRange(range: Record<string, unknown>): React.ReactNode | null {
  const keys = Object.keys(range ?? {});
  if (keys.length === 0) return null;
  return (
    <div className="hist-block">
      <h3 className="hist-label">Historical price range</h3>
      <div className="req-chips">
        {keys.map((k) => (
          <span key={k} className="req-chip">
            <span className="muted">{k}</span>
            <strong>{String(range[k])}</strong>
          </span>
        ))}
      </div>
    </div>
  );
}

/** Historical memory panel (Phase 15). Renders an empty state when no context exists. */
export function HistoricalContextPanel({ context }: { context: HistoricalContextOut | null }) {
  if (!hasData(context)) {
    return (
      <section className="panel">
        <h2 className="panel-title">Historical context</h2>
        <p className="muted">
          No historical context retrieved for this case. Previously analyzed cases, supplier
          performance records and evidence from earlier runs will appear here.
        </p>
      </section>
    );
  }
  const c = context as HistoricalContextOut;
  return (
    <section className="panel">
      <h2 className="panel-title">
        Historical context <span className="panel-tag">{c.context_type}</span>
      </h2>
      {renderRecords('Previous cases', c.previous_cases)}
      {renderRecords('Supplier historical performance', c.supplier_historical_performance)}
      {renderRecords('Similar cases', c.similar_cases)}
      {renderRecords('Repeated compliance issues', c.repeated_compliance_issues)}
      {renderPriceRange(c.historical_price_range)}
    </section>
  );
}