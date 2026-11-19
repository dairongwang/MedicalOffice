"""Test fixtures: a throw-away SQLite database per session, the app started through its lifespan
(migrations run), and a TestClient. Environment is fixed *before* config is imported."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

_tmp = tempfile.mkdtemp(prefix="account-tests-")


def _fresh_postgres(url: str) -> None:
    """``python dev.py test --postgres``: the tests run against TEST_DATABASE_URL — created when missing,
    emptied first (a clean schema, migrated by the app). Only a database whose name ends in ``_test``."""
    import psycopg

    plain = url.replace("postgresql+psycopg://", "postgresql://", 1)
    base, _, name = plain.rpartition("/")
    name = name.split("?", 1)[0]
    if not name.endswith("_test"):
        raise pytest.UsageError(f"TEST_DATABASE_URL must name a database ending in _test (got {name!r}).")
    with psycopg.connect(f"{base}/postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            conn.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(plain, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


if os.environ.get("TEST_DATABASE_URL"):
    _fresh_postgres(os.environ["TEST_DATABASE_URL"])
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ.setdefault("AUTO_MIGRATE", "true")  # PostgreSQL is migrated by the app only when asked to

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATA_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("TOKEN_SECRET", "dummy-test-secret-not-for-real-use")
os.environ.setdefault("SEED_FAKE_ACCOUNTS", "false")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_headers(client):
    """A signed-in administrator (seed.py's admin account)."""
    import clientcrypto
    import seed
    from database import SessionLocal

    with SessionLocal() as db:
        seed.seed(db, log=lambda *a: None)
    auth_key, _ = clientcrypto.derive_keys(seed.password_for("admin"), seed.email_for("admin"))
    res = client.post("/api/v1/auth/login", json={"email": seed.email_for("admin"), "auth_key": auth_key})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}
