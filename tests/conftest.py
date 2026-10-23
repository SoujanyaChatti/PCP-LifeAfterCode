import os
import tempfile
from importlib import metadata

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


_real_version = metadata.version


def simulate_twin_deps(vulnerable: bool) -> None:
    """In-process stand-in for the image's installed packages: the pre-patch
    twin image ships python-multipart 0.0.6 (requirements-vuln.txt)."""
    def version(name):
        if vulnerable and name == "python-multipart":
            return "0.0.6"
        return _real_version(name)

    metadata.version = version if vulnerable else _real_version


def make_client(secure: bool, base_url: str = "http://testserver", db_path: str | None = None,
                unpatched: str | None = None):
    """Build a TestClient for either the vulnerable (pre-patch twin) or the
    patched build. `unpatched` (e.g. "V1") leaves those fixes out, like the
    PCP_UNPATCHED build arg; None keeps the current environment's value."""
    import importlib
    import os as _os

    _os.environ["SECURE_MODE"] = "1" if secure else "0"
    if db_path:
        _os.environ["PCP_DB_PATH"] = db_path
    previous = _os.environ.get("PCP_UNPATCHED")
    if unpatched is not None:
        _os.environ["PCP_UNPATCHED"] = unpatched
    effective = _os.environ.get("PCP_UNPATCHED", "")
    # The image's installed packages: the twin, or a build whose V6 fix is missing.
    simulate_twin_deps(vulnerable=not secure or "V6" in effective.upper())
    from app import db as db_module
    from app import main as main_module
    from app import summarize as summarize_module

    try:
        # The app reads PCP_UNPATCHED at import; it must not outlive this build.
        importlib.reload(db_module)
        importlib.reload(summarize_module)
        importlib.reload(main_module)
    finally:
        if unpatched is not None:
            if previous is None:
                _os.environ.pop("PCP_UNPATCHED", None)
            else:
                _os.environ["PCP_UNPATCHED"] = previous
    db_module.init_db(reset=True)

    from fastapi.testclient import TestClient

    # raise_server_exceptions=False so the app's own 500 handler runs (as it
    # would in production) instead of the exception bubbling into the test.
    return TestClient(main_module.app, base_url=base_url, raise_server_exceptions=False)


def target_client():
    """Client for the build under test. Regression tests use this so the same
    test can be run against the pre-patch twin and the patched build (see
    redteam/prove_regressions.py): it must fail on the first, pass on the second.

      PCP_TARGET_URL    run against a deployed staging service over HTTP
      PCP_TARGET_BUILD  'patched' (default) or 'pre_patch', in-process
    """
    url = os.environ.get("PCP_TARGET_URL")
    if url:
        import httpx

        return httpx.Client(base_url=url, timeout=15.0)
    return make_client(secure=os.environ.get("PCP_TARGET_BUILD", "patched") != "pre_patch")
