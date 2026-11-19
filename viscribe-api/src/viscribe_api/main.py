from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from secrets import compare_digest
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from viscribe_api.azure_clients import AzureGateway
from viscribe_api.catalog_search import search_codes
from viscribe_api.config import Settings, get_settings
from viscribe_api.db import make_session_factory
from viscribe_api.models import CatalogRelease, Encounter


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


def owned_encounter(session: Session, encounter_id: UUID, doctor_id: str) -> Encounter:
    encounter = session.scalar(
        select(Encounter).where(Encounter.id == encounter_id, Encounter.doctor_id == doctor_id)
    )
    if encounter is None:
        raise HTTPException(404, "Encounter not found")
    return encounter


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

    app = FastAPI(title="ViScribe-ICD API", version="0.1.0", lifespan=lifespan)

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

    @app.get("/encounters/{encounter_id}", response_model=EncounterView)
    def get_encounter(encounter_id: UUID, session: SessionDep, doctor_id: DoctorDep) -> Encounter:
        return owned_encounter(session, encounter_id, doctor_id)

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
