import { statusTone } from '../lib/format';

type Tone = 'ok' | 'warn' | 'bad' | 'muted';

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const tone: Tone = statusTone(status);
  return <span className={`badge badge-${tone}`}>{status ?? '—'}</span>;
}

export function ScoreBar({ score, max = 100 }: { score: number; max?: number }) {
  const pct = Math.max(0, Math.min(100, (score / max) * 100));
  const tone = score >= 80 ? 'ok' : score >= 60 ? 'warn' : 'bad';
  return (
    <div className="scorebar" title={`${score.toFixed(1)} / ${max}`}>
      <div className={`scorebar-fill fill-${tone}`} style={{ width: `${pct}%` }} />
      <span className="scorebar-label">{score.toFixed(0)}</span>
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="spinner-wrap">
      <span className="spinner" aria-hidden="true" />
      {label ? <span className="spinner-label">{label}</span> : null}
    </div>
  );
}

export function ErrorBanner({ title, message }: { title?: string; message: string }) {
  return (
    <div className="banner banner-error">
      <strong>{title ?? 'Error'}</strong>
      <span>{message}</span>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      {hint ? <p>{hint}</p> : null}
    </div>
  );
}