"""End-to-end rehearsal — the whole proof-carrying loop, offline.

Runs the same steps as the CI pipeline, in-process (no GitLab or Google Cloud):
real pytest run -> claim -> verifier; verifier self-test; Red Team against the
pre-patch twin; replay + fail-before/pass-after proof against the hardened
build; health probes against the running app; evidence pack; ledger; report;
dashboard. Only two things are stand-ins, and they are labelled as such: the
image digest (no registry) and the signature (no OIDC identity -> UNSIGNED).

    python -m scripts.rehearse --scenario happy      # proven promotion
    python -m scripts.rehearse --scenario security   # incomplete patch blocked, then fixed
    python -m scripts.rehearse --scenario lie        # false 'tests pass' claim caught
    python -m scripts.rehearse --scenario all --out rehearsal   # + dashboard at rehearsal/public

Runs in one invocation share a trust ledger, so `all` shows earned autonomy:
the Builder's caught lie puts it on probation.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAKE_STAGING = "http://staging.local"
COMMIT = "abc123def4567890abc123def4567890abc123de"


def _banner(t: str) -> None:
    print("\n" + "=" * 72 + f"\n  {t}\n" + "=" * 72)


def _run(cmd: list[str], env: dict | None = None, quiet: bool = True) -> int:
    r = subprocess.run([sys.executable, *cmd], cwd=ROOT, capture_output=quiet,
                       text=True, env={**os.environ, **(env or {})})
    return r.returncode


def _gate(claim_paths: list[str], fixtures: str, evidence_dir: str) -> str:
    """Run the real verifier CLI and summarize its verdict."""
    from verifier.checks import implementations  # noqa: F401
    from verifier.run import main as verify

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = verify([a for c in claim_paths for a in ("--claim", c)]
                    + ["--out", evidence_dir, "--fixtures", fixtures])
    for line in out.getvalue().splitlines():
        if line.startswith("===") or line.strip().startswith("["):
            print("   " + line.replace("=== ", "").replace(" ===", ""))
    return "PASS" if rc == 0 else "FAIL"


# --------------------------------------------------------------------------
# Steps (each mirrors a CI job)
# --------------------------------------------------------------------------
def step_tests(work: str, lie: bool) -> str:
    print("\n▸ test + claim:tests + verify:tests")
    report, cov = os.path.join(work, "report.xml"), os.path.join(work, "coverage.xml")
    targets = ["tests"] + (["scripts/_demo_failing_test.py"] if lie else [])
    _run(["-m", "pytest", *targets, f"--junitxml={report}", "--cov=app", "--cov=verifier",
          "--cov=redteam", "--cov=evidence_pack", f"--cov-report=xml:{cov}", "-q",
          "-p", "no:cacheprovider"])
    fx = os.path.join(work, "fx")
    _run(["scripts/report_to_fixture.py", "--report", report, "--coverage", cov,
          "--out", fx, "--commit", COMMIT])
    env = {"CI_COMMIT_SHA": COMMIT, "PCP_DEMO": "lie" if lie else ""}
    _run(["scripts/collect_claims.py", "tests.pass", "--out-dir", os.path.join(work, "claims"),
          "--report", report, "--coverage", cov], env=env)
    with open(os.path.join(work, "claims", "tests.pass.json")) as f:
        asserted = json.load(f)["asserted"]
    print(f"   Builder claims: {asserted}")
    verdict = _gate([os.path.join(work, "claims", "tests.pass.json")], fx,
                    os.path.join(work, "evidence"))
    print("\n▸ verify:canary")
    from verifier.canary import run_canaries

    canary = run_canaries(fx)
    with open(os.path.join(work, "evidence", "canary.json"), "w") as f:
        json.dump(canary, f, indent=2)
    print(f"   planted {canary['planted']} known lies, caught {canary['caught']}"
          f" -> verifier {'alive' if canary['verifier_alive'] else 'BROKEN'}")
    return verdict


def step_image(work: str) -> str:
    """No registry offline: a stand-in digest, but checked by the real verifier."""
    print("\n▸ package + verify:package  (stand-in registry record, labelled)")
    digest = "sha256:" + hashlib.sha256(f"rehearsal-{COMMIT}".encode()).hexdigest()
    fx = os.path.join(work, "fx_image")
    os.makedirs(fx, exist_ok=True)
    with open(os.path.join(fx, "registry_image.json"), "w") as f:
        json.dump({"digest": digest, "simulated": True,
                   "labels": {"commit_sha": COMMIT, "secure_mode": "1"}}, f)
    images = os.path.join(work, "images.json")
    with open(images, "w") as f:
        json.dump({"sha": COMMIT, "digest": digest}, f)
    _run(["scripts/collect_claims.py", "image.built", "--out-dir", os.path.join(work, "claims"),
          "--images", images], env={"CI_COMMIT_SHA": COMMIT})
    _gate([os.path.join(work, "claims", "image.built.json")], fx, os.path.join(work, "evidence"))
    return digest


def _bind_probes(secure: bool, unpatched: str, db_path: str) -> None:
    from redteam import runner
    from redteam.playbook import exploits
    from tests.conftest import make_client

    def fake_client_for(target, allowed=None):
        runner.assert_allowed(target, FAKE_STAGING)  # the real host guard still runs
        return make_client(secure, base_url=FAKE_STAGING, db_path=db_path, unpatched=unpatched)

    exploits.client_for = fake_client_for


def step_security(work: str, unpatched: str) -> str:
    from redteam import attack, replay_gate

    rt = os.path.join(work, "evidence", "redteam")
    db = os.path.join(work, "notes.db")
    print("\n▸ redteam  (against the pre-patch twin)")
    _bind_probes(secure=False, unpatched="", db_path=db)
    with contextlib.redirect_stdout(io.StringIO()):
        attack.main(["--staging", FAKE_STAGING, "--out", rt])
    with open(os.path.join(rt, "findings.json")) as f:
        findings = json.load(f)
    for fnd in findings:
        print(f"   FOUND {fnd['vuln_id']} {fnd['exploit_id']}: {fnd['evidence']}")

    label = f"hardened build, but the patch misses {unpatched}" if unpatched else "hardened build"
    print(f"\n▸ blueteam-verify  ({label})")
    _bind_probes(secure=True, unpatched=unpatched, db_path=db)
    with contextlib.redirect_stdout(io.StringIO()):
        replay_gate.main(["--staging", FAKE_STAGING,
                          "--findings", os.path.join(rt, "findings.json"), "--out", rt])
    env = {"PCP_UNPATCHED": unpatched}
    _run(["-m", "redteam.prove_regressions", "--in-process", "--out", rt], env=env)
    _run(["scripts/collect_claims.py", "vuln.fixed", "--out-dir", os.path.join(work, "claims"),
          "--findings", os.path.join(rt, "findings.json")], env={"CI_COMMIT_SHA": COMMIT})
    claims = sorted(os.path.join(work, "claims", c) for c in os.listdir(os.path.join(work, "claims"))
                    if c.startswith("vuln.fixed"))
    print(f"   Blue Team claims {len(claims)} weaknesses fixed; the verifier replays and proves:")
    return _gate(claims, rt, os.path.join(work, "evidence"))


def step_deploy(work: str, digest: str) -> str:
    """Real health probes against the running hardened app; the 'served
    digest' is a stand-in for the Cloud Run API (labelled)."""
    print("\n▸ deploy-prod + verify:deploy  (in-process app; Cloud Run digest is a stand-in)")
    from tests.conftest import make_client

    client = make_client(True, db_path=os.path.join(work, "prod.db"), unpatched="")
    probes = [client.get("/health").status_code for _ in range(5)]
    fx = os.path.join(work, "fx_deploy")
    os.makedirs(fx, exist_ok=True)
    with open(os.path.join(fx, "deploy.json"), "w") as f:
        json.dump({"digest": digest, "simulated": True}, f)
    with open(os.path.join(fx, "health.json"), "w") as f:
        json.dump({"probes": probes, "error_rate": sum(p >= 500 for p in probes) / len(probes)}, f)
    claim = {"claim_id": "rehearsal-deploy", "type": "deploy.healthy", "agent": "release",
             "stage": "release", "subject": {"project_id": 1, "commit_sha": COMMIT,
                                             "image_digest": digest, "service_url": "in-process"},
             "statement": "Promoted; healthy.", "asserted": {},
             "created_at": "1970-01-01T00:00:00Z"}
    path = os.path.join(work, "claims", "deploy.healthy.json")
    with open(path, "w") as f:
        json.dump(claim, f)
    return _gate([path], fx, os.path.join(work, "evidence"))


