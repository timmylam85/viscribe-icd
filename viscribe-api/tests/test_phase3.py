import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from viscribe_api.azure_clients import AzureCallError, Result
from viscribe_api.catalog import CatalogError, import_release, read_catalog
from viscribe_api.config import Settings
from viscribe_api.db import make_session_factory
from viscribe_api.main import create_app
from viscribe_api.models import EMBEDDING_DIMENSIONS, AIRun, Base, ICDCode, ICDCodeEmbedding

CATALOG = Path(__file__).resolve().parents[2] / "viscribe-data/fixtures/icd10-who2019-demo.csv"
VERSION = "demo-test-v1"
SOURCE = "https://icd.who.int/browse10/2019/en"
ALICE = {"Authorization": f"Bearer {'a' * 32}"}
BOB = {"Authorization": f"Bearer {'b' * 32}"}


@pytest.fixture
def phase3():
    database_url = os.environ.get("VISCRIBE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set VISCRIBE_TEST_DATABASE_URL to a local PostgreSQL with pgvector")
    engine = create_engine(database_url)
    schema = f"test_phase3_{uuid4().hex[:12]}"
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
        connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}, public")
        Base.metadata.create_all(connection, checkfirst=False)
    test_url = make_url(database_url).update_query_dict(
        {"options": f"-csearch_path={schema},public"}
    )
    settings = Settings(
        VISCRIBE_DATABASE_URL=test_url.render_as_string(hide_password=False),
        VISCRIBE_ICD_CATALOG_VERSION=VERSION,
        VISCRIBE_DEV_USERS={"doctor-a": "a" * 32, "doctor-b": "b" * 32},
    )
    sessions = make_session_factory(settings)
    try:
        rows = read_catalog(CATALOG)
        assert import_release(sessions, rows, VERSION, SOURCE)
        with TestClient(create_app(settings)) as client:
            yield client, sessions, rows
    finally:
        sessions.kw["bind"].dispose()
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def test_catalog_import_is_idempotent_and_version_is_immutable(phase3):
    _, sessions, rows = phase3
    assert not import_release(sessions, rows, VERSION, SOURCE)
    with pytest.raises(CatalogError, match="already has a different"):
        import_release(sessions, rows[:-1], VERSION, SOURCE)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ICDCode)) == len(rows)


def test_lookup_by_code_and_vietnamese_title(phase3):
    client, _, _ = phase3
    assert client.get("/health").json() == {"status": "ok"}
    for query in ("viêm họng", "viem hong", "J02", "j029"):
        response = client.get("/icd/search", params={"q": query}, headers=ALICE)
        assert response.status_code == 200
        assert response.json()["catalogVersion"] == VERSION
        codes = [item["code"] for item in response.json()["items"]]
        assert "J02.9" in codes
        assert "J02" not in codes
    assert client.get("/icd/search", params={"q": "Z99.9"}, headers=ALICE).json()["items"] == []


def test_only_owner_can_list_or_open_encounter(phase3):
    client, _, _ = phase3
    assert client.get("/encounters").status_code == 401
    response = client.post(
        "/encounters", headers=ALICE, json={"mode": "silent", "patientRef": "demo-01"}
    )
    assert response.status_code == 201
    encounter = response.json()
    assert encounter["mode"] == "silent"
    assert encounter["catalogVersion"] == VERSION
    assert encounter["revision"] == 1
    path = f"/encounters/{encounter['id']}"
    assert client.get(path, headers=ALICE).json() == encounter
    assert client.get(path, headers=BOB).status_code == 404
    assert client.get("/encounters", headers=BOB).json() == []
    params = {"q": "viêm họng", "encounter_id": encounter["id"]}
    assert client.get("/icd/search", params=params, headers=BOB).status_code == 404
    assert (
        client.get("/icd/search", params=params, headers=ALICE).json()["catalogVersion"] == VERSION
    )


def test_similarity_uses_pgvector_when_keyword_search_has_no_match(phase3, monkeypatch):
    client, sessions, _ = phase3
    azure = client.app.state.azure
    embedding_a = [1.0, 0.0] + [0.0] * (EMBEDDING_DIMENSIONS - 2)
    embedding_b = [0.0, 1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 2)
    with sessions.begin() as session:
        session.add_all(
            ICDCodeEmbedding(
                catalog_version=VERSION,
                code=code,
                deployment=azure.settings.azure_openai_embedding,
                embedding=vector,
            )
            for code, vector in (("J00", embedding_a), ("J02.9", embedding_b))
        )
    with monkeypatch.context() as patch:
        patch.setattr(azure, "chat", object())
        patch.setattr(azure, "embed", lambda *_args, **_kwargs: [embedding_b])
        result = client.get("/icd/search", params={"q": "cổ rát"}, headers=ALICE)
    assert result.status_code == 200
    assert [item["code"] for item in result.json()["items"]] == ["J02.9", "J00"]


def test_azure_retry_audits_each_attempt(phase3, monkeypatch):
    client, sessions, _ = phase3
    waits = []
    monkeypatch.setattr("viscribe_api.azure_clients.time.sleep", waits.append)
    outcomes = iter([AzureCallError("http_429", transient=True, retry_after=3), Result("ok", 2, 1)])

    def request():
        result = next(outcomes)
        if isinstance(result, AzureCallError):
            raise result
        return result

    assert client.app.state.azure.invoke("test", "demo-model", request) == "ok"
    assert waits == [3]
    with sessions() as session:
        runs = session.scalars(select(AIRun).order_by(AIRun.attempt)).all()
        assert [(run.attempt, run.status, run.error_code) for run in runs] == [
            (1, "failed", "http_429"),
            (2, "success", None),
        ]
        assert (runs[1].tokens_in, runs[1].tokens_out) == (2, 1)
