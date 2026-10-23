"""Tests for the evidence pack: it assembles correctly, reflects a caught lie,
renders the issue comment, and its canonical form detects tampering."""
import json
import os

from evidence_pack.assemble import build_pack, canonical_bytes
from evidence_pack.report import render


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


def _scaffold(tmp_path, gate_records, redteam=None):
    claims_dir = tmp_path / "claims"
    ev_dir = tmp_path / "evidence"
    rt_dir = ev_dir / "redteam"
    claims_dir.mkdir()
    for gate, data in gate_records.items():
        _write(str(ev_dir / f"{gate}.evidence.json"),
               {"claim": {"type": gate}, "verdict": data["verdict"], "records": data["records"]})
    _write(str(claims_dir / "tests.pass.json"),
           {"claim_id": "c1", "type": "tests.pass", "agent": "builder",
            "stage": "verify", "subject": {}, "statement": "x", "asserted": {},
            "created_at": "2026-10-23T00:00:00Z"})
    if redteam:
        rt_dir.mkdir(parents=True)
        _write(str(rt_dir / "attack.json"), redteam.get("attack", {}))
        if "replay" in redteam:
            _write(str(rt_dir / "replay_results.json"), redteam["replay"])
    return str(claims_dir), str(ev_dir), str(rt_dir)


def test_pack_promoted_when_all_pass(tmp_path):
    c, e, r = _scaffold(tmp_path, {
        "tests.pass": {"verdict": "PASS", "records": [
            {"check_id": "tests.junit_zero_failures", "verdict": "PASS", "reason": "0 failures", "tags": []}]},
    })
    pack = build_pack(c, e, r, {"issue_iid": 7})
    assert pack["decision"] == "PROMOTED"
    assert pack["gate_decisions"]["tests.pass"] == "PASS"
    assert pack["content_sha256"]


def test_pack_blocked_on_caught_lie(tmp_path):
    c, e, r = _scaffold(tmp_path, {
        "tests.pass": {"verdict": "FAIL", "records": [
            {"check_id": "tests.junit_zero_failures", "verdict": "FAIL",
             "reason": "claim asserts 0 but report shows 3", "tags": ["claim_mismatch"],
             "observed": {"failed": 3, "asserted_failed": 0}}]},
    })
    pack = build_pack(c, e, r, {})
    assert pack["decision"] == "BLOCKED"
    assert len(pack["caught_false_claims"]) == 1


def test_report_render_shows_caught_lie(tmp_path):
    c, e, r = _scaffold(tmp_path, {
        "tests.pass": {"verdict": "FAIL", "records": [
            {"check_id": "tests.junit_zero_failures", "verdict": "FAIL",
             "reason": "mismatch", "tags": ["claim_mismatch"],
             "observed": {"failed": 3}}]},
    })
    pack = build_pack(c, e, r, {"issue_iid": 7})
    md = render(pack, "http://pack", "http://verify")
    assert "BLOCKED" in md
    assert "Caught false claim" in md
    assert "http://pack" in md


def test_red_vs_blue_closed(tmp_path):
    redteam = {
        "attack": {"results": [
            {"exploit_id": "v1_sql_injection", "vuln_id": "V1", "succeeded": True},
            {"exploit_id": "v2_reflected_xss", "vuln_id": "V2", "succeeded": False}]},
        "replay": {"v1_sql_injection": {"succeeded": False, "vuln_id": "V1"}},
    }
    c, e, r = _scaffold(tmp_path, {
        "vuln.fixed": {"verdict": "PASS", "records": [
            {"check_id": "vuln.replay_now_fails", "verdict": "PASS", "reason": "closed", "tags": []}]},
    }, redteam=redteam)
    pack = build_pack(c, e, r, {})
    assert pack["red_vs_blue"]["succeeded_pre_patch"] == ["v1_sql_injection"]
    assert pack["red_vs_blue"]["all_closed"] is True
    assert pack["decision"] == "PROMOTED"


def test_tamper_changes_content_hash(tmp_path):
    c, e, r = _scaffold(tmp_path, {
        "tests.pass": {"verdict": "PASS", "records": [
            {"check_id": "x", "verdict": "PASS", "reason": "ok", "tags": []}]},
    })
    pack = build_pack(c, e, r, {"issue_iid": 7})
    original = pack["content_sha256"]
    # Tamper: flip a verdict in the signed content.
    pack["gate_decisions"]["tests.pass"] = "FAIL"
    import hashlib
    recomputed = hashlib.sha256(
        canonical_bytes({k: v for k, v in pack.items() if k != "content_sha256"})
    ).hexdigest()
    assert recomputed != original
