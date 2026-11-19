import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, AudioLines, Check, FileAudio, Plus, Upload } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { api, type Detail, type Encounter, type Job, json, message, type Mode, upload } from './api';

const sessionKey = 'viscribe-local-token';
const modeLabel: Record<Mode, string> = { assisted: 'Có hỗ trợ AI', silent: 'Chế độ ngầm' };
const statusLabel: Record<string, string> = {
  created: 'Chưa có audio', audio_ready: 'Đã tải audio', transcribing: 'Đang chép lời',
  transcript_ready: 'Có transcript cuối',
};

function clock(milliseconds: number) {
  const seconds = Math.floor(milliseconds / 1000);
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

function caseName(encounter: Encounter) {
  return `Buổi khám ${encounter.patientRef || encounter.id.slice(0, 8).toUpperCase()}`;
}

function notice(error: Error | null) {
  return error ? <p role="alert" className="notice">{error.message}</p> : null;
}

function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [code, setCode] = useState('');
  const login = useMutation({
    mutationFn: (token: string) => api<Encounter[]>(token, '/encounters'),
    onSuccess: (_, token) => onLogin(token),
  });
  return (
    <form className="panel mx-auto mt-12 max-w-md p-6" onSubmit={(event) => {
      event.preventDefault();
      login.mutate(code.trim());
    }}>
      <h1 className="text-2xl font-semibold tracking-tight">Mở buổi khám</h1>
      <label className="mt-5 block text-sm font-medium" htmlFor="access">Mã truy cập môi trường thử nghiệm</label>
      <input className="field mt-2" id="access" type="password" value={code}
        onChange={(event) => setCode(event.target.value)} required autoComplete="off" />
      {notice(login.error)}
      <button className="button primary mt-4" disabled={!code.trim() || login.isPending}>
        {login.isPending ? 'Đang kiểm tra…' : 'Tiếp tục'}
      </button>
    </form>
  );
}

function EncounterList({ token, onOpen }: { token: string; onOpen: (id: string) => void }) {
  const cache = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [mode, setMode] = useState<Mode>('assisted');
  const [ref, setRef] = useState('');
  const cases = useQuery({
    queryKey: ['encounters', token],
    queryFn: () => api<Encounter[]>(token, '/encounters'),
    refetchInterval: (query) => query.state.data?.some((item) => item.status === 'transcribing') ? 2500 : false,
  });
  const create = useMutation({
    mutationFn: () => api<Encounter>(token, '/encounters', json({ mode, patientRef: ref.trim() || null })),
    onSuccess: (item) => {
      void cache.invalidateQueries({ queryKey: ['encounters', token] });
      onOpen(item.id);
    },
  });

  return <>
    <div className="page-heading">
      <div><h1>Buổi khám</h1><p>Khám ngoại trú</p></div>
      <button className="button primary" type="button" onClick={() => setCreating(true)}>
        <Plus size={16} aria-hidden /> Tạo buổi khám
      </button>
    </div>
    {creating && <form className="panel mb-4 p-5" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
      <h2>Buổi khám mới</h2>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-sm font-medium">Mã tham chiếu mô phỏng
          <input className="field mt-1" value={ref} onChange={(event) => setRef(event.target.value)}
            pattern="[A-Za-z0-9_\-]+" maxLength={64} placeholder="Ví dụ demo-01" />
        </label>
        <label className="text-sm font-medium">Chế độ
          <select className="field mt-1" value={mode} onChange={(event) => setMode(event.target.value as Mode)}>
            <option value="assisted">Có hỗ trợ AI</option><option value="silent">Chế độ ngầm</option>
          </select>
        </label>
      </div>
      {notice(create.error)}
      <div className="mt-4 flex flex-wrap gap-2">
        <button className="button primary" disabled={create.isPending}>Tạo buổi khám</button>
        <button className="button" type="button" onClick={() => setCreating(false)}>Huỷ</button>
      </div>
    </form>}
    <div className="panel divide-y divide-line" aria-label="Danh sách buổi khám">
      {notice(cases.error)}
      {cases.isPending && <p className="p-5 text-muted" role="status">Đang tải buổi khám…</p>}
      {cases.data?.length === 0 && <p className="p-5 text-muted">Chưa có buổi khám. Tạo một ca để tải audio.</p>}
      {cases.data?.map((item) => <article key={item.id} className="flex flex-wrap items-center justify-between gap-3 p-4 sm:p-5">
        <div><h2 className="break-words">{caseName(item)}</h2><p className="mt-1 text-sm text-muted">{modeLabel[item.mode]}</p></div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="status">{statusLabel[item.status] ?? item.status}</span>
          <button className="button" type="button" onClick={() => onOpen(item.id)} aria-label={`Mở ${caseName(item)}`}>
            Mở buổi khám
          </button>
        </div>
      </article>)}
    </div>
  </>;
}

