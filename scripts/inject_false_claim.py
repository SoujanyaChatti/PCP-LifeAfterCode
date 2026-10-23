"""Produce a deliberately FALSE tests.pass claim — the negative-path demo.

It asserts 0 failures and inflated coverage regardless of the real report, so
the verifier's claim_mismatch logic catches it and blocks the gate. Used by the
seeded lie-blocked pipeline runs and the negative-path test."""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.claim_lib import emit_claim  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="claims/tests.pass.json")
    p.add_argument("--claimed-passed", type=int, default=142)
    args = p.parse_args(argv)

    os.environ["PCP_CLAIMS_DIR"] = os.path.dirname(args.out) or "claims"
    claim = emit_claim(
        claim_type="tests.pass", agent="builder", stage="verify",
        subject={
            "project_id": int(os.environ.get("CI_PROJECT_ID", "0")),
            "mr_iid": int(os.environ.get("CI_MERGE_REQUEST_IID", "0") or 0),
            "commit_sha": os.environ.get("CI_COMMIT_SHA", ""),
            "pipeline_id": int(os.environ.get("CI_PIPELINE_ID", "0") or 0),
        },
        statement=f"All {args.claimed_passed} tests pass with 92% coverage.",
        asserted={"tests_passed": args.claimed_passed, "tests_failed": 0, "coverage_pct": 92.0},
    )
    print(f"wrote FALSE claim (asserts 0 failures): {claim['asserted']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
