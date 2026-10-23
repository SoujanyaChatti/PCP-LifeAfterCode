"""Fail-before/pass-after proof, claim notes, pack assembly + rendering, and the
dashboard."""
import json
import os

import pytest

from agents.claim_lib import claim_note_body, emit_claim, parse_claim_notes
from evidence_pack.assemble import build_pack
from evidence_pack.assemble import main as assemble_main
from evidence_pack.report import main as report_main
from evidence_pack.report import render
from redteam import prove_regressions


def test_regression_tests_fail_before_and_pass_after(tmp_path, monkeypatch):
    monkeypatch.delenv("PCP_UNPATCHED", raising=False)
    assert prove_regressions.main(["--in-process", "--out", str(tmp_path)]) == 0
    proof = json.loads((tmp_path / "regression_proof.json").read_text())
    assert len(proof) == 7
    assert all(r == {"pre_patch": "failed", "post_patch": "passed"} for r in proof.values())


def test_incomplete_patch_shows_up_in_the_proof(tmp_path, monkeypatch):
    monkeypatch.setenv("PCP_UNPATCHED", "V1")
    assert prove_regressions.main(["--in-process", "--out", str(tmp_path)]) == 1
    proof = json.loads((tmp_path / "regression_proof.json").read_text())
    assert proof["tests/test_regressions.py::test_v1_sqli_fixed"]["post_patch"] == "failed"


def test_prove_needs_both_urls(tmp_path):
    assert prove_regressions.main(["--pre-url", "", "--post-url", "", "--out", str(tmp_path)]) == 2


def test_claim_notes_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("PCP_CLAIMS_DIR", str(tmp_path))
    claim = emit_claim(claim_type="tests.pass", agent="builder", stage="verify",
                       subject={"project_id": 1, "commit_sha": "abc1234"},
                       statement="ok", asserted={"tests_failed": 0})
    notes = [{"id": 2, "body": claim_note_body(claim), "created_at": "2026-01-02",
              "author": {"username": "ai-pcp"}},
             {"id": 1, "body": "just chatting", "created_at": "2026-01-01"},
             {"id": 3, "body": "<!-- pcp-claim -->\n```json\n{not json}\n```"}]
    parsed = parse_claim_notes(notes)
    assert len(parsed) == 1
    assert parsed[0]["claim_id"] == claim["claim_id"]
    assert parsed[0]["_source"]["author"] == "ai-pcp"


def _scaffold(tmp_path, gates, canary=None, trust_core=None, redteam=None):
    claims, ev = tmp_path / "claims", tmp_path / "evidence"
    rt = ev / "redteam"
    rt.mkdir(parents=True)
    claims.mkdir()
    (claims / "images.json").write_text('{"digest": "not a claim"}')
    for gate, (verdict, agent, recs, fp) in gates.items():
        (ev / f"{gate}.evidence.json").write_text(json.dumps({
            "claim": {"type": gate, "agent": agent, "claim_id": gate}, "verdict": verdict,
            "records": recs, "verifier_fingerprint": fp}))
    if canary is not None:
        (ev / "canary.json").write_text(json.dumps(canary))
    if trust_core is not None:
        (ev / "trust_core.json").write_text(json.dumps(trust_core))
    for name, obj in (redteam or {}).items():
        (rt / name).write_text(json.dumps(obj))
    return str(claims), str(ev), str(rt)


PASS_REC = [{"check_id": "x", "verdict": "PASS", "reason": "ok", "tags": []}]
ALIVE = {"verifier_alive": True, "planted": 9, "caught": 9}


def test_missing_required_gate_blocks(tmp_path):
    c, e, r = _scaffold(tmp_path, {"tests.pass": ("PASS", "builder", PASS_REC, "f")}, ALIVE)
    pack = build_pack(c, e, r, {}, ["tests.pass", "vuln.fixed", "verifier.canary"])
    assert pack["decision"] == "BLOCKED"
    assert "required gate vuln.fixed has no evidence" in pack["blocked_reasons"]
    assert pack["claims"] == []  # images.json is not a claim


def test_no_evidence_at_all_blocks(tmp_path):
    c, e, r = _scaffold(tmp_path, {})
    assert build_pack(c, e, r, {})["decision"] == "BLOCKED"


def test_dead_canary_and_mixed_verifiers_block(tmp_path):
    c, e, r = _scaffold(tmp_path, {"tests.pass": ("PASS", "builder", PASS_REC, "f1"),
                                   "image.built": ("PASS", "release", PASS_REC, "f2")},
                        {"verifier_alive": False, "planted": 9, "caught": 8})
    pack = build_pack(c, e, r, {}, ["tests.pass", "image.built", "verifier.canary"])
    assert pack["decision"] == "BLOCKED"
    assert any("planted lie got through" in x for x in pack["blocked_reasons"])
    assert any("different verifier versions" in x for x in pack["blocked_reasons"])


