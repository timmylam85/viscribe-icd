from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIMENSIONS = 1536


class Base(DeclarativeBase):
    pass


class CatalogRelease(Base):
    __tablename__ = "icd_catalog_releases"

    version: Mapped[str] = mapped_column(Text, primary_key=True)
    source_url: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ICDCode(Base):
    __tablename__ = "icd_codes"
    __table_args__ = (
        CheckConstraint("age_min IS NULL OR age_min >= 0", name="ck_icd_age_min"),
        CheckConstraint("age_max IS NULL OR age_max >= 0", name="ck_icd_age_max"),
        CheckConstraint("age_min <= age_max", name="ck_icd_age_range"),
        CheckConstraint("sex_restriction IN ('female', 'male')", name="ck_icd_sex_restriction"),
        CheckConstraint("active OR NOT selectable", name="ck_icd_selectable_active"),
    )

    catalog_version: Mapped[str] = mapped_column(
        ForeignKey("icd_catalog_releases.version"), primary_key=True
    )
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    title_vi: Mapped[str] = mapped_column(Text)
    title_en: Mapped[str | None] = mapped_column(Text)
    chapter: Mapped[str | None] = mapped_column(Text)
    block: Mapped[str | None] = mapped_column(Text)
    includes: Mapped[str | None] = mapped_column(Text)
    synonyms: Mapped[list[str]] = mapped_column(ARRAY(Text))
    excludes_codes: Mapped[list[str]] = mapped_column(ARRAY(Text))
    sex_restriction: Mapped[str | None] = mapped_column(Text)
    age_min: Mapped[int | None] = mapped_column(Integer)
    age_max: Mapped[int | None] = mapped_column(Integer)
    rule_source: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean)
    selectable: Mapped[bool] = mapped_column(Boolean)
    in_focus_set: Mapped[bool] = mapped_column(Boolean)
    search_title: Mapped[str] = mapped_column(Text)
    search_document: Mapped[str] = mapped_column(Text)


class ICDCodeEmbedding(Base):
    __tablename__ = "icd_code_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["catalog_version", "code"], ["icd_codes.catalog_version", "icd_codes.code"]
        ),
    )

    catalog_version: Mapped[str] = mapped_column(Text, primary_key=True)
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    deployment: Mapped[str] = mapped_column(Text, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))


class Encounter(Base):
    __tablename__ = "encounters"
    __table_args__ = (
        CheckConstraint("mode IN ('silent', 'assisted')", name="ck_encounter_mode"),
        CheckConstraint("revision >= 1", name="ck_encounter_revision"),
        Index("ix_encounters_doctor_created", "doctor_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    doctor_id: Mapped[str] = mapped_column(Text)
    patient_ref: Mapped[str | None] = mapped_column(Text)
    catalog_version: Mapped[str] = mapped_column(ForeignKey("icd_catalog_releases.version"))
    mode: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="created")
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIRun(Base):
    __tablename__ = "ai_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    encounter_id: Mapped[UUID | None] = mapped_column(ForeignKey("encounters.id"))
    operation: Mapped[str] = mapped_column(Text)
    deployment: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AudioFile(Base):
    __tablename__ = "audio_files"
    __table_args__ = (UniqueConstraint("encounter_id", "revision", name="uq_audio_revision"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    encounter_id: Mapped[UUID] = mapped_column(ForeignKey("encounters.id"))
    revision: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)


class TranscriptionJob(Base):
    __tablename__ = "transcription_jobs"
    __table_args__ = (
        Index("ix_transcription_jobs_due", "status", "available_at", "id"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'superseded')",
            name="ck_transcription_job_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    audio_id: Mapped[UUID] = mapped_column(ForeignKey("audio_files.id"), unique=True)
    status: Mapped[str] = mapped_column(Text, default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chunk_seconds: Mapped[int] = mapped_column(Integer)
    parts: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    parts_done: Mapped[int] = mapped_column(Integer, default=0)
    parts_total: Mapped[int | None] = mapped_column(Integer)


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    audio_id: Mapped[UUID] = mapped_column(ForeignKey("audio_files.id"), unique=True)
    kind: Mapped[str] = mapped_column(Text, default="final")
    text: Mapped[str] = mapped_column(Text)
    segments: Mapped[list[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class STTRateLimit(Base):
    __tablename__ = "stt_rate_limits"

    deployment: Mapped[str] = mapped_column(Text, primary_key=True)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
