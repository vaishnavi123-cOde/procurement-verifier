// In production the UI is served by nginx which proxies /api to the backend,
// so the default base URL is same-origin (''). In development, vite proxies
// /api to the local backend (see vite.config.ts). Override either case with the
// VITE_API_BASE_URL environment variable — no production URL is hard-coded.
const DEFAULT_BASE_URL = '';

export class ApiError extends Error {
  public readonly status: number;
  public readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

export function getBaseUrl(): string {
  const raw = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';
  return (raw || DEFAULT_BASE_URL).replace(/\/+$/, '');
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${getBaseUrl()}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      /* non-JSON error body */
    }
    const message =
      typeof detail === 'object' && detail !== null && 'detail' in detail
        ? String((detail as Record<string, unknown>).detail)
        : `${res.status} ${res.statusText}`;
    throw new ApiError(res.status, message, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}