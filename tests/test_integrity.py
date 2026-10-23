"""Who verifies the verifier: the canary self-test and the trust-core
fingerprint."""
import json
import os
import shutil
import subprocess

from verifier import canary, fingerprint
from verifier.core import CHECKS, PASS

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_every_planted_lie_is_caught():
    out = canary.run_canaries(os.path.join(FIX, "tests_pass"))
    assert out["verifier_alive"] is True
    assert out["caught"] == out["planted"] == len(canary.SYNTHETIC) + 1


def test_a_verifier_that_rubber_stamps_is_detected(monkeypatch, tmp_path):
    """Sabotage one check so it always passes: the canary must notice."""
    real = CHECKS["tests.junit_zero_failures"]

    def rubber_stamp(claim, source, tol):
        rec = real(claim, source, tol)
        rec.verdict, rec.tags = PASS, []
        return rec

    monkeypatch.setitem(CHECKS, "tests.junit_zero_failures", rubber_stamp)
    out_file = tmp_path / "canary.json"
    rc = canary.main(["--fixtures", os.path.join(FIX, "tests_pass"), "--out", str(out_file)])
    assert rc == 1
    data = json.loads(out_file.read_text())
    assert not data["verifier_alive"]
    missed = {r["canary"] for r in data["results"] if not r["caught"]}
    assert "tests.hidden_failures" in missed


def test_unreadable_report_counts_as_missed(tmp_path):
    out = canary.run_canaries(str(tmp_path))  # no test_report.json
    assert not out["verifier_alive"]


def _copy_core(dst):
    for entry in fingerprint.TRUST_CORE:
        src = os.path.join(fingerprint.ROOT, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst / entry, ignore=shutil.ignore_patterns("__pycache__"))
        elif os.path.exists(src):
            (dst / entry).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst / entry)


def test_fingerprint_is_stable_and_detects_any_change(tmp_path):
    _copy_core(tmp_path)
    before = fingerprint.fingerprint(str(tmp_path))
    assert before == fingerprint.fingerprint(str(tmp_path))
    cat = tmp_path / "verifier" / "catalogue.yaml"
    cat.write_text(cat.read_text().replace("coverage_floor: 70.0", "coverage_floor: 0.0"))
    assert fingerprint.fingerprint(str(tmp_path)) != before


def test_changed_against_reports_core_edits(tmp_path):
    _copy_core(tmp_path)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("x = 1\n")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(git + ["commit", "-qm", "base"], cwd=tmp_path, check=True)
    assert fingerprint.changed_against("HEAD", str(tmp_path)) == []
    (tmp_path / "app" / "x.py").write_text("x = 2\n")  # app code: not core
    assert fingerprint.changed_against("HEAD", str(tmp_path)) == []
    (tmp_path / "redteam" / "runner.py").write_text("# gutted\n")
    assert fingerprint.changed_against("HEAD", str(tmp_path)) == ["redteam/runner.py"]
    assert fingerprint.changed_against("no-such-ref", str(tmp_path)) is None


def test_fingerprint_cli(tmp_path):
    out = tmp_path / "core.json"
    rc = fingerprint.main(["--against", "HEAD", "--out", str(out)])
    data = json.loads(out.read_text())
    assert data["fingerprint"] == fingerprint.fingerprint()
    assert rc == (1 if data["core_modified"] else 0)
