"""Gather the claims a gate will judge.

Agents post claims as MR notes carrying a ```pcp-claim fenced block (see
agents/claim_lib.py). This reads them back from the GitLab API, keeps only the
ones bound to the commit under test, and writes them to claims/. If no agent
claim exists (a CI-only run with no Duo flow attached), it falls back to a
claim synthesized by CI, so the gate always has something to judge.

    python scripts/collect_claims.py tests.pass --report report.xml --coverage coverage.xml
    python scripts/collect_claims.py vuln.fixed --findings evidence/redteam/findings.json
    python scripts/collect_claims.py image.built --images build/images.json

PCP_DEMO=lie makes the tests.pass fallback a deliberately FALSE claim (0
failures, inflated numbers), for the caught-lie pipeline runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.claim_lib import emit_claim, parse_claim_notes  # noqa: E402
from verifier.core import sha_matches  # noqa: E402


def _subject(**extra) -> dict:
    subject = {
        "project_id": int(os.environ.get("CI_PROJECT_ID", "0")),
        "mr_iid": int(os.environ.get("CI_MERGE_REQUEST_IID", "0") or 0),
        "commit_sha": os.environ.get("CI_COMMIT_SHA", ""),
        "pipeline_id": int(os.environ.get("CI_PIPELINE_ID", "0") or 0),
        # No agent posted this claim; CI synthesized it from the job output.
        "origin": "ci-synthesized",
    }
    subject.update(extra)
    return subject


def _notes(path: str, token: str) -> list[dict]:
    import httpx

    r = httpx.get(
        f"{os.environ.get('CI_API_V4_URL', 'https://gitlab.com/api/v4')}/projects/"
        f"{os.environ['CI_PROJECT_ID']}/{path}/notes",
        params={"per_page": 100, "sort": "asc"},
        headers={"PRIVATE-TOKEN": token}, timeout=15.0)
    r.raise_for_status()
    return r.json()


def agent_claims(claim_type: str) -> list[dict]:
    """Claims of this type posted by agents for this MR.

    Most claims are MR notes bound to the current commit. plan.ready is written
    on the linked issue before any commit exists, so it is read from the issue
    and bound to that issue instead."""
    mr = os.environ.get("CI_MERGE_REQUEST_IID")
    token = os.environ.get("VERIFIER_READONLY_TOKEN")
    if not (mr and token):
        return []
    if claim_type == "plan.ready":
        from scripts.gitlab_report import find_issue_iid

        iid = find_issue_iid()
        if iid is None:
            return []
        plans = [c for c in parse_claim_notes(_notes(f"issues/{iid}", token))
                 if c["type"] == claim_type
                 and str(c.get("subject", {}).get("issue_iid")) == str(iid)]
        return plans[-1:]  # a re-planned issue: judge the latest plan
    head = os.environ.get("CI_COMMIT_SHA", "")
    return [c for c in parse_claim_notes(_notes(f"merge_requests/{mr}", token))
            if c["type"] == claim_type
            and sha_matches(c.get("subject", {}).get("commit_sha"), head)]


def fallback_plan_ready(args) -> list[dict]:
    from scripts.gitlab_report import find_issue_iid

    iid = find_issue_iid()
    return [emit_claim(
        claim_type="plan.ready", agent="planner", stage="plan",
        subject=_subject(issue_iid=iid),
        statement=f"Issue #{iid} has acceptance criteria and a risk label.", asserted={})]


def fallback_code_ready(args) -> list[dict]:
    return [emit_claim(
        claim_type="code.ready", agent="builder", stage="create", subject=_subject(),
        statement="MR opened against main at the head commit.", asserted={})]


def fallback_image_built(args) -> list[dict]:
    with open(args.images) as f:
        images = json.load(f)
    return [emit_claim(
        claim_type="image.built", agent="release", stage="package",
        subject=_subject(image_digest=images["digest"]),
        statement=f"Hardened image {images['digest'][:19]} built from this commit.",
        asserted={"image_ref": images.get("gitlab_ref")})]


def fallback_tests_pass(args) -> list[dict]:
    from scripts.report_to_fixture import parse_coverage, parse_junit

    if os.environ.get("PCP_DEMO") == "lie":
        from scripts.inject_false_claim import main as inject

        inject(["--out", os.path.join(args.out_dir, "tests.pass.json")])
        with open(os.path.join(args.out_dir, "tests.pass.json")) as f:
            return [json.load(f)]

    r = parse_junit(args.report)
    cov = parse_coverage(args.coverage) or 0.0
    passed = r["total_count"] - r["failed_count"]
    return [emit_claim(
        claim_type="tests.pass", agent="builder", stage="verify", subject=_subject(),
        statement=f"All {r['total_count']} tests pass with {cov}% coverage.",
        asserted={"tests_passed": passed, "tests_failed": r["failed_count"],
                  "coverage_pct": cov})]


def fallback_vuln_fixed(args) -> list[dict]:
    """One vuln.fixed claim per Red Team finding, naming the regression test
    for that weakness (tests/test_regressions.py::test_<vuln>_*)."""
    with open(args.findings) as f:
        findings = json.load(f)
    from scripts.regression_index import regression_test_for

    claims = []
    for fnd in findings:
        claims.append(emit_claim(
            claim_type="vuln.fixed", agent="blueteam", stage="secure",
            subject=_subject(staging_url=os.environ.get("STAGING_URL")),
            statement=f"Patched {fnd['vuln_id']} ({fnd['title']}).",
            asserted={"exploit_id": fnd["exploit_id"], "vuln_id": fnd["vuln_id"],
                      "regression_test": regression_test_for(fnd["vuln_id"])}))
        # emit_claim names files by type; give each finding its own file.
        os.replace(os.path.join(args.out_dir, "vuln.fixed.json"),
                   os.path.join(args.out_dir, f"vuln.fixed.{fnd['vuln_id']}.json"))
    return claims


FALLBACKS = {"plan.ready": fallback_plan_ready, "code.ready": fallback_code_ready,
             "tests.pass": fallback_tests_pass, "image.built": fallback_image_built,
             "vuln.fixed": fallback_vuln_fixed}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("type", choices=list(FALLBACKS))
    p.add_argument("--out-dir", default="claims")
    p.add_argument("--report", default="report.xml")
    p.add_argument("--coverage", default="coverage.xml")
    p.add_argument("--findings", default="evidence/redteam/findings.json")
    p.add_argument("--images", default="build/images.json")
    args = p.parse_args(argv)

    os.environ["PCP_CLAIMS_DIR"] = args.out_dir
    os.makedirs(args.out_dir, exist_ok=True)
    claims = agent_claims(args.type)
    if claims:
        for i, c in enumerate(claims):
            c["subject"]["origin"] = "agent-note"
            name = f"{c['type']}.json" if len(claims) == 1 else f"{c['type']}.{i}.json"
            with open(os.path.join(args.out_dir, name), "w") as f:
                json.dump(c, f, indent=2)
        print(f"read {len(claims)} agent {args.type} claim(s) from agent notes")
    else:
        FALLBACKS[args.type](args)
        print(f"no agent {args.type} claim found; synthesized a CI claim")
    for name in sorted(os.listdir(args.out_dir)):
        if name.startswith(args.type):
            print(os.path.join(args.out_dir, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