def step_evidence(work: str, out: str, name: str, require: str) -> dict:
    print("\n▸ evidence + verify-evidence + ledger + report")
    meta = {"kind": "rehearsal", "scenario": name, "issue_iid": 42, "mr_iid": 7,
            "commit_sha": COMMIT, "pipeline_id": None}
    with open(os.path.join(work, "run_meta.json"), "w") as f:
        json.dump(meta, f)
    pack_path = os.path.join(work, "evidence-pack.json")
    _run(["-m", "evidence_pack.assemble", "--claims", os.path.join(work, "claims"),
          "--evidence", os.path.join(work, "evidence"),
          "--redteam", os.path.join(work, "evidence", "redteam"),
          "--meta", os.path.join(work, "run_meta.json"), "--require", require,
          "--out", pack_path])
    env = {"PCP_ALLOW_UNSIGNED": "1"}
    subprocess.run(["bash", "scripts/sign_pack.sh", pack_path + ".canonical"], cwd=ROOT,
                   capture_output=True, env={**os.environ, **env})
    vr = subprocess.run(["bash", "scripts/verify_pack.sh", pack_path], cwd=ROOT,
                        capture_output=True, text=True, env={**os.environ, **env})
    print(f"   pack integrity: {'OK' if vr.returncode == 0 else 'FAILED'} "
          "(UNSIGNED locally; CI signs keyless with cosign)")

    from evidence_pack.report import render
    from verifier.ledger import append, entry_from_pack, load, save, summarize

    with open(pack_path) as f:
        pack = json.load(f)
    ledger_path = os.path.join(out, "ledger.json")
    ledger = load(ledger_path)
    append(ledger, entry_from_pack(pack))
    save(ledger, ledger_path)
    trust = summarize(ledger)
    shutil.copy(pack_path, os.path.join(out, f"evidence-pack.{name}.json"))
    md = render(pack, pack_url=f"file://{out}/evidence-pack.{name}.json",
                verify_job_url="(verify-evidence job)", trust=trust)
    with open(os.path.join(out, f"issue-comment.{name}.md"), "w") as f:
        f.write(md)
    print("\n--- Reporter comment (posted on the issue in CI) ---\n")
    print(md)
    return pack


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------
REQUIRE = "tests.pass,verifier.canary,image.built,vuln.fixed,deploy.healthy"


