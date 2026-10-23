import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    """Each test gets a clean, seeded, throwaway database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    monkeypatch.setenv("PCP_DB_PATH", path)
    # app.db reads PCP_DB_PATH at import time, so reload after setting it.
    import importlib

    from app import db as db_module

    importlib.reload(db_module)
    db_module.init_db(reset=True)
    yield
    if os.path.exists(path):
        os.remove(path)


def make_client(secure: bool):
    """Build a TestClient for either the vulnerable or patched build."""
    import importlib
    import os as _os

    _os.environ["SECURE_MODE"] = "1" if secure else "0"
    from app import db as db_module
    from app import main as main_module
    from app import summarize as summarize_module

    importlib.reload(db_module)
    importlib.reload(summarize_module)
    importlib.reload(main_module)
    db_module.init_db(reset=True)

    from fastapi.testclient import TestClient

    # raise_server_exceptions=False so the app's own 500 handler runs (as it
    # would in production) instead of the exception bubbling into the test.
    return TestClient(main_module.app, raise_server_exceptions=False)
