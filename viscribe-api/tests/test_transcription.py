import io
import wave
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from viscribe_api.azure_clients import AzureCallError, Result
from viscribe_api.models import AIRun, AudioFile, Transcript, TranscriptionJob
from viscribe_api.transcription_worker import (
    LeaseLost,
    claim_job,
    finish,
    process_next,
    reserve_stt_slot,
)

ALICE = {"Authorization": f"Bearer {'a' * 32}"}
BOB = {"Authorization": f"Bearer {'b' * 32}"}


def recording(seconds: int) -> bytes:
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as file:
            file.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            file.writeframes(b"\0\0" * seconds * 16000)
        return buffer.getvalue()


def encounter_path(client, mode="assisted"):
    result = client.post("/encounters", headers=ALICE, json={"mode": mode})
    assert result.status_code == 201
    return f"/encounters/{result.json()['id']}"


def upload(client, path, seconds=1):
    result = client.post(
        f"{path}/audio", headers=ALICE, files={"file": ("file.wav", recording(seconds))}
    )
    assert result.status_code == 201, result.json()
    return result.json()["audioId"]


def enqueue(client, path, audio_id):
    result = client.post(f"{path}/transcribe", headers=ALICE, json={"audioId": audio_id})
    assert result.status_code == 202, result.json()
    return result.json()["jobId"]


def test_upload_validation_and_access_control(test_api, monkeypatch):
    client, sessions, _ = test_api
    path = encounter_path(client)
    audio = {"file": ("untrusted.wav", b"not valid audio")}
    assert client.post(f"{path}/audio", files=audio).status_code == 401
    assert client.post(f"{path}/audio", headers=BOB, files=audio).status_code == 404
    malformed = client.post(f"{path}/audio", headers=ALICE, files=audio)
    assert (malformed.status_code, malformed.json()["detail"]) == (422, "invalid_audio")
    rejected = client.post(f"{path}/audio", headers=ALICE, files={"file": ("file.txt", b"fake")})
    assert rejected.status_code == 415
    monkeypatch.setattr(client.app.state.settings, "audio_max_bytes", 100)
    oversized = client.post(
        f"{path}/audio", headers=ALICE, files={"file": ("file.wav", recording(1))}
    )
    assert oversized.status_code == 413
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(AudioFile)) == 0


def test_file_job_deduplicates_resumes_after_429_and_joins_chunks(test_api, monkeypatch):
    client, sessions, _ = test_api
    path = encounter_path(client, mode="silent")
    azure = client.app.state.azure
    monkeypatch.setattr(azure.settings, "stt_chunk_seconds", 10)
    monkeypatch.setattr(azure.settings, "stt_min_interval_seconds", 0)
    audio_id = upload(client, path, 21)
    assert upload(client, path, 21) == audio_id
    job_id = enqueue(client, path, audio_id)
    assert enqueue(client, path, audio_id) == job_id
    job_path = f"{path}/jobs/{job_id}"
    assert client.get(job_path, headers=BOB).status_code == 404
    assert client.post(f"{job_path}/retry", headers=BOB).status_code == 404
    assert client.get(path, headers=ALICE).json()["transcript"] is None
    calls = []

    def simulated(payload, *, encounter_id, attempt):
        with wave.open(io.BytesIO(payload), "rb") as file:
            calls.append(file.getnframes())

        def respond():
            if len(calls) == 2:
                raise AzureCallError("http_429", transient=True, retry_after=15)
            return Result(f"Đoạn {len(calls)}")

        return azure.invoke(
            "transcribe_file",
            "test-stt",
            respond,
            attempts=1,
            encounter_id=encounter_id,
            attempt_start=attempt,
        )

    with monkeypatch.context() as patch:
        patch.setattr(azure, "stt", object())
        patch.setattr(azure, "transcribe_file", simulated)
        assert process_next(sessions, azure)
        queued = client.get(job_path, headers=ALICE).json()
        assert queued["status"] == "queued"
        assert queued["errorCode"] == "http_429"
        assert queued["partsDone"] == 1 and queued["partsTotal"] == 3
        assert datetime.fromisoformat(queued["availableAt"]) > datetime.now(UTC) + timedelta(
            seconds=10
        )
        assert not process_next(sessions, azure)
        with sessions.begin() as session:
            session.get(TranscriptionJob, UUID(job_id)).available_at = datetime.now(UTC)
        assert process_next(sessions, azure)
        assert not process_next(sessions, azure)
    assert calls == [160000, 160000, 160000, 16000]
    response = client.get(path, headers=ALICE)
    assert response.headers["cache-control"] == "no-store"
    detail = response.json()
    assert detail["job"]["status"] == "succeeded"
    assert detail["status"] == "transcript_ready"
    assert detail["transcript"]["kind"] == "final"
    assert detail["transcript"]["text"] == "Đoạn 1\n\nĐoạn 3\n\nĐoạn 4"
    assert [part["endMs"] for part in detail["transcript"]["segments"]] == [10000, 20000, 21000]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Transcript)) == 1
        assert session.scalar(select(func.count()).select_from(AudioFile)) == 1
        assert session.scalar(select(func.count()).select_from(TranscriptionJob)) == 1
        audits = session.scalars(select(AIRun)).all()
        assert len(audits) == 4 and len({run.encounter_id for run in audits}) == 1
        assert sum(run.error_code == "http_429" for run in audits) == 1


def test_expired_and_replaced_jobs_cannot_publish_stale_results(test_api):
    client, sessions, _ = test_api
    path = encounter_path(client)
    first_audio = upload(client, path)
    first_job = enqueue(client, path, first_audio)
    abandoned = claim_job(sessions)
    assert abandoned is not None
    with sessions.begin() as session:
        session.get(TranscriptionJob, UUID(first_job)).lease_until = datetime.now(UTC) - timedelta(
            seconds=1
        )
    replacement = claim_job(sessions)
    assert replacement is not None and replacement.token != abandoned.token
    with pytest.raises(LeaseLost):
        finish(sessions, abandoned, None)
    second_audio = upload(client, path, 2)
    assert first_audio != second_audio
    with pytest.raises(LeaseLost):
        finish(sessions, replacement, None)
    repeated = client.post(f"{path}/transcribe", headers=ALICE, json={"audioId": first_audio})
    assert repeated.status_code == 409
    assert client.get(f"{path}/jobs/{first_job}", headers=ALICE).json()["status"] == "superseded"
    assert client.get(path, headers=ALICE).json()["transcript"] is None


def test_failed_job_retries_using_same_id(test_api):
    client, sessions, _ = test_api
    path = encounter_path(client)
    job_id = enqueue(client, path, upload(client, path))
    azure = client.app.state.azure
    assert process_next(sessions, azure)
    job_path = f"{path}/jobs/{job_id}"
    assert (
        client.get(job_path, headers=ALICE).json()["errorCode"]
        == "file_stt_resource_not_configured"
    )
    for _ in range(2):
        response = client.post(f"{job_path}/retry", headers=ALICE)
        assert response.status_code == 202
        assert response.json()["jobId"] == job_id and response.json()["status"] == "queued"


def test_db_reserves_spaced_slots_for_same_deployment(test_api):
    _, sessions, _ = test_api
    assert reserve_stt_slot(sessions, "test-stt", 6.2) == 0
    assert 5 < reserve_stt_slot(sessions, "test-stt", 6.2) <= 6.2
    assert reserve_stt_slot(sessions, "other-stt", 6.2) == 0
