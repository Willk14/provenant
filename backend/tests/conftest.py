import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from provenant.api.main import create_app
from sqlalchemy.engine import make_url

from provenant.api.migrate import upgrade
from provenant.provenance import ExtractedField, SourceRef

DOC = "a" * 64
APP_ROLE, APP_PASSWORD = "provenant_app", "provenant_app_test"

# Set TEST_DATABASE_URL to a Postgres superuser URL to run the suite against Postgres with RLS enforced.
PG_ADMIN_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def make_field():
    def _make(name: str, value: str, confidence: float = 0.99) -> ExtractedField:
        return ExtractedField(
            name=name,
            value=value,
            confidence=confidence,
            source=SourceRef(DOC, page=1, bbox=(72.0, 100.0, 300.0, 114.0), char_start=0, char_end=len(value)),
            extractor="test-extractor@0",
        )

    return _make


def _postgres_database(monkeypatch) -> str:
    """Fresh database migrated by the owner; returns a URL for the non-superuser app role (so RLS applies)."""
    admin = make_url(PG_ADMIN_URL).set(drivername="postgresql+psycopg")
    name = f"pv_{uuid.uuid4().hex[:12]}"
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with engine.connect() as c:
        if not c.scalar(text("select 1 from pg_roles where rolname = :r"), {"r": APP_ROLE}):
            c.execute(text(f"create role {APP_ROLE} login password '{APP_PASSWORD}'"))
        c.execute(text(f"create database {name}"))
    engine.dispose()
    monkeypatch.setenv("PROVENANT_APP_ROLE", APP_ROLE)
    upgrade(admin.set(database=name).render_as_string(hide_password=False))
    return admin.set(database=name, username=APP_ROLE, password=APP_PASSWORD).render_as_string(hide_password=False)


@pytest.fixture
def database_url(tmp_path, monkeypatch):
    if PG_ADMIN_URL:
        url = _postgres_database(monkeypatch)
        yield url
        _drop_database(make_url(url).database)  # each test gets its own database; don't leave hundreds behind
        return
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    upgrade(url)
    yield url


def _drop_database(name: str) -> None:
    engine = create_engine(make_url(PG_ADMIN_URL).set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT")
    with engine.connect() as c:
        c.execute(text(f"drop database if exists {name} with (force)"))
    engine.dispose()


@pytest.fixture
def app(database_url, tmp_path):
    return create_app(database_url, tmp_path / "storage", dev_auth=True)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c
