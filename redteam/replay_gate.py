"""Blue-team-verify CLI: replay the previously-successful exploits against the
patched staging and decide the security gate.

    python -m redteam.replay_gate --staging $STAGING_URL \
        --findings evidence/redteam/findings.json --out evidence/redteam/

The gate PASSES only if every exploit that succeeded pre-patch now FAILS. This
is run by the verifier/CI, not the Blue Team agent, using the pinned exploit
scripts (CODEOWNERS-protected). A fix that only hides a finding from a scanner
still fails here. Exits non-zero if any exploit still succeeds (fail closed).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from redteam.playbook import exploits  # noqa: F401
from redteam.runner import PLAYBOOK


def replay(staging_url: str, finding_ids: list[str]) -> dict:
    results = {}
    for eid in finding_ids:
        probe = PLAYBOOK.get(eid)
        if probe is None:
            results[eid] = {"succeeded": True, "evidence": "unknown exploit id"}
            continue
        res = probe(staging_url, staging_url)
        results[eid] = {"succeeded": res.succeeded, "evidence": res.evidence,
                        "vuln_id": res.vuln_id}
    return results


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--staging", default=os.environ.get("STAGING_URL", ""))
    p.add_argument("--findings", default="evidence/redteam/findings.json")
    p.add_argument("--out", default="evidence/redteam")
    args = p.parse_args(argv)

    if not args.staging:
        print("ERROR: no staging URL; fail closed.", file=sys.stderr)
        return 2
    if not os.path.exists(args.findings):
        print("No findings file; nothing was found pre-patch. Gate PASSES.")
        return 0

    with open(args.findings) as f:
        findings = json.load(f)
    finding_ids = [f["exploit_id"] for f in findings]
    if not finding_ids:
        print("No pre-patch findings. Security gate PASSES trivially.")
        return 0

    results = replay(args.staging, finding_ids)

    os.makedirs(args.out, exist_ok=True)
    # This file is the authoritative source the verifier's vuln.replay_now_fails
    # check reads (as replay_results.json in the FixtureSource layout).
    with open(os.path.join(args.out, "replay_results.json"), "w") as f:
        json.dump(results, f, indent=2)

    still = [eid for eid, r in results.items() if r["succeeded"]]
    print(f"Replayed {len(finding_ids)} previously-successful exploit(s) on patched staging:")
    for eid, r in results.items():
        verdict = "STILL OPEN" if r["succeeded"] else "closed"
        print(f"  [{verdict:10}] {r.get('vuln_id','?'):3} {eid}: {r['evidence']}")

    if still:
        print(f"\nSECURITY GATE FAIL: {len(still)} exploit(s) still succeed: {still}")
        return 1
    print("\nSECURITY GATE PASS: every previously-successful exploit now fails.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
