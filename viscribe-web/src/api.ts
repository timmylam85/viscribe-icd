import { copy } from './locale';

export type Mode = 'assisted' | 'silent';

export type Encounter = {
  id: string;
  mode: Mode;
  patientRef: string | null;
  status: string;
  createdAt: string;
  revision: number;
};

export type Audio = { audioId: string; format: string; durationMs: number; sizeBytes: number };

export type Job = {
  jobId: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'superseded';
  errorCode: string | null;
  retryable: boolean;
  availableAt: string;
  partsDone: number;
  partsTotal: number | null;
};

export type Transcript = {
  id: string;
  text: string;
  segments: { id: string; startMs: number; endMs: number; text: string }[];
};

export type Detail = Encounter & { audio?: Audio | null; job?: Job | null; transcript?: Transcript | null };

const errors: Record<string, string> = copy.errors;

export function message(code: string | null) {
  return (code && Object.hasOwn(errors, code) ? errors[code] : copy.fallbackError);
}

export async function api<T>(token: string, path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(`/api${path}`, { ...options, headers, cache: 'no-store' });
  if (!response.ok) {
    const responseBody = await response.json().catch(() => ({}));
    throw new Error(message(typeof responseBody.detail === 'string' ? responseBody.detail : null));
  }
  return response.json() as Promise<T>;
}

export function json(body: object): RequestInit {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

export function upload(token: string, path: string, file: File) {
  const form = new FormData();
  form.set('file', file);
  return api<Audio>(token, `${path}/audio`, { method: 'POST', body: form });
}
