from pathlib import Path

import pytest
from sqlalchemy import func, select

from viscribe_api.azure_clients import AzureCallError, Result
from viscribe_api.catalog import CatalogError, import_release
from viscribe_api.models import EMBEDDING_DIMENSIONS, AIRun, ICDCode, ICDCodeEmbedding

CATALOG = Path(__file__).resolve().parents[2] / "viscribe-data/fixtures/icd10-who2019-demo.csv"
VERSION = "demo-test-v1"
SOURCE = "https://icd.who.int/browse10/2019/en"
ALICE = {"Authorization": f"Bearer {'a' * 32}"}
BOB = {"Authorization": f"Bearer {'b' * 32}"}


def test_catalog_import_is_idempotent_and_version_is_immutable(test_api):
    _, sessions, rows = test_api
    assert not import_release(sessions, rows, VERSION, SOURCE)
    with pytest.raises(CatalogError, match="already has a different"):
        import_release(sessions, rows[:-1], VERSION, SOURCE)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ICDCode)) == len(rows)


def test_lookup_by_code_and_vietnamese_title(test_api):
    client, _, _ = test_api
    assert client.get("/health").json() == {"status": "ok"}
    for query in ("viêm họng", "viem hong", "J02", "j029"):
        response = client.get("/icd/search", params={"q": query}, headers=ALICE)
        assert response.status_code == 200
        assert response.json()["catalogVersion"] == VERSION
        codes = [item["code"] for item in response.json()["items"]]
        assert "J02.9" in codes
        assert "J02" not in codes
    assert client.get("/icd/search", params={"q": "Z99.9"}, headers=ALICE).json()["items"] == []


def test_only_owner_can_list_or_open_encounter(test_api):
    client, _, _ = test_api
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


def test_similarity_uses_pgvector_when_keyword_search_has_no_match(test_api, monkeypatch):
    client, sessions, _ = test_api
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


def test_azure_retry_audits_each_attempt(test_api, monkeypatch):
    client, sessions, _ = test_api
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
