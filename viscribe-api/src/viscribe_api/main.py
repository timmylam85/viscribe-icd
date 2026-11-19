from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from secrets import compare_digest
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from viscribe_api.audio import Upload
from viscribe_api.audio_upload import MULTIPART_SCHEMA, parse_audio
from viscribe_api.azure_clients import AzureGateway
from viscribe_api.catalog_search import search_codes
from viscribe_api.config import Settings, get_settings
from viscribe_api.db import make_session_factory
from viscribe_api.models import AudioFile, CatalogRelease, Encounter, TranscriptionJob
from viscribe_api.transcription import (
    WorkflowError,
    current_audio,
    enqueue,
    get_job,
    job_for_audio,
    retry_job,
    store_audio,
    transcript_for_audio,
)


class APISchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, validate_by_name=True)


class EncounterInput(APISchema):
    mode: Literal["silent", "assisted"]
    patient_ref: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )


class EncounterView(APISchema):
    id: UUID
    mode: Literal["silent", "assisted"]
    patient_ref: str | None
    catalog_version: str
    status: str
    revision: int
    created_at: datetime


class ICDCodeView(APISchema):
    code: str
    title_vi: str
    title_en: str | None


class SearchView(APISchema):
    catalog_version: str
    items: list[ICDCodeView]


class AudioView(APISchema):
    audio_id: UUID = Field(validation_alias="id")
    format: str
    duration_ms: int
    size_bytes: int


class JobView(APISchema):
    job_id: UUID = Field(validation_alias="id")
    audio_id: UUID
    status: str
    attempt: int
    error_code: str | None
    retryable: bool
    available_at: datetime
    parts_done: int
    parts_total: int | None


class TranscribeInput(APISchema):
    audio_id: UUID


class TranscriptSegment(APISchema):
    id: str
    start_ms: int
    end_ms: int
    text: str


class TranscriptView(APISchema):
    id: UUID
    kind: Literal["final"]
    text: str
    segments: list[TranscriptSegment]
    created_at: datetime


class EncounterDetail(EncounterView):
    audio: AudioView | None = None
    job: JobView | None = None
    transcript: TranscriptView | None = None


def db_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session


SessionDep = Annotated[Session, Depends(db_session)]
bearer = HTTPBearer(auto_error=False)


def current_doctor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is not None:
        token = credentials.credentials.encode()
        for doctor_id, secret in request.app.state.settings.dev_users.items():
            if compare_digest(token, secret.get_secret_value().encode()):
                return doctor_id
    raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})


DoctorDep = Annotated[str, Depends(current_doctor)]


def owned_encounter(
    session: Session,
    encounter_id: UUID,
    doctor_id: str,
    *,
    lock: bool = False,
) -> Encounter:
    statement = select(Encounter).where(
        Encounter.id == encounter_id, Encounter.doctor_id == doctor_id
    )
    encounter = session.scalar(statement.with_for_update() if lock else statement)
    if encounter is None:
        raise HTTPException(404, "Encounter not found")
    return encounter


def check_upload_owner(encounter_id: UUID, session: SessionDep, doctor_id: DoctorDep) -> None:
    owned_encounter(session, encounter_id, doctor_id)
    session.rollback()


async def authorized_upload(
    request: Request,
    _: Annotated[None, Depends(check_upload_owner)],
) -> Upload:
    return await parse_audio(request)


