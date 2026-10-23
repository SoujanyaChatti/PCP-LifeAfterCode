"""End-to-end security gate: Red Team finds weaknesses on the vulnerable build,
then the replay against the patched build must show every one closed. This is
the climax of the demo, proven offline and deterministically."""
import importlib
import os

from fastapi.testclient import TestClient

from redteam import attack, replay_gate
from redteam.playbook import exploits

FAKE_TARGET = "http://staging.local"


def _client_factory(secure: bool):
    os.environ["SECURE_MODE"] = "1" if secure else "0"
    os.environ["PCP_DB_PATH"] = f"/tmp/pcp_gate_{secure}.db"
    from app import db as db_module
    from app import main as main_module
    from app import summarize as summarize_module

    importlib.reload(db_module)
    importlib.reload(summarize_module)
    importlib.reload(main_module)
    db_module.init_db(reset=True)
    return TestClient(main_module.app, base_url=FAKE_TARGET, raise_server_exceptions=False)


def _patch_probes(monkeypatch, secure: bool):
    from redteam import runner

    def fake_client_for(target, allowed=None):
        runner.assert_allowed(target, FAKE_TARGET)
        return _client_factory(secure)

    monkeypatch.setattr(exploits, "client_for", fake_client_for)


def test_redteam_finds_all_seven_then_replay_closes_them(monkeypatch, tmp_path):
    # Phase 1: Red Team attacks the VULNERABLE build.
    _patch_probes(monkeypatch, secure=False)
    out = tmp_path / "redteam"
    rc = attack.main(["--staging", FAKE_TARGET, "--out", str(out)])
    assert rc == 0

    import json
    findings = json.loads((out / "findings.json").read_text())
    found_ids = {f["exploit_id"] for f in findings}
    assert len(findings) == 7, f"expected all 7 weaknesses, got {found_ids}"

    # Phase 2: Blue Team patches (we flip to the secure build), verifier replays.
    _patch_probes(monkeypatch, secure=True)
    rc = replay_gate.main([
        "--staging", FAKE_TARGET,
        "--findings", str(out / "findings.json"),
        "--out", str(out),
    ])
    assert rc == 0, "security gate should PASS after patch"

    results = json.loads((out / "replay_results.json").read_text())
    assert all(not r["succeeded"] for r in results.values()), \
        "every previously-successful exploit must now fail"


def test_replay_gate_blocks_when_fix_incomplete(monkeypatch, tmp_path):
    """If the 'patch' is a no-op (still vulnerable), the gate FAILs."""
    _patch_probes(monkeypatch, secure=False)
    out = tmp_path / "redteam"
    attack.main(["--staging", FAKE_TARGET, "--out", str(out)])

    # Replay against the STILL-vulnerable build: gate must fail closed.
    _patch_probes(monkeypatch, secure=False)
    rc = replay_gate.main([
        "--staging", FAKE_TARGET,
        "--findings", str(out / "findings.json"),
        "--out", str(out),
    ])
    assert rc == 1, "gate must FAIL when exploits still succeed"
