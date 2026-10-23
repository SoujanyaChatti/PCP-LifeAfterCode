"""Scheduled drift watch — proofs expire.

A promotion is proven at one moment. This re-runs the deploy.healthy gate
against the digest the ledger says was last PROMOTED: if production now serves
something else (a deploy outside the pipeline, a manual rollback) or is
unhealthy, it opens an incident issue.

    python scripts/watch_prod.py --ledger ledger.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.checks import implementations  # noqa: F401,E402
from verifier.ledger import load  # noqa: E402
from verifier.run import _cloudrun, load_catalogue, run_gate  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ledger", default="ledger.json")
    p.add_argument("--prod-url", default=os.environ.get("PROD_URL", ""))
    p.add_argument("--out", default="evidence")
    args = p.parse_args(argv)

    promoted = [e for e in load(args.ledger).get("entries", []) if e.get("decision") == "PROMOTED"]
    if not promoted:
        print("nothing has been promoted yet; nothing to watch")
        return 0
    last = promoted[-1]["run"]
    claim = {
        "claim_id": f"watch-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}", "type": "deploy.healthy",
        "agent": "release", "stage": "monitor",
        "subject": {"project_id": int(os.environ.get("CI_PROJECT_ID", "0")),
                    "commit_sha": last.get("commit_sha") or "",
                    "image_digest": last.get("image_digest"), "service_url": args.prod_url},
        "statement": "Production still serves the last proven digest and is healthy.",
        "asserted": {}, "created_at": datetime.now(timezone.utc).isoformat(),
    }
    verdict, records = run_gate(claim, _cloudrun(), load_catalogue())
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "watch.evidence.json"), "w") as f:
        json.dump({"claim": claim, "verdict": verdict, "records": records}, f, indent=2)
    for r in records:
        print(f"  [{r['verdict']:12}] {r['check_id']}: {r['reason']}")
    if verdict == "PASS":
        print("production still matches its proof")
        return 0

    token = os.environ.get("PCP_REPORTER_TOKEN")
    if token:
        import httpx

        body = "\n".join(
            ["Scheduled re-verification found production no longer matches its proof.", "",
             f"Last proven digest: `{last.get('image_digest')}` (commit `{last.get('commit_sha')}`)", ""]
            + [f"- {r['verdict']} `{r['check_id']}`: {r['reason']}" for r in records]
            + ["", f"Watch job: {os.environ.get('CI_JOB_URL', '')}"])
        httpx.post(f"{os.environ['CI_API_V4_URL']}/projects/{os.environ['CI_PROJECT_ID']}/issues",
                   headers={"PRIVATE-TOKEN": token}, timeout=30.0,
                   json={"title": "[pcp] Production drifted from its proof",
                         "description": body, "labels": "incident,pcp::drift"}).raise_for_status()
        print("opened drift incident issue")
    return 1


if __name__ == "__main__":
    sys.exit(main())
