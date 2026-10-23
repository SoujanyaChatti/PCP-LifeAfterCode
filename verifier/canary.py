"""Verifier liveness proof — a smoke-detector test for the gate itself.

A gate that always says PASS looks exactly like a gate that works, until the
day it matters. So every pipeline plants known lies, one per claim type, and
requires the verifier to catch each one. One of them is built from THIS run's
real test report, so the canary exercises the same evidence the real gate used.
If any planted lie gets through, the verifier is broken and the run blocks.

    python -m verifier.canary --fixtures /tmp/fx --out evidence/canary.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from verifier.checks import implementations  # noqa: F401 (registers checks)
from verifier.core import FAIL
from verifier.fingerprint import fingerprint
from verifier.run import load_catalogue, run_gate
from verifier.sources import FixtureSource

SHA = "c0ffee0123456789abcdef0123456789abcdef01"
OTHER_SHA = "deadbeef0123456789abcdef0123456789abcdef"


def _claim(ctype: str, asserted: dict, **subject) -> dict:
    subj = {"project_id": 1, "mr_iid": 1, "issue_iid": 1, "commit_sha": SHA,
            "pipeline_id": 1, "image_digest": "sha256:" + "a" * 64,
            "service_url": "http://canary.invalid"}
    subj.update(subject)
    return {"claim_id": f"canary-{ctype}", "type": ctype, "agent": "builder",
            "stage": "canary", "subject": subj, "asserted": asserted,
            "statement": "planted false claim", "created_at": "1970-01-01T00:00:00Z"}


# name -> (fixture files, false claim, check that must catch it)
SYNTHETIC = {
    "tests.inflated_pass_count": (
        {"test_report.json": {"total_count": 10, "failed_count": 0},
         "coverage.json": {"coverage_pct": 90.0}},
        _claim("tests.pass", {"tests_passed": 99, "tests_failed": 0, "coverage_pct": 90.0}),
        "tests.junit_zero_failures"),
    "tests.hidden_failures": (
        {"test_report.json": {"total_count": 10, "failed_count": 2},
         "coverage.json": {"coverage_pct": 90.0}},
        _claim("tests.pass", {"tests_passed": 10, "tests_failed": 0, "coverage_pct": 90.0}),
        "tests.junit_zero_failures"),
    "tests.inflated_coverage": (
        {"test_report.json": {"total_count": 10, "failed_count": 0},
         "coverage.json": {"coverage_pct": 75.0}},
        _claim("tests.pass", {"tests_passed": 10, "tests_failed": 0, "coverage_pct": 95.0}),
        "tests.coverage_within_tolerance"),
    "code.wrong_head_sha": (
        {"mr.json": {"iid": 1, "target_branch": "main", "sha": OTHER_SHA}},
        _claim("code.ready", {}), "code.head_sha_matches"),
    "image.built_from_other_commit": (
        {"registry_image.json": {"digest": "sha256:" + "a" * 64,
                                 "labels": {"commit_sha": OTHER_SHA}}},
        _claim("image.built", {}), "image.built_from_claimed_sha"),
    "vuln.exploit_still_works": (
        {"replay_results.json": {"v1_sql_injection": {"succeeded": True}},
         "regression_proof.json": {"tests/test_regressions.py::test_v1_sqli_fixed":
                                   {"pre_patch": "failed", "post_patch": "passed"}}},
        _claim("vuln.fixed", {"exploit_id": "v1_sql_injection",
                              "regression_test": "tests/test_regressions.py::test_v1_sqli_fixed"}),
        "vuln.replay_now_fails"),
    "vuln.test_that_tests_nothing": (
        {"replay_results.json": {"v1_sql_injection": {"succeeded": False}},
         "regression_proof.json": {"tests/test_regressions.py::test_v1_sqli_fixed":
                                   {"pre_patch": "passed", "post_patch": "passed"}}},
        _claim("vuln.fixed", {"exploit_id": "v1_sql_injection",
                              "regression_test": "tests/test_regressions.py::test_v1_sqli_fixed"}),
        "vuln.regression_test_discriminates"),
    "deploy.serves_other_digest": (
        {"deploy.json": {"digest": "sha256:" + "b" * 64},
         "health.json": {"probes": [200] * 5, "error_rate": 0.0}},
        _claim("deploy.healthy", {}), "deploy.serves_claimed_digest"),
}


def _write_fixtures(files: dict) -> str:
    d = tempfile.mkdtemp(prefix="pcp_canary_")
    for name, obj in files.items():
        with open(os.path.join(d, name), "w") as f:
            json.dump(obj, f)
    return d


def _probe(name, source, claim, must_catch, catalogue) -> dict:
    verdict, records = run_gate(claim, source, catalogue)
    rec = next((r for r in records if r.get("check_id") == must_catch), {})
    caught = verdict != "PASS" and rec.get("verdict") == FAIL
    return {"canary": name, "gate_verdict": verdict, "check": must_catch,
            "check_verdict": rec.get("verdict"), "reason": rec.get("reason"),
            "caught": caught}


def run_canaries(real_fixtures: str | None = None) -> dict:
    catalogue = load_catalogue()
    results = []
    for name, (files, claim, must_catch) in SYNTHETIC.items():
        results.append(_probe(name, FixtureSource(_write_fixtures(files)), claim,
                              must_catch, catalogue))

    if real_fixtures:
        # A lie about THIS run's real report: one more failure than observed,
        # and a passing count that cannot be right.
        src = FixtureSource(real_fixtures)
        report = src.test_report(0) or {}
        failed = report.get("failed_count", report.get("failed"))
        total = report.get("total_count", report.get("total"))
        if failed is not None and total is not None:
            claim = _claim("tests.pass",
                           {"tests_failed": failed + 1, "tests_passed": total + 1},
                           commit_sha=report.get("commit_sha") or SHA)
            results.append(_probe("tests.lie_about_this_run", src, claim,
                                  "tests.junit_zero_failures", catalogue))
        else:
            results.append({"canary": "tests.lie_about_this_run", "caught": False,
                            "reason": "this run's test report is unreadable"})

    return {"verifier_fingerprint": fingerprint(),
            "planted": len(results),
            "caught": sum(r["caught"] for r in results),
            "verifier_alive": all(r["caught"] for r in results),
            "results": results}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fixtures", default=None, help="this run's real evidence fixtures")
    p.add_argument("--out", default="evidence/canary.json")
    args = p.parse_args(argv)

    out = run_canaries(args.fixtures)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)

    print(f"Verifier self-test: planted {out['planted']} known lies, caught {out['caught']}.")
    for r in out["results"]:
        print(f"  [{'caught' if r['caught'] else 'MISSED'}] {r['canary']}: {r.get('reason')}")
    if not out["verifier_alive"]:
        print("VERIFIER SELF-TEST FAILED: a planted lie got through. Blocking.")
        return 1
    print("Verifier is alive: every planted lie was caught.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