def _pipeline(out: str, name: str, *, lie=False, unpatched="") -> dict:
    work = tempfile.mkdtemp(prefix=f"pcp_rehearse_{name}_")
    try:
        verdict = step_tests(work, lie)
        if verdict == "PASS":
            digest = step_image(work)
            if step_security(work, unpatched) == "PASS":
                step_deploy(work, digest)
            else:
                print("\n   ✋ security gate FAILED: no deploy.")
        else:
            print("\n   ✋ verify:tests FAILED: package, security and deploy never run.")
        return step_evidence(work, out, name, REQUIRE)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def scenario_happy(out):
    _banner("SCENARIO happy — an honest change, proven all the way to production")
    return _pipeline(out, "happy")


def scenario_security(out):
    _banner("SCENARIO security, round 1 — the Blue Team's patch misses V1 (SQL injection)")
    _pipeline(out, "security-round1", unpatched="V1")
    _banner("SCENARIO security, round 2 — the real patch: the same exploit now fails")
    return _pipeline(out, "security-round2")


def scenario_lie(out):
    _banner("SCENARIO lie — the Builder claims tests pass, but one fails")
    return _pipeline(out, "lie", lie=True)


SCENARIOS = {"happy": scenario_happy, "security": scenario_security, "lie": scenario_lie}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", choices=list(SCENARIOS) + ["all"], default="all")
    p.add_argument("--out", default=None, help="keep ledger, packs, comments + dashboard here")
    args = p.parse_args(argv)

    out = os.path.abspath(args.out) if args.out else tempfile.mkdtemp(prefix="pcp_rehearsal_")
    os.makedirs(out, exist_ok=True)
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    last = None
    for name in names:
        last = SCENARIOS[name](out)
    latest = os.path.join(out, "evidence-pack.latest.json")
    with open(latest, "w") as f:
        json.dump(last, f, indent=2)

    from dashboard.build import main as build_dashboard
    from verifier.ledger import load, summarize

    labels = {"happy": "Happy path", "security-round1": "Security · round 1",
              "security-round2": "Security · round 2", "lie": "Caught lie"}
    replays = [f"{label}={os.path.join(out, f'evidence-pack.{name}.json')}"
               for name, label in labels.items()
               if os.path.exists(os.path.join(out, f"evidence-pack.{name}.json"))]
    with contextlib.redirect_stdout(io.StringIO()):
        build_dashboard(["--ledger", os.path.join(out, "ledger.json"), "--pack", latest,
                         "--out", os.path.join(out, "public")]
                        + [a for r in replays for a in ("--replay-pack", r)])
    trust = summarize(load(os.path.join(out, "ledger.json")))
    _banner("Trust ledger after this rehearsal")
    for agent, a in sorted(trust["agents"].items()):
        print(f"  {agent:9} verified {a['verified']}/{a['claims']}  lies {a['lies']}  "
              f"-> {a['tier']} ({a['why']})")
    print(f"  hash chain: {'intact' if trust['chain_ok'] else 'BROKEN'}, {trust['runs']} runs")
    print(f"\nArtifacts: {out}  (dashboard: {out}/public/index.html)")
    if not args.out:
        print("(temporary directory; pass --out DIR to keep it somewhere specific)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
