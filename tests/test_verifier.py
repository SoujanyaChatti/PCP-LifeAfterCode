"""Verifier unit tests. For each gate we feed recorded evidence (fixtures)
representing PASS, FAIL and the caught-lie case, and assert the verdict and the
claim_mismatch tagging. These are the trust-critical tests."""
import os

from verifier.checks import implementations  # noqa: F401 (registers checks)
from verifier.run import load_catalogue, run_gate
from verifier.sources import FixtureSource

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
CAT = load_catalogue()


def _claim(ctype, asserted, subject_extra=None):
    subj = {"project_id": 1, "mr_iid": 45, "issue_iid": 7,
            "commit_sha": "abc123def456", "pipeline_id": 9,
            "image_digest": "sha256:deadbeef", "staging_url": "http://staging.local"}
    if subject_extra:
        subj.update(subject_extra)
    return {"claim_id": "c-1", "type": ctype, "agent": "builder",
            "stage": "verify", "subject": subj, "asserted": asserted,
            "statement": "x", "created_at": "2026-10-23T00:00:00Z"}


def _run(ctype, fixture, asserted):
    src = FixtureSource(os.path.join(FIX, fixture))
    return run_gate(_claim(ctype, asserted), src, CAT)


def test_tests_pass_gate_passes():
    verdict, records = _run("tests.pass", "tests_pass",
                            {"tests_passed": 142, "tests_failed": 0, "coverage_pct": 87.0})
    assert verdict == "PASS"
    assert all(r["verdict"] == "PASS" for r in records)


def test_tests_pass_caught_lie():
    """Agent asserts 0 failures but the JUnit report shows 3 -> caught lie."""
    verdict, records = _run("tests.pass", "tests_lie",
                            {"tests_passed": 142, "tests_failed": 0, "coverage_pct": 87.0})
    assert verdict == "FAIL"
    junit = next(r for r in records if r["check_id"] == "tests.junit_zero_failures")
    assert junit["verdict"] == "FAIL"
    assert "claim_mismatch" in junit["tags"]


def test_tests_fail_honest_failure():
    """Honest failures (asserted matches) still FAIL, but not tagged as a lie."""
    verdict, records = _run("tests.pass", "tests_fail",
                            {"tests_passed": 137, "tests_failed": 5, "coverage_pct": 55.0})
    assert verdict == "FAIL"
    junit = next(r for r in records if r["check_id"] == "tests.junit_zero_failures")
    assert junit["verdict"] == "FAIL"
    assert "claim_mismatch" not in junit.get("tags", [])


def test_coverage_lie_tagged():
    verdict, records = _run("tests.pass", "tests_pass",
                            {"tests_passed": 142, "tests_failed": 0, "coverage_pct": 99.0})
    cov = next(r for r in records if r["check_id"] == "tests.coverage_within_tolerance")
    assert cov["verdict"] == "FAIL"
    assert "claim_mismatch" in cov["tags"]


def test_inconclusive_when_evidence_missing():
    """Empty fixture dir -> no evidence -> INCONCLUSIVE, which blocks the gate."""
    src = FixtureSource(os.path.join(FIX, "does_not_exist"))
    verdict, records = run_gate(
        _claim("tests.pass", {"tests_failed": 0}), src, CAT)
    assert verdict in ("INCONCLUSIVE", "FAIL")
    assert verdict != "PASS"


def test_code_ready_passes():
    verdict, records = _run("code.ready", "code_ready", {})
    assert verdict == "PASS"


def test_image_built_passes():
    verdict, records = _run("image.built", "image_built", {})
    assert verdict == "PASS"


def test_vuln_fixed_passes_when_replay_fails():
    verdict, records = _run(
        "vuln.fixed", "vuln_fixed",
        {"exploit_id": "v1_sql_injection",
         "regression_test": "tests/test_regressions.py::test_v1_sqli_fixed"})
    assert verdict == "PASS"


def test_deploy_healthy_passes():
    verdict, records = _run("deploy.healthy", "deploy_healthy", {})
    assert verdict == "PASS"


def test_fail_closed_blocks_promotion():
    """Any non-PASS gate must return a non-zero-worthy verdict."""
    verdict, _ = _run("tests.pass", "tests_lie", {"tests_failed": 0})
    assert verdict != "PASS"


# --------------------------------------------------------------------------
# Fail-closed regressions: each of these used to PASS.
# --------------------------------------------------------------------------
def test_empty_claimed_sha_never_matches():
    """An empty claimed SHA is a prefix of every SHA; it must not match."""
    src = FixtureSource(os.path.join(FIX, "code_ready"))
    verdict, records = run_gate(_claim("code.ready", {}, {"commit_sha": ""}), src, CAT)
    sha = next(r for r in records if r["check_id"] == "code.head_sha_matches")
    assert sha["verdict"] == "FAIL"
    assert verdict != "PASS"


def test_image_sha_label_mismatch_with_empty_claim():
    src = FixtureSource(os.path.join(FIX, "image_built"))
    verdict, _ = run_gate(_claim("image.built", {}, {"commit_sha": ""}), src, CAT)
    assert verdict != "PASS"


def test_inflated_pass_count_is_a_caught_lie():
    """0 failures is true, but the agent claims 500 passing tests vs 142."""
    verdict, records = _run("tests.pass", "tests_pass",
                            {"tests_passed": 500, "tests_failed": 0, "coverage_pct": 87.0})
    junit = next(r for r in records if r["check_id"] == "tests.junit_zero_failures")
    assert verdict == "FAIL"
    assert "claim_mismatch" in junit["tags"]


def test_report_for_other_commit_rejected(tmp_path):
    import json

    (tmp_path / "test_report.json").write_text(json.dumps(
        {"total_count": 142, "failed_count": 0, "commit_sha": "fffffff0000000"}))
    (tmp_path / "coverage.json").write_text(json.dumps({"coverage_pct": 87.0}))
    verdict, records = run_gate(
        _claim("tests.pass", {"tests_passed": 142, "tests_failed": 0, "coverage_pct": 87.0}),
        FixtureSource(str(tmp_path)), CAT)
    assert verdict == "FAIL"
    assert "claim_mismatch" in records[0]["tags"]


def test_regression_test_that_passes_on_vulnerable_build_is_rejected():
    """A regression test that also passes pre-patch proves nothing."""
    verdict, records = _run(
        "vuln.fixed", "vuln_weak_test",
        {"exploit_id": "v1_sql_injection",
         "regression_test": "tests/test_regressions.py::test_v1_sqli_fixed"})
    disc = next(r for r in records if r["check_id"] == "vuln.regression_test_discriminates")
    assert verdict == "FAIL"
    assert disc["verdict"] == "FAIL"
    assert "claim_mismatch" in disc["tags"]


def test_deploy_digest_mismatch_is_caught():
    verdict, records = run_gate(
        _claim("deploy.healthy", {}, {"image_digest": "sha256:other"}),
        FixtureSource(os.path.join(FIX, "deploy_healthy")), CAT)
    assert verdict == "FAIL"
