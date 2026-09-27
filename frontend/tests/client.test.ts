import { describe, expect, it, vi, afterEach } from 'vitest';
import { ApiError, getBaseUrl, request } from '../src/api/client';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('getBaseUrl', () => {
  it('falls back to the local default when env is unset', () => {
    vi.stubEnv('VITE_API_BASE_URL', '');
    expect(getBaseUrl()).toBe('http://localhost:8000');
  });

  it('uses the configured env value and strips trailing slashes', () => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://api.example.test:9000/');
    expect(getBaseUrl()).toBe('http://api.example.test:9000');
  });
});

describe('request', () => {
  it('parses a successful JSON response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ items: [], total: 3 }), { status: 200 }),
      ),
    );
    const data = await request<{ total: number }>('/api/cases');
    expect(data.total).toBe(3);
    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/cases',
      expect.objectContaining({ headers: { 'Content-Type': 'application/json' } }),
    );
  });

  it('throws ApiError with the body detail on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Case not found.' }), { status: 404 }),
      ),
    );
    await expect(request('/api/cases/missing')).rejects.toMatchObject({
      status: 404,
      message: 'Case not found.',
    });
  });

  it('throws ApiError with the status text when the body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('oops', { status: 502 })));
    await expect(request('/health')).rejects.toMatchObject({ status: 502 });
  });

  it('throws on network failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));
    await expect(request('/health')).rejects.toThrow('ECONNREFUSED');
  });

  it('returns undefined for 204', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    const out = await request<void>('/api/cases/1/delete');
    expect(out).toBeUndefined();
  });
});

describe('ApiError', () => {
  it('is distinguishable from generic errors', () => {
    const err = new ApiError(422, 'Analysis failed');
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toBeInstanceOf(Error);
    expect(err.name).toBe('ApiError');
    expect(err.status).toBe(422);
  });
});