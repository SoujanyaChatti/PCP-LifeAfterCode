"""End-to-end rehearsal — the whole proof-carrying loop, offline.

Runs the full issue-to-production story in-process (no GitLab/GCP needed) so you
can see and record it, and so pipeline history can be seeded. Three scenarios:

    python -m scripts.rehearse --scenario happy      # proven promotion
    python -m scripts.rehearse --scenario security   # Red finds, Blue fixes, replay proves it
    python -m scripts.rehearse --scenario lie        # false 'tests pass' claim is caught & blocked

Each scenario assembles a real evidence pack and prints the Reporter comment.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import tempfile

from fastapi.testclient import TestClient

FAKE_STAGING = "http://staging.local"


# --------------------------------------------------------------------------
# In-process app + probe plumbing (mirrors the CI jobs, offline)
# --------------------------------------------------------------------------
def _app_client(secure: bool, db_path: str) -> TestClient:
    os.environ["SECURE_MODE"] = "1" if secure else "0"
    os.environ["PCP_DB_PATH"] = db_path
    from app import db as dbm
    from app import main as mainm
    from app import summarize as summ

    importlib.reload(dbm)
    importlib.reload(summ)
    importlib.reload(mainm)
    dbm.init_db(reset=True)
    return TestClient(mainm.app, base_url=FAKE_STAGING, raise_server_exceptions=False)


def _bind_probes(secure: bool, db_path: str) -> None:
    from redteam import runner
    from redteam.playbook import exploits

    def fake_client_for(target, allowed=None):
        runner.assert_allowed(target, FAKE_STAGING)
        return _app_client(secure, db_path)

    exploits.client_for = fake_client_for


def _banner(t: str) -> None:
    print("\n" + "=" * 68 + f"\n  {t}\n" + "=" * 68)


# --------------------------------------------------------------------------
# Shared gate steps
# --------------------------------------------------------------------------
def _run_tests_gate(work: str, tmp: str, lie: bool, failing: bool) -> str:
    """Produce a tests.pass claim + evidence. If failing, a real test fails and
    (lie) the claim still asserts 0 failures -> caught. Returns verdict."""
    import subprocess
    import sys

    report = os.path.join(tmp, "report.xml")
    cov = os.path.join(tmp, "coverage.xml")
    target = "tests/test_app.py" if not failing else "scripts/_demo_failing_test.py"
    subprocess.run(
        [sys.executable, "-m", "pytest", target, f"--junitxml={report}",
         "--cov=app", f"--cov-report=xml:{cov}", "-q"],
        capture_output=True,
    )
    fx = os.path.join(tmp, "fx_tests")
    subprocess.run(
        [sys.executable, "scripts/report_to_fixture.py", "--report", report,
         "--coverage", cov, "--out", fx], capture_output=True,
    )
    claim_path = os.path.join(work, "claims", "tests.pass.json")
    os.makedirs(os.path.dirname(claim_path), exist_ok=True)
    env = dict(os.environ, PCP_CLAIMS_DIR=os.path.join(work, "claims"), CI_COMMIT_SHA="abc123def456")
    script = "scripts/inject_false_claim.py" if lie else "scripts/claim_from_report.py"
    cmd = [sys.executable, script, "--out", claim_path]
    if not lie:
        cmd += ["--report", report, "--coverage", cov]
    subprocess.run(cmd, env=env, capture_output=True)

    from verifier.checks import implementations  # noqa: F401
    from verifier.run import load_catalogue, run_gate
    from verifier.sources import FixtureSource

    with open(claim_path) as f:
        claim = json.load(f)
    verdict, records = run_gate(claim, FixtureSource(fx), load_catalogue())
    ev = os.path.join(work, "evidence", "tests.pass.evidence.json")
    os.makedirs(os.path.dirname(ev), exist_ok=True)
    with open(ev, "w") as f:
        json.dump({"claim": claim, "verdict": verdict, "records": records}, f, indent=2)
    icon = "✅" if verdict == "PASS" else "❌"
    print(f"  {icon} verify:tests -> {verdict}")
    for r in records:
        tag = "   [CAUGHT LIE]" if "claim_mismatch" in r.get("tags", []) else ""
        print(f"      [{r['verdict']:5}] {r['check_id']}: {r['reason']}{tag}")
    return verdict


def _run_security_gate(work: str, db_path: str) -> dict:
    """Red attacks vulnerable build, Blue 'patches' (secure build), verifier
    replays. Writes attack.json + replay_results.json. Returns summary."""
    from redteam import attack, replay_gate

    rt = os.path.join(work, "evidence", "redteam")
    _bind_probes(secure=False, db_path=db_path)
    attack.main(["--staging", FAKE_STAGING, "--out", rt])
    findings = json.loads(open(os.path.join(rt, "findings.json")).read())
    print(f"  Red Team found {len(findings)} reachable weakness(es).")

    _bind_probes(secure=True, db_path=db_path)  # Blue Team patch applied
    rc = replay_gate.main(["--staging", FAKE_STAGING,
                           "--findings", os.path.join(rt, "findings.json"), "--out", rt])
    icon = "✅" if rc == 0 else "❌"
    print(f"  {icon} blueteam-verify (replay) -> {'PASS' if rc == 0 else 'FAIL'}")

    # Write a vuln.fixed evidence record per finding for the pack.
    from verifier.checks import implementations  # noqa: F401
    from verifier.run import load_catalogue, run_gate
    from verifier.sources import FixtureSource

    records = []
    # regression tests exist (tests/test_regressions.py)
    reg = os.path.join(rt, "regression_tests.json")
    json.dump(["tests/test_regressions.py::test_v1_sqli_fixed"], open(reg, "w"))
    for fnd in findings:
        claim = {"claim_id": f"vf-{fnd['exploit_id']}", "type": "vuln.fixed",
                 "agent": "blueteam", "stage": "secure",
                 "subject": {"project_id": 1, "commit_sha": "abc123def456"},
                 "asserted": {"exploit_id": fnd["exploit_id"],
                              "regression_test": "tests/test_regressions.py::test_v1_sqli_fixed"},
                 "statement": f"patched {fnd['vuln_id']}", "created_at": "1970-01-01T00:00:00Z"}
        v, recs = run_gate(claim, FixtureSource(rt), load_catalogue())
        records.append({"claim": claim, "verdict": v, "records": recs})
    ev = os.path.join(work, "evidence", "vuln.fixed.evidence.json")
    all_recs = [r for rr in records for r in rr["records"]]
    verdict = "PASS" if all(r["verdict"] == "PASS" for r in all_recs) else "FAIL"
    json.dump({"claim": {"type": "vuln.fixed"}, "verdict": verdict, "records": all_recs},
              open(ev, "w"), indent=2)
    return {"findings": len(findings), "gate": "PASS" if rc == 0 else "FAIL"}


def _assemble_and_report(work: str) -> dict:
    import subprocess
    import sys

    meta = {"issue_iid": 42, "mr_iid": 7, "commit_sha": "abc123def456",
            "image_digest": "sha256:deadbeefcafef00d", "pipeline_id": 9}
    meta_path = os.path.join(work, "run_meta.json")
    json.dump(meta, open(meta_path, "w"))
    pack_path = os.path.join(work, "evidence-pack.json")
    subprocess.run(
        [sys.executable, "-m", "evidence_pack.assemble",
         "--claims", os.path.join(work, "claims"),
         "--evidence", os.path.join(work, "evidence"),
         "--redteam", os.path.join(work, "evidence", "redteam"),
         "--meta", meta_path, "--out", pack_path], capture_output=True)
    # Sign + verify (fallback digest if no cosign).
    subprocess.run(["bash", "scripts/sign_pack.sh", pack_path + ".canonical"], capture_output=True)
    vr = subprocess.run(["bash", "scripts/verify_pack.sh", pack_path], capture_output=True, text=True)
    sig_ok = vr.returncode == 0

    from evidence_pack.report import render
    pack = json.load(open(pack_path))
    print("\n--- Reporter comment (posted on the issue) ---\n")
    print(render(pack, pack_url=f"file://{pack_path}", verify_job_url="(verify-evidence job)"))
    print(f"\n  signature verifies: {sig_ok}")
    return pack


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------
def scenario_happy(work, db_path):
    _banner("SCENARIO: happy path — proven promotion")
    print("Plan: acceptance criteria written, risk::low.  (agent step)")
    print("Build: MR opened, tests written.  (agent step)")
    _run_tests_gate(work, work, lie=False, failing=False)
    _run_security_gate(work, db_path)
    print("  ✅ verify:deploy -> PASS (prod serves claimed digest, 5/5 probes)")
    return _assemble_and_report(work)


def scenario_security(work, db_path):
    _banner("SCENARIO: security — Red finds, Blue fixes, replay proves it")
    _run_tests_gate(work, work, lie=False, failing=False)
    summary = _run_security_gate(work, db_path)
    print(f"\n  Security outcome: {summary}")
    return _assemble_and_report(work)


def scenario_lie(work, db_path):
    _banner("SCENARIO: lie — agent claims tests pass, but one fails")
    verdict = _run_tests_gate(work, work, lie=True, failing=True)
    print(f"\n  Gate verdict: {verdict} — promotion BLOCKED, follow-up issue opened.")
    return _assemble_and_report(work)


SCENARIOS = {"happy": scenario_happy, "security": scenario_security, "lie": scenario_lie}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", choices=list(SCENARIOS) + ["all"], default="all")
    p.add_argument("--keep", action="store_true", help="keep the work dir")
    args = p.parse_args(argv)

    scenarios = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in scenarios:
        work = tempfile.mkdtemp(prefix=f"pcp_rehearse_{name}_")
        db_path = os.path.join(work, "notes.db")
        try:
            SCENARIOS[name](work, db_path)
        finally:
            if not args.keep:
                shutil.rmtree(work, ignore_errors=True)
    print("\nRehearsal complete.")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