def active_catalog(request: Request, session: Session) -> str:
    version = request.app.state.settings.icd_catalog_version
    if session.get(CatalogRelease, version) is None:
        raise HTTPException(503, "ICD-10 catalog has not been loaded")
    return version


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configured = settings or get_settings()
        sessions = make_session_factory(configured)
        azure = AzureGateway(configured, sessions)
        app.state.settings = configured
        app.state.sessions = sessions
        app.state.azure = azure
        try:
            yield
        finally:
            azure.close()
            sessions.kw["bind"].dispose()

    app = FastAPI(title="ViScribe-ICD API", version="0.2.0", lifespan=lifespan)

    @app.exception_handler(WorkflowError)
    async def workflow_error(_: Request, error: WorkflowError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content={"detail": error.code})

    @app.get("/health")
    def health(session: SessionDep) -> dict[str, str]:
        try:
            session.scalar(select(1))
        except SQLAlchemyError:
            raise HTTPException(503, "Database unavailable") from None
        return {"status": "ok"}

    @app.post("/encounters", status_code=201, response_model=EncounterView)
    def create_encounter(
        payload: EncounterInput, request: Request, session: SessionDep, doctor_id: DoctorDep
    ) -> Encounter:
        encounter = Encounter(
            doctor_id=doctor_id,
            catalog_version=active_catalog(request, session),
            **payload.model_dump(),
        )
        session.add(encounter)
        session.commit()
        return encounter

    @app.get("/encounters", response_model=list[EncounterView])
    def list_encounters(
        session: SessionDep,
        doctor_id: DoctorDep,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[Encounter]:
        return list(
            session.scalars(
                select(Encounter)
                .where(Encounter.doctor_id == doctor_id)
                .order_by(Encounter.created_at.desc(), Encounter.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )

    @app.get(
        "/encounters/{encounter_id}",
        response_model=EncounterDetail,
        response_model_exclude_unset=True,
    )
    def get_encounter(
        encounter_id: UUID, session: SessionDep, doctor_id: DoctorDep, response: Response
    ) -> EncounterDetail:
        encounter = owned_encounter(session, encounter_id, doctor_id)
        response.headers["Cache-Control"] = "no-store"
        detail = EncounterView.model_validate(encounter).model_dump()
        if audio := current_audio(session, encounter):
            return EncounterDetail(
                **detail,
                audio=AudioView.model_validate(audio),
                job=job_for_audio(session, audio.id),
                transcript=transcript_for_audio(session, audio.id),
            )
        return EncounterDetail(**detail)

    @app.post(
        "/encounters/{encounter_id}/audio",
        status_code=201,
        response_model=AudioView,
        openapi_extra=MULTIPART_SCHEMA,
    )
    def upload_audio(
        encounter_id: UUID,
        session: SessionDep,
        doctor_id: DoctorDep,
        upload: Annotated[Upload, Depends(authorized_upload)],
    ) -> AudioFile:
        encounter = owned_encounter(session, encounter_id, doctor_id, lock=True)
        audio = store_audio(session, encounter, upload)
        session.commit()
        return audio

    @app.post("/encounters/{encounter_id}/transcribe", status_code=202, response_model=JobView)
    def enqueue_transcription(
        encounter_id: UUID,
        payload: TranscribeInput,
        request: Request,
        session: SessionDep,
        doctor_id: DoctorDep,
    ) -> TranscriptionJob:
        encounter = owned_encounter(session, encounter_id, doctor_id, lock=True)
        job = enqueue(
            session, encounter, payload.audio_id, request.app.state.settings.stt_chunk_seconds
        )
        session.commit()
        return job

    @app.get("/encounters/{encounter_id}/jobs/{job_id}", response_model=JobView)
    def get_transcription_job(
        encounter_id: UUID,
        job_id: UUID,
        session: SessionDep,
        doctor_id: DoctorDep,
    ) -> TranscriptionJob:
        return get_job(session, owned_encounter(session, encounter_id, doctor_id), job_id)

    @app.post(
        "/encounters/{encounter_id}/jobs/{job_id}/retry", status_code=202, response_model=JobView
    )
    def retry_transcription(
        encounter_id: UUID,
        job_id: UUID,
        session: SessionDep,
        doctor_id: DoctorDep,
    ) -> TranscriptionJob:
        encounter = owned_encounter(session, encounter_id, doctor_id, lock=True)
        job = retry_job(session, encounter, job_id, session.scalar(select(func.now())))
        session.commit()
        return job

    @app.get("/icd/search", response_model=SearchView)
    def icd_search(
        request: Request,
        session: SessionDep,
        doctor_id: DoctorDep,
        q: Annotated[str, Query(min_length=1, max_length=150)],
        limit: Annotated[int, Query(ge=1, le=20)] = 5,
        encounter_id: UUID | None = None,
    ) -> SearchView:
        version = (
            owned_encounter(session, encounter_id, doctor_id).catalog_version
            if encounter_id is not None
            else active_catalog(request, session)
        )
        matches = search_codes(session, request.app.state.azure, version, q.strip(), limit)
        return SearchView(
            catalog_version=version, items=[ICDCodeView.model_validate(code) for code in matches]
        )

    return app


app = create_app()
