"""Tests run against a throwaway SQLite database, never the real one."""
import os
import tempfile

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_db.name}"
os.environ["ENABLE_SCHEDULER"] = "false"

import pytest  # noqa: E402

from app.db import Base, engine  # noqa: E402
import app.models  # noqa: E402,F401


@pytest.fixture(scope="session", autouse=True)
def schema():
    Base.metadata.create_all(engine)
    yield
    os.unlink(_db.name)
