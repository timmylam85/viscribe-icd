from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from viscribe_api.audio import Upload
from viscribe_api.models import AudioFile, Encounter, Transcript, TranscriptionJob

REPLACEABLE = {"created", "audio_ready", "transcribing", "transcript_ready"}


class WorkflowError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


def current_audio(session: Session, encounter: Encounter) -> AudioFile | None:
    return session.scalar(
        select(AudioFile).where(
            AudioFile.encounter_id == encounter.id,
            AudioFile.revision == encounter.revision,
        )
    )


def job_for_audio(
    session: Session, audio_id: UUID, *, lock: bool = False
) -> TranscriptionJob | None:
    statement = select(TranscriptionJob).where(TranscriptionJob.audio_id == audio_id)
    return session.scalar(statement.with_for_update() if lock else statement)


def transcript_for_audio(session: Session, audio_id: UUID) -> Transcript | None:
    return session.scalar(select(Transcript).where(Transcript.audio_id == audio_id))


def store_audio(session: Session, encounter: Encounter, upload: Upload) -> AudioFile:
    previous = current_audio(session, encounter)
    if previous and previous.sha256 == upload.sha256:
        return previous
    if encounter.status not in REPLACEABLE:
        raise WorkflowError("encounter_already_under_review")
    if previous and (old_job := job_for_audio(session, previous.id, lock=True)):
        if old_job.status != "succeeded":
            old_job.status = "superseded"
            old_job.retryable = False
            old_job.lease_token = None
            old_job.lease_until = None
            old_job.parts = []
    encounter.revision += 1
    encounter.status = "audio_ready"
    audio = AudioFile(
        encounter_id=encounter.id,
        revision=encounter.revision,
        format=upload.format,
        duration_ms=upload.duration_ms,
        size_bytes=len(upload.content),
        sha256=upload.sha256,
        content=upload.content,
    )
    session.add(audio)
    session.flush()
    return audio


def enqueue(
    session: Session, encounter: Encounter, audio_id: UUID, chunk_seconds: int
) -> TranscriptionJob:
    audio = session.get(AudioFile, audio_id)
    if audio is None or audio.encounter_id != encounter.id:
        raise WorkflowError("audio_not_found", 404)
    if audio.revision != encounter.revision:
        raise WorkflowError("audio_version_changed")
    job = job_for_audio(session, audio.id, lock=True)
    if job is not None:
        return job
    if encounter.status not in REPLACEABLE:
        raise WorkflowError("encounter_already_under_review")
    job = TranscriptionJob(audio_id=audio.id, chunk_seconds=chunk_seconds)
    session.add(job)
    encounter.status = "transcribing"
    session.flush()
    return job


def get_job(
    session: Session, encounter: Encounter, job_id: UUID, *, lock: bool = False
) -> TranscriptionJob:
    statement = (
        select(TranscriptionJob)
        .join(AudioFile)
        .where(
            TranscriptionJob.id == job_id,
            AudioFile.encounter_id == encounter.id,
        )
    )
    if lock:
        statement = statement.with_for_update(of=TranscriptionJob)
    job = session.scalar(statement)
    if job is None:
        raise WorkflowError("job_not_found", 404)
    return job


def retry_job(
    session: Session, encounter: Encounter, job_id: UUID, now: datetime
) -> TranscriptionJob:
    job = get_job(session, encounter, job_id, lock=True)
    audio = session.get(AudioFile, job.audio_id)
    if audio is None or audio.revision != encounter.revision:
        raise WorkflowError("audio_version_changed")
    if job.status in {"queued", "running", "succeeded"}:
        return job
    if job.status != "failed" or not job.retryable:
        raise WorkflowError("job_not_retryable")
    job.status = "queued"
    job.failures = 0
    job.error_code = None
    job.retryable = False
    job.available_at = max(now, job.available_at)
    encounter.status = "transcribing"
    session.flush()
    return job


def next_retry(now: datetime, failures: int, upstream: float, jitter: float) -> datetime:
    return now + timedelta(seconds=max(2 ** min(failures, 5) + jitter, min(upstream, 86400)))
