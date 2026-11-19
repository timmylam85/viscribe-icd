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

const errors: Record<string, string> = {
  'Authentication required': 'Mã truy cập không hợp lệ.',
  'Encounter not found': 'Không tìm thấy buổi khám trong tài khoản này.',
  'ICD-10 catalog has not been loaded': 'Cần nạp danh mục mẫu trước khi tạo buổi khám.',
  audio_too_large: 'Tệp vượt quá giới hạn dung lượng.',
  audio_too_long: 'Tệp vượt quá giới hạn thời lượng.',
  audio_too_short: 'Bản ghi quá ngắn.',
  unsupported_audio_format: 'Chọn tệp WAV, MP3, M4A, MP4 hoặc WebM.',
  empty_audio: 'Tệp âm thanh trống.',
  invalid_audio: 'Không đọc được âm thanh. Hãy chọn một tệp khác.',
  audio_decode_timeout: 'Không thể đọc âm thanh trong thời gian cho phép.',
  audio_version_changed: 'Tệp âm thanh đã được thay thế. Hãy tải lại buổi khám.',
  media_tool_unavailable: 'Máy chủ chưa có công cụ đọc âm thanh.',
  file_stt_resource_not_configured: 'Worker chưa được cấu hình Azure STT.',
  no_speech_detected: 'Không tìm thấy lời nói trong bản ghi.',
  http_429: 'Dịch vụ chép lời đang giới hạn số lượt gọi.',
  connection_or_timeout: 'Kết nối dịch vụ chép lời bị gián đoạn.',
  worker_timeout: 'Worker bị gián đoạn trước khi xử lý xong.',
  job_not_retryable: 'Hãy chọn tệp âm thanh khác để thử lại.',
};

export function message(code: string | null) {
  return (code && errors[code]) || 'Có lỗi khi xử lý yêu cầu. Vui lòng thử lại.';
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