function JobProgress({ job, onRetry, busy }: { job: Job; onRetry: () => void; busy: boolean }) {
  if (job.status === 'succeeded') return null;
  const details = job.errorCode && message(job.errorCode);
  if (job.status === 'failed') {
    return <div className="notice" role="alert">
      <p className="font-medium">Chép lời chưa hoàn tất</p><p className="mt-1">{details}</p>
      {job.retryable && <button className="button mt-3" onClick={onRetry} disabled={busy} type="button">Thử lại</button>}
    </div>;
  }
  if (job.status === 'superseded') return null;
  return <div className="mt-4 border-t border-line pt-4" role="status">
    <p className="text-sm font-medium">
      {job.status === 'running' ? 'Đang chép lời' : details ? 'Đang chờ tự động thử lại' : 'Đang chờ worker'}
    </p>
    {details && <p className="mt-1 text-sm text-muted">{details}</p>}
    {job.partsTotal && <>
      <progress className="mt-2 block w-full accent-brand" aria-label="Số phần đã xử lý"
        value={job.partsDone} max={job.partsTotal} />
      <p className="mt-1 text-sm text-muted">{job.partsDone}/{job.partsTotal} phần đã xử lý</p>
    </>}
  </div>;
}

function EncounterDetail({ token, id, onBack }: { token: string; id: string; onBack: () => void }) {
  const cache = useQueryClient();
  const path = `/encounters/${id}`;
  const key = ['encounter', token, id];
  const [file, setFile] = useState<File | null>(null);
  const detail = useQuery({
    queryKey: key, queryFn: () => api<Detail>(token, path),
    refetchInterval: ({ state }) => ['queued', 'running'].includes(state.data?.job?.status ?? '') ? 1500 : false,
  });
  const action = useMutation({
    mutationFn: async (operation: 'upload' | 'start' | 'retry') => {
      const encounter = detail.data;
      if (operation === 'retry' && encounter?.job) {
        return api<Job>(token, `${path}/jobs/${encounter.job.jobId}/retry`, { method: 'POST' });
      }
      const audio = operation === 'upload' && file ? await upload(token, path, file) : encounter?.audio;
      if (!audio) throw new Error('Chọn một tệp âm thanh.');
      return api<Job>(token, `${path}/transcribe`, json({ audioId: audio.audioId }));
    },
    onSuccess: () => setFile(null),
    onSettled: () => Promise.all([
      cache.invalidateQueries({ queryKey: key }), cache.invalidateQueries({ queryKey: ['encounters', token] }),
    ]),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (file) action.mutate('upload');
  }
  const item = detail.data;
  return <>
    <button className="back mb-3" type="button" onClick={onBack}><ArrowLeft size={16} aria-hidden /> Buổi khám</button>
    {notice(detail.error)}
    {detail.isPending && <p role="status" className="text-muted">Đang tải buổi khám…</p>}
    {item && <>
      <div className="page-heading">
        <div><h1 className="break-words">{caseName(item)}</h1><p>Khám ngoại trú · {modeLabel[item.mode]}</p></div>
        <span className="status">{item.job?.status === 'failed' ? 'Chép lời gặp lỗi' : statusLabel[item.status] ?? item.status}</span>
      </div>
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.5fr)]">
        <section className="panel min-w-0" aria-labelledby="audio-title">
          <div className="panel-heading"><FileAudio size={18} aria-hidden /><h2 id="audio-title">Tệp âm thanh</h2></div>
          <div className="space-y-4 p-4 sm:p-5">
            <form onSubmit={submit}>
              <label htmlFor="audio" className="block text-sm font-medium">Chọn bản ghi mô phỏng</label>
              <p id="audio-help" className="mt-1 text-sm text-muted">WAV, MP3, M4A, MP4 hoặc WebM.</p>
              <div className="relative mt-3 flex min-h-16 flex-wrap items-center gap-2 rounded-xl border border-dashed border-line bg-canvas px-3 py-3 group focus-within:outline-2 focus-within:outline-brand">
                <Upload size={17} aria-hidden className="shrink-0 text-brand" />
                <span className="min-w-0 break-all text-sm font-medium">{file ? file.name : 'Chọn tệp âm thanh'}</span>
                <input id="audio" type="file" accept=".wav,.mp3,.m4a,.mp4,.webm" className="absolute inset-0 cursor-pointer opacity-0"
                  aria-describedby="audio-help" disabled={action.isPending}
                  onChange={(event) => { setFile(event.target.files?.[0] ?? null); action.reset(); }}
                  ref={(input) => { if (input && !file) input.value = ''; }} />
              </div>
              <button className="button primary mt-3" disabled={!file || action.isPending}>
                {action.isPending && action.variables === 'upload' ? 'Đang tải lên…' : 'Tải lên và chép lời'}
              </button>
            </form>
            {notice(action.error)}
            {item.audio && <div className="border-t border-line pt-3 text-sm text-muted">
              <p className="font-medium text-ink">Đã lưu tệp {item.audio.format.toUpperCase()}</p>
              <p className="mt-1">{clock(item.audio.durationMs)} · {(item.audio.sizeBytes / 1024 / 1024).toFixed(1)} MiB</p>
              {!item.job && <button className="button mt-3" type="button" onClick={() => action.mutate('start')} disabled={action.isPending}>Bắt đầu chép lời</button>}
            </div>}
            {item.job && <JobProgress job={item.job} onRetry={() => action.mutate('retry')} busy={action.isPending} />}
          </div>
        </section>
        <section className="panel min-w-0" aria-labelledby="transcript-title">
          <div className="panel-heading"><AudioLines size={19} aria-hidden /><h2 id="transcript-title">Transcript</h2>
            {item.transcript && <span className="status ml-auto"><Check size={14} aria-hidden /> Bản cuối</span>}
          </div>
          {item.transcript ? <ol className="divide-y divide-line px-4 sm:px-5">
            {item.transcript.segments.filter((segment) => segment.text).map((segment) => <li className="py-4" key={segment.id}>
              <p className="text-xs font-semibold tabular-nums text-brand">{clock(segment.startMs)}–{clock(segment.endMs)}</p>
              <p className="mt-2 whitespace-pre-wrap wrap-break-word text-sm leading-7 sm:text-base">{segment.text}</p>
            </li>)}
          </ol> : <p className="px-5 py-8 text-sm text-muted">Transcript cuối sẽ xuất hiện sau khi chép lời hoàn tất.</p>}
        </section>
      </div>
    </>}
  </>;
}

