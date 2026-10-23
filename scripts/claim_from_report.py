"""Builder-side helper: synthesize the tests.pass claim from the real JUnit
report. In the live flow the Builder agent emits this claim; in CI-only runs
this keeps the gate fed with HONEST numbers taken straight from the report.

A deliberately-false claim (for the lie-blocking demo) is produced instead by
scripts/inject_false_claim.py."""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.claim_lib import emit_claim  # noqa: E402
from scripts.report_to_fixture import parse_coverage, parse_junit  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", required=True)
    p.add_argument("--coverage", default="coverage.xml")
    p.add_argument("--out", default="claims/tests.pass.json")
    args = p.parse_args(argv)

    r = parse_junit(args.report)
    cov = parse_coverage(args.coverage) or 0.0
    passed = r["total_count"] - r["failed_count"]

    os.environ["PCP_CLAIMS_DIR"] = os.path.dirname(args.out) or "claims"
    claim = emit_claim(
        claim_type="tests.pass",
        agent="builder",
        stage="verify",
        subject={
            "project_id": int(os.environ.get("CI_PROJECT_ID", "0")),
            "mr_iid": int(os.environ.get("CI_MERGE_REQUEST_IID", "0") or 0),
            "commit_sha": os.environ.get("CI_COMMIT_SHA", ""),
            "pipeline_id": int(os.environ.get("CI_PIPELINE_ID", "0") or 0),
        },
        statement=f"All {r['total_count']} tests pass with {cov}% coverage.",
        asserted={"tests_passed": passed, "tests_failed": r["failed_count"], "coverage_pct": cov},
    )
    print(f"wrote honest claim: {claim['asserted']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
