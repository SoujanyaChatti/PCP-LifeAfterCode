"""The run replay must show what the evidence pack says, and nothing an
evidence string contains may escape into the page as markup."""
import json
import re

from dashboard.replay import render, slim


def _pack(**over):
    pack = {
        "run": {"kind": "main", "pipeline_id": 9, "ref": "main", "issue_iid": 3,
                "pipeline_url": "https://gitlab.example/p/-/pipelines/9", "commit_sha": "abc1234def"},
        "decision": "BLOCKED",
        "required_gates": ["verifier.canary", "tests.pass", "vuln.fixed", "deploy.healthy"],
        "gate_decisions": {"tests.pass": "FAIL", "vuln.fixed": "FAIL"},
        "claims": [
            {"type": "tests.pass", "agent": "builder", "statement": "All 142 tests pass.",
             "asserted": {"tests_failed": 0}},
            {"type": "vuln.fixed", "agent": "blueteam", "statement": "Patched V1."},
            {"type": "vuln.fixed", "agent": "blueteam", "statement": "Patched V2."},
        ],
        "evidence_by_gate": {
            "tests.pass": [
                {"check_id": "tests.junit_zero_failures", "verdict": "FAIL",
                 "reason": "claim asserts 0 failures but report shows 1", "tags": ["claim_mismatch"]},
                {"check_id": "tests.coverage_within_tolerance", "verdict": "PASS", "reason": "ok"}],
            "vuln.fixed": [{"check_id": "vuln.replay_now_fails", "verdict": "FAIL",
                            "reason": "exploit still succeeds after patch", "tags": ["claim_mismatch"]}],
        },
        "red_vs_blue": {
            "exploits_attempted": ["v1_sql_injection", "v2_reflected_xss"],
            "pre_patch_evidence": {"v1_sql_injection": "tokens leaked",
                                   "v2_reflected_xss": "unescaped <script> reflected into the page"},
            "replay_post_patch": {"v1_sql_injection": {"succeeded": True, "evidence": "tokens leaked"},
                                  "v2_reflected_xss": {"succeeded": False, "evidence": "input was HTML-escaped"}},
        },
        "verifier_self_test": {"planted": 9, "caught": 9, "verifier_alive": True, "results": []},
        "caught_false_claims": [{"check_id": "tests.junit_zero_failures",
                                 "reason": "claim asserts 0 failures but report shows 1"}],
        "blocked_reasons": ["gate tests.pass is FAIL", "1 false claim(s) caught"],
        "content_sha256": "f" * 64,
    }
    pack.update(over)
    return pack


def test_slim_reflects_the_pack():
    s = slim(_pack())
    gates = {g["gate"]: g for g in s["gates"]}
    assert [g["gate"] for g in s["gates"]][0] == "verifier.canary"
    assert gates["verifier.canary"]["verdict"] == "PASS"
    assert (gates["verifier.canary"]["passed"], gates["verifier.canary"]["total"]) == (9, 9)
    assert gates["tests.pass"]["verdict"] == "FAIL"
    assert any(c["lie"] for c in gates["tests.pass"]["checks"])
    assert gates["vuln.fixed"]["claims"] == 2
    assert gates["deploy.healthy"]["verdict"] == "NO EVIDENCE"   # never ran: not a pass
    x = {e["id"]: e for e in s["exploits"]}
    assert x["v1_sql_injection"]["found"] and x["v1_sql_injection"]["still_open"]
    assert x["v2_reflected_xss"]["found"] and not x["v2_reflected_xss"]["still_open"]
    assert s["signed"] and s["label"] == "Pipeline #9 · main"


def test_rehearsal_runs_are_not_presented_as_signed():
    s = slim(_pack(run={"kind": "rehearsal", "scenario": "lie"}))
    assert not s["signed"] and s["label"] == "Rehearsal · lie"


def test_failed_self_test_is_shown_as_fail():
    st = {"planted": 9, "caught": 8, "verifier_alive": False,
          "results": [{"canary": "tests.hidden_failures", "caught": False}]}
    g = slim(_pack(verifier_self_test=st))["gates"][0]
    assert g["verdict"] == "FAIL" and g["passed"] == 8
    assert "tests.hidden_failures" in g["checks"][0]["reason"]


def test_render_embeds_data_without_markup_escaping_the_script():
    evil = "</script><script>alert(1)</script><!--"
    page = render([(None, _pack(blocked_reasons=[evil]))])
    blob = re.search(r"<script type=application/json id=data>(.*?)</script>", page, re.S).group(1)
    assert "<" not in blob and ">" not in blob
    data = json.loads(blob)
    assert data[0]["blocked"] == [evil]                      # round-trips exactly
    assert page.count("<script") == 2                        # data block + player only


def test_render_offers_every_run_given():
    page = render([("Happy path", _pack(decision="PROMOTED")), ("Caught lie", _pack())])
    data = json.loads(re.search(r"id=data>(.*?)</script>", page, re.S).group(1))
    assert [d["label"] for d in data] == ["Happy path", "Caught lie"]


def test_dashboard_build_writes_replay(tmp_path):
    from dashboard.build import main as build

    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"entries": []}))
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps(_pack()))
    assert build(["--ledger", str(ledger), "--pack", str(pack), "--out", str(tmp_path / "public")]) == 0
    index = (tmp_path / "public" / "index.html").read_text()
    assert 'href=replay.html' in index
    assert (tmp_path / "public" / "replay.html").exists()
