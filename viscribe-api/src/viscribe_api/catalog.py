import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, HttpUrl, ValidationError, field_validator, model_validator
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from viscribe_api.azure_clients import AzureGateway
from viscribe_api.models import CatalogRelease, ICDCode, ICDCodeEmbedding

CODE_PATTERN = re.compile(r"[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?")
VERSION_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}")


class CatalogError(ValueError):
    pass


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", plain))


class CatalogRow(BaseModel):
    code: str
    title_vi: str
    title_en: str | None = None
    chapter: str | None = None
    block: str | None = None
    includes: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    excludes_codes: list[str] = Field(default_factory=list)
    sex_restriction: Literal["female", "male"] | None = None
    age_min: int | None = Field(default=None, ge=0)
    age_max: int | None = Field(default=None, ge=0)
    rule_source: HttpUrl | None = None
    active: bool = True
    selectable: bool = True
    in_focus_set: bool = False

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        code = value.upper()
        if not CODE_PATTERN.fullmatch(code):
            raise ValueError("Expected a discrete ICD-10 code")
        return code

    @field_validator("synonyms", "excludes_codes", mode="before")
    @classmethod
    def split_values(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split("|") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_rules(self) -> Self:
        if not normalize(self.title_vi):
            raise ValueError("A searchable Vietnamese title is required")
        if self.age_min is not None and self.age_max is not None and self.age_min > self.age_max:
            raise ValueError("age_min must not exceed age_max")
        if not self.active and self.selectable:
            raise ValueError("An inactive code must have selectable=false")
        self.excludes_codes = sorted({self.validate_code(code) for code in self.excludes_codes})
        if self.code in self.excludes_codes:
            raise ValueError("A code must not exclude itself")
        has_rule = (
            self.sex_restriction is not None
            or self.age_min is not None
            or self.age_max is not None
            or bool(self.excludes_codes)
        )
        if has_rule and self.rule_source is None:
            raise ValueError("Structured ICD restrictions require a rule_source URL")
        return self


def read_catalog(path: Path) -> list[CatalogRow]:
    rows: dict[str, CatalogRow] = {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, strict=True)
        fields = reader.fieldnames or []
        if (
            not {"code", "title_vi"}.issubset(fields)
            or set(fields) - CatalogRow.model_fields.keys()
            or len(fields) != len(set(fields))
        ):
            raise CatalogError("CSV must contain code, title_vi and only supported columns")
        for values in reader:
            if None in values or any(value is None for value in values.values()):
                raise CatalogError(f"Line {reader.line_num}: unexpected number of columns")
            try:
                row = CatalogRow.model_validate(
                    {key: value.strip() for key, value in values.items() if value.strip()}
                )
            except ValidationError as error:
                detail = "; ".join(item["msg"] for item in error.errors(include_input=False))
                raise CatalogError(f"Line {reader.line_num}: {detail}") from None
            if row.code in rows:
                raise CatalogError(f"Line {reader.line_num}: duplicate code {row.code}")
            rows[row.code] = row
    if not rows:
        raise CatalogError("The catalog is empty")
    for row in rows.values():
        if missing := set(row.excludes_codes).difference(rows):
            raise CatalogError(
                f"{row.code}: excluded codes absent from release: {', '.join(sorted(missing))}"
            )
    return sorted(rows.values(), key=lambda row: row.code)


def import_release(
    sessions: sessionmaker[Session], rows: list[CatalogRow], version: str, source_url: str
) -> bool:
    canonical_rows = [row.model_dump(mode="json") for row in rows]
    checksum = hashlib.sha256(
        json.dumps(canonical_rows, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    with sessions.begin() as session:
        inserted = session.scalar(
            pg_insert(CatalogRelease)
            .values(version=version, source_url=source_url, checksum=checksum)
            .on_conflict_do_nothing(index_elements=[CatalogRelease.version])
            .returning(CatalogRelease.version)
        )
        if inserted is None:
            release = session.get(CatalogRelease, version)
            if release is None or (release.source_url, release.checksum) != (source_url, checksum):
                raise CatalogError(
                    "This version already has a different source or content. Use a new version."
                )
            return False
        session.execute(
            insert(ICDCode),
            [
                {
                    **data,
                    "catalog_version": version,
                    "search_title": normalize(row.title_vi),
                    "search_document": f" {normalize(embedding_text(row))} ",
                }
                for row, data in zip(rows, canonical_rows, strict=True)
            ],
        )
    return True


def embedding_text(row: CatalogRow) -> str:
    return " | ".join(
        item for item in (row.code, row.title_vi, row.title_en, row.includes, *row.synonyms) if item
    )


def embed_release(
    sessions: sessionmaker[Session], rows: list[CatalogRow], version: str, azure: AzureGateway
) -> int:
    deployment = azure.settings.azure_openai_embedding
    with sessions() as session:
        existing = set(
            session.scalars(
                select(ICDCodeEmbedding.code).where(
                    ICDCodeEmbedding.catalog_version == version,
                    ICDCodeEmbedding.deployment == deployment,
                )
            )
        )
    pending = [row for row in rows if row.active and row.selectable and row.code not in existing]
    embedded = 0
    for start in range(0, len(pending), 32):
        batch = pending[start : start + 32]
        vectors = azure.embed([embedding_text(row) for row in batch], operation="catalog_embedding")
        with sessions.begin() as session:
            stored = session.scalars(
                pg_insert(ICDCodeEmbedding)
                .values(
                    [
                        {
                            "catalog_version": version,
                            "code": row.code,
                            "deployment": deployment,
                            "embedding": vector,
                        }
                        for row, vector in zip(batch, vectors, strict=True)
                    ]
                )
                .on_conflict_do_nothing()
                .returning(ICDCodeEmbedding.code)
            ).all()
            embedded += len(stored)
    return embedded
