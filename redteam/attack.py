"""Red Team CLI: run the full playbook against staging and record findings.

    python -m redteam.attack --staging $STAGING_URL --out evidence/redteam/

Writes:
  - findings.json : list of probes that succeeded (reachable weaknesses)
  - attack.json   : the full result set (for the evidence pack)

The host guard inside each probe refuses any target but --staging. This is the
authorized pre-release test of our own code on our own throwaway staging.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

from redteam.playbook import exploits  # noqa: F401 (registers probes)
from redteam.runner import PLAYBOOK


def run_playbook(staging_url: str) -> list[dict]:
    results = []
    for eid, probe in PLAYBOOK.items():
        res = probe(staging_url, staging_url)
        results.append(dataclasses.asdict(res))
    return results


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--staging", default=os.environ.get("STAGING_URL", ""))
    p.add_argument("--out", default="evidence/redteam")
    args = p.parse_args(argv)

    if not args.staging:
        print("ERROR: no staging URL; refusing to run (fail closed).", file=sys.stderr)
        return 2

    results = run_playbook(args.staging)
    findings = [r for r in results if r["succeeded"]]

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "attack.json"), "w") as f:
        json.dump({"staging_url": args.staging, "results": results}, f, indent=2)
    with open(os.path.join(args.out, "findings.json"), "w") as f:
        json.dump(findings, f, indent=2)

    print(f"Red Team ran {len(results)} probes against {args.staging}")
    for r in results:
        flag = "FOUND  " if r["succeeded"] else "clean  "
        print(f"  [{flag}] {r['vuln_id']:3} {r['exploit_id']:28} {r['evidence']}")
    print(f"\n{len(findings)} reachable weakness(es) filed. Evidence -> {args.out}")
    # Finding weaknesses is the Red Team's job succeeding, so exit 0.
    return 0


if __name__ == "__main__":
    sys.exit(main())
