import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from viscribe_api.catalog import import_release, read_catalog
from viscribe_api.config import Settings
from viscribe_api.db import make_session_factory
from viscribe_api.main import create_app
from viscribe_api.models import Base

CATALOG = Path(__file__).resolve().parents[2] / "viscribe-data/fixtures/icd10-who2019-demo.csv"
VERSION = "demo-test-v1"
SOURCE = "https://icd.who.int/browse10/2019/en"


@pytest.fixture
def test_api():
    database_url = os.environ.get("VISCRIBE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set VISCRIBE_TEST_DATABASE_URL to a local PostgreSQL with pgvector")
    engine = create_engine(database_url)
    schema = f"test_viscribe_{uuid4().hex[:12]}"
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
