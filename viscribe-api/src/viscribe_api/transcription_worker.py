import argparse
import logging
import random
import time
import wave
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from viscribe_api.audio import AudioError, chunk_count, normalize_audio, read_chunks
from viscribe_api.azure_clients import AzureCallError, AzureGateway
from viscribe_api.config import get_settings
from viscribe_api.db import make_session_factory
from viscribe_api.models import AudioFile, Encounter, STTRateLimit, Transcript, TranscriptionJob
from viscribe_api.transcription import next_retry

LOGGER = logging.getLogger(__name__)
LEASE_SECONDS = 120
MAX_FAILURES = 3


class LeaseLost(Exception):
    pass


@dataclass(frozen=True)
class Claim:
    job_id: UUID
    audio_id: UUID
    encounter_id: UUID
    revision: int
    token: UUID
    attempt: int
    failures: int
    chunk_seconds: int
    parts_done: int


def claim_job(sessions: sessionmaker[Session]) -> Claim | None:
    with sessions.begin() as session:
        now = session.scalar(select(func.now()))
        job = session.scalar(
            select(TranscriptionJob)
            .where(
                or_(
                    and_(TranscriptionJob.status == "queued", TranscriptionJob.available_at <= now),
                    and_(TranscriptionJob.status == "running", TranscriptionJob.lease_until <= now),
                )
            )
            .order_by(TranscriptionJob.available_at, TranscriptionJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        audio = session.get(AudioFile, job.audio_id)
        if job.status == "running":
            job.failures += 1
        job.status = "running"
        job.attempt += 1
        job.lease_token = uuid4()
        job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        return Claim(
            job.id,
            audio.id,
            audio.encounter_id,
            audio.revision,
            job.lease_token,
            job.attempt,
            job.failures,
            job.chunk_seconds,
            job.parts_done,
        )


def leased_job(session: Session, claim: Claim) -> TranscriptionJob:
    job = session.scalar(
        select(TranscriptionJob)
        .where(
            TranscriptionJob.id == claim.job_id,
            TranscriptionJob.status == "running",
            TranscriptionJob.lease_token == claim.token,
            TranscriptionJob.lease_until > func.now(),
        )
        .with_for_update()
    )
    if job is None:
        raise LeaseLost
    return job


def reserve_stt_slot(sessions: sessionmaker[Session], deployment: str, interval: float) -> float:
    if interval <= 0:
        return 0
    spacing = timedelta(seconds=interval)
    with sessions.begin() as session:
        next_at, now = session.execute(
            insert(STTRateLimit)
            .values(deployment=deployment, next_at=func.now() + spacing)
            .on_conflict_do_update(
                index_elements=[STTRateLimit.deployment],
                set_={"next_at": func.greatest(STTRateLimit.next_at, func.now()) + spacing},
            )
            .returning(STTRateLimit.next_at, func.now())
        ).one()
    return max(0, (next_at - now - spacing).total_seconds())


def finish(
    sessions: sessionmaker[Session], claim: Claim, error: AudioError | AzureCallError | None
) -> None:
    with sessions.begin() as session:
        # Upload and API retry acquire the encounter before the job as well.
        encounter = session.get(Encounter, claim.encounter_id, with_for_update=True)
        job = leased_job(session, claim)
        if encounter is None or encounter.revision != claim.revision:
            raise LeaseLost
        now = session.scalar(select(func.now()))
        job.lease_token = None
        job.lease_until = None
        if error is None:
            text = "\n\n".join(part["text"] for part in job.parts if part["text"])
            if text:
                session.add(Transcript(audio_id=job.audio_id, text=text, segments=job.parts))
                job.parts = []
                job.status = "succeeded"
                job.error_code = None
                job.retryable = False
                encounter.status = "transcript_ready"
                return
            error = AudioError("no_speech_detected")
        job.error_code = error.code
        job.failures += 1
        transient = isinstance(error, AzureCallError) and error.transient
        if transient:
            job.available_at = next_retry(now, job.failures, error.retry_after, random.random())
        else:
            job.available_at = now
        job.retryable = transient or error.code in {
            "file_stt_resource_not_configured",
            "media_tool_unavailable",
            "http_401",
            "http_403",
            "worker_timeout",
        }
        if transient and job.failures < MAX_FAILURES:
            job.status = "queued"
            return
        job.status = "failed"
        encounter.status = "audio_ready"


def process_next(sessions: sessionmaker[Session], azure: AzureGateway) -> bool:
    claim = claim_job(sessions)
    if claim is None:
        return False
    try:
        try:
            if claim.failures >= MAX_FAILURES:
                raise AudioError("worker_timeout")
            if not azure.file_stt_configured:
                raise AzureCallError("file_stt_resource_not_configured")
            with sessions() as session:
                audio = session.get(AudioFile, claim.audio_id)
                content = audio.content
                extension = audio.format
            with TemporaryDirectory(prefix="viscribe-stt-") as work:
                source = Path(work) / "source.bin"
                target = Path(work) / "normalized.wav"
                source.write_bytes(content)
                normalize_audio(source, target, extension, azure.settings.audio_max_seconds)
                with wave.open(str(target), "rb") as decoded:
                    with sessions.begin() as session:
                        job = leased_job(session, claim)
                        job.parts_total = chunk_count(decoded, claim.chunk_seconds)
                    for index, chunk in enumerate(
                        read_chunks(decoded, claim.chunk_seconds, claim.parts_done),
                        start=claim.parts_done,
                    ):
                        delay = reserve_stt_slot(
                            sessions,
                            azure.settings.azure_stt_transcribe,
                            azure.settings.stt_min_interval_seconds,
                        )
                        with sessions.begin() as session:
                            job = leased_job(session, claim)
                            now = session.scalar(select(func.now()))
                            job.lease_until = now + timedelta(seconds=delay + LEASE_SECONDS)
                        if delay:
                            time.sleep(delay)
                        with sessions.begin() as session:
                            leased_job(session, claim)
                        text = azure.transcribe_file(
                            chunk.wav, encounter_id=claim.encounter_id, attempt=claim.attempt
                        )
                        with sessions.begin() as session:
                            job = leased_job(session, claim)
                            job.parts = [
                                *job.parts,
                                {
                                    "id": f"chunk-{index + 1:03}",
                                    "startMs": chunk.start_ms,
                                    "endMs": chunk.end_ms,
                                    "text": text,
                                },
                            ]
                            job.parts_done = index + 1
        except (AudioError, AzureCallError) as error:
            finish(sessions, claim, error)
        except (OSError, wave.Error):
            finish(sessions, claim, AudioError("invalid_audio"))
        else:
            finish(sessions, claim, None)
    except LeaseLost:
        pass
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local file transcription worker")
    parser.add_argument("--once", action="store_true", help="Process at most one ready job")
    args = parser.parse_args()
    settings = get_settings()
    sessions = make_session_factory(settings)
    azure = AzureGateway(settings, sessions)
    if not azure.file_stt_configured:
        parser.error(
            "Configure the Azure STT endpoint, key and API version before running the worker"
        )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    LOGGER.info("File transcription worker ready")
    try:
        while True:
            try:
                worked = process_next(sessions, azure)
            except SQLAlchemyError:
                LOGGER.error(
                    "Transcription database unavailable. Check the connection and migrations."
                )
                if args.once:
                    raise SystemExit(1) from None
                worked = False
            if args.once:
                return
            if not worked:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        azure.close()
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