def test_promoted_pack_records_judged_claims_and_core_notice(tmp_path):
    c, e, r = _scaffold(
        tmp_path, {"tests.pass": ("PASS", "builder", PASS_REC, "f")}, ALIVE,
        trust_core={"core_modified": True, "changed": ["verifier/core.py"]},
        redteam={"attack.json": {"results": [
            {"exploit_id": "v1_sql_injection", "succeeded": True, "evidence": "dumped tokens"}]},
            "replay_results.json": {"v1_sql_injection": {"succeeded": False, "vuln_id": "V1",
                                                         "evidence": "literal"}},
            "regression_proof.json": {"tests/test_regressions.py::test_v1_sqli_fixed":
                                      {"pre_patch": "failed", "post_patch": "passed"}}})
    pack = build_pack(c, e, r, {"issue_iid": 4}, ["tests.pass", "verifier.canary"])
    assert pack["decision"] == "PROMOTED"
    assert pack["judged_claims"] == [{"claim_id": "tests.pass", "type": "tests.pass",
                                      "agent": "builder", "verdict": "PASS", "lie": False,
                                      "origin": None}]
    assert pack["notices"] and pack["verifier_fingerprint"] == "f"
    md = render(pack, "http://pack", "http://verify", trust={"agents": {
        "builder": {"verified": 2, "claims": 2, "lies": 0, "tier": "autonomous"}}})
    assert "PROMOTED" in md and "dumped tokens" in md and "test_v1_sqli_fixed" in md
    assert "planted 9 known lies, caught 9" in md and "🟢 autonomous" in md
    assert "CODEOWNER" in md


def test_blocked_render_mentions_followup_only_when_given(tmp_path):
    lie = [{"check_id": "tests.junit_zero_failures", "verdict": "FAIL", "reason": "lie",
            "tags": ["claim_mismatch"], "observed": {"failed": 1}}]
    c, e, r = _scaffold(tmp_path, {"tests.pass": ("FAIL", "builder", lie, "f")})
    pack = build_pack(c, e, r, {}, ["tests.pass", "verifier.canary"])
    assert "follow-up" not in render(pack)
    assert "https://gl/issues/9" in render(pack, followup_url="https://gl/issues/9")
    assert "Verifier self-test did not run" in render(pack)


def test_assemble_and_report_cli(tmp_path):
    c, e, r = _scaffold(tmp_path, {"tests.pass": ("PASS", "builder", PASS_REC, "f")}, ALIVE)
    meta = tmp_path / "meta.json"
    meta.write_text('{"issue_iid": 1}')
    out = tmp_path / "pack.json"
    assert assemble_main(["--claims", c, "--evidence", e, "--redteam", r, "--meta", str(meta),
                          "--require", "tests.pass,verifier.canary", "--out", str(out)]) == 0
    assert (tmp_path / "pack.json.canonical").exists()
    assert assemble_main(["--claims", c, "--evidence", e, "--redteam", r,
                          "--require", "tests.pass,deploy.healthy", "--out", str(out)]) == 1
    md = tmp_path / "comment.md"
    assert report_main(["--pack", str(out), "--trust", str(tmp_path / "missing.json"),
                        "--out", str(md)]) == 0
    assert "BLOCKED" in md.read_text()


def test_dashboard_renders_history(tmp_path):
    from dashboard.build import main as build
    from verifier.ledger import append, save

    led = {"entries": []}
    append(led, {"decision": "BLOCKED", "run": {"commit_sha": "abc"},
                 "claims": [{"agent": "builder", "type": "tests.pass", "verdict": "FAIL",
                             "lie": True}]})
    path = tmp_path / "ledger.json"
    save(led, str(path))
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps({"decision": "BLOCKED", "blocked_reasons": ["<b>x</b>"],
                                "gate_decisions": {"tests.pass": "FAIL"},
                                "caught_false_claims": [{"check_id": "c", "reason": "r"}]}))
    assert build(["--ledger", str(path), "--pack", str(pack), "--out", str(tmp_path / "pub")]) == 0
    html = (tmp_path / "pub" / "index.html").read_text()
    assert "false claims caught" in html and "probation" in html
    assert "<b>x</b>" not in html  # escaped


@pytest.mark.parametrize("text,ref,expected", [
    ("Closes #12", "", 12), ("", "feature/issue-7", 7), ("nothing", "main", None)])
def test_issue_discovery(monkeypatch, text, ref, expected):
    from scripts.gitlab_report import find_issue_iid

    monkeypatch.delenv("ISSUE_IID", raising=False)
    monkeypatch.setenv("CI_MERGE_REQUEST_DESCRIPTION", text)
    monkeypatch.setenv("CI_COMMIT_MESSAGE", "")
    monkeypatch.setenv("CI_COMMIT_REF_NAME", ref)
    assert find_issue_iid() == expected


def test_regression_index_maps_every_weakness():
    from scripts.regression_index import regression_test_for

    assert all(regression_test_for(f"V{i}") for i in range(1, 8))
    assert regression_test_for("V99") == ""


def test_collect_claims_fallback_for_findings(tmp_path, monkeypatch):
    from scripts.collect_claims import main

    monkeypatch.delenv("VERIFIER_READONLY_TOKEN", raising=False)
    monkeypatch.setenv("CI_COMMIT_SHA", "abc1234567")
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps([
        {"exploit_id": "v1_sql_injection", "vuln_id": "V1", "title": "SQLi"},
        {"exploit_id": "v3_idor", "vuln_id": "V3", "title": "IDOR"}]))
    out = tmp_path / "claims"
    assert main(["vuln.fixed", "--out-dir", str(out), "--findings", str(findings)]) == 0
    names = sorted(os.listdir(out))
    assert names == ["vuln.fixed.V1.json", "vuln.fixed.V3.json"]
    v3 = json.loads((out / "vuln.fixed.V3.json").read_text())
    assert v3["asserted"]["regression_test"].endswith("::test_v3_idor_fixed")