export function App() {
  const cache = useQueryClient();
  const [token, setToken] = useState(() => sessionStorage.getItem(sessionKey) ?? '');
  const [id, setId] = useState<string | null>(null);
  function access(value: string) {
    if (value) sessionStorage.setItem(sessionKey, value);
    else sessionStorage.removeItem(sessionKey);
    cache.clear();
    setId(null);
    setToken(value);
  }

  return <>
    <header className="border-b border-line bg-paper">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-7">
        <div className="flex items-center gap-2.5 font-semibold tracking-tight">
          <span className="grid size-9 place-items-center rounded-lg bg-brand text-brand-ink"><AudioLines size={20} aria-hidden /></span>
          ViScribe ICD
        </div>
        <div className="flex flex-wrap items-center gap-3"><span className="text-xs text-muted sm:text-sm">Dữ liệu mô phỏng</span>
          {token && <button type="button" className="back text-xs" onClick={() => access('')}>Đổi mã truy cập</button>}
        </div>
      </div>
    </header>
    <main className="mx-auto max-w-6xl px-4 py-6 sm:px-7 sm:py-8">
      {!token ? <Login onLogin={access} /> : id ? <EncounterDetail key={id} token={token} id={id} onBack={() => setId(null)} /> : <EncounterList token={token} onOpen={setId} />}
    </main>
  </>;
}
