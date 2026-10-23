"""Assemble the signed evidence pack — the single document that proves the run.

It gathers every claim and every evidence record produced during the run,
groups the evidence by gate, folds in the Red-vs-Blue before/after, computes the
overall decision (PROMOTED or BLOCKED), and surfaces any caught false claims.
The output is canonicalized (sorted keys, no whitespace) so its signature is
reproducible and a separate job can re-canonicalize and verify it.

    python -m evidence_pack.assemble --claims claims/ --evidence evidence/ \
        --redteam evidence/redteam --meta run_meta.json --out evidence-pack.json
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
from datetime import datetime, timezone


def canonical_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def collect_claims(claims_dir: str) -> list[dict]:
    claims = []
    for p in sorted(glob.glob(os.path.join(claims_dir, "*.json"))):
        c = _load_json(p)
        if c:
            claims.append(c)
    return claims


def collect_evidence(evidence_dir: str) -> dict[str, list[dict]]:
    by_gate: dict[str, list[dict]] = {}
    for p in sorted(glob.glob(os.path.join(evidence_dir, "**", "*.evidence.json"), recursive=True)):
        data = _load_json(p)
        if not data:
            continue
        gate = data.get("claim", {}).get("type", os.path.basename(p))
        by_gate.setdefault(gate, []).extend(data.get("records", []))
    return by_gate


def build_pack(claims_dir, evidence_dir, redteam_dir, meta) -> dict:
    claims = collect_claims(claims_dir)
    evidence_by_gate = collect_evidence(evidence_dir)

    all_records = [r for recs in evidence_by_gate.values() for r in recs]
    caught_lies = [r for r in all_records if "claim_mismatch" in r.get("tags", [])]

    # Per-gate decision: PASS only if every record for the gate is PASS.
    gate_decisions = {}
    for gate, records in evidence_by_gate.items():
        verdicts = {r.get("verdict") for r in records}
        if verdicts == {"PASS"}:
            gate_decisions[gate] = "PASS"
        elif "FAIL" in verdicts:
            gate_decisions[gate] = "FAIL"
        else:
            gate_decisions[gate] = "INCONCLUSIVE"

    # Red vs Blue section.
    attack = _load_json(os.path.join(redteam_dir, "attack.json"), {})
    replay = _load_json(os.path.join(redteam_dir, "replay_results.json"), {})
    pre_patch = [r for r in attack.get("results", []) if r.get("succeeded")]
    red_blue = {
        "exploits_attempted": [r.get("exploit_id") for r in attack.get("results", [])],
        "succeeded_pre_patch": [r.get("exploit_id") for r in pre_patch],
        "replay_post_patch": replay,
        "all_closed": bool(replay) and all(not v.get("succeeded") for v in replay.values()),
    }

    overall = "PROMOTED"
    if any(v != "PASS" for v in gate_decisions.values()) or caught_lies:
        overall = "BLOCKED"
    if pre_patch and not red_blue["all_closed"]:
        overall = "BLOCKED"

    pack = {
        "schema": "pcp/evidence-pack/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": meta,
        "claims": claims,
        "evidence_by_gate": evidence_by_gate,
        "gate_decisions": gate_decisions,
        "red_vs_blue": red_blue,
        "caught_false_claims": caught_lies,
        "decision": overall,
    }
    pack["content_sha256"] = hashlib.sha256(
        canonical_bytes({k: v for k, v in pack.items() if k != "content_sha256"})
    ).hexdigest()
    return pack


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--claims", default="claims")
    p.add_argument("--evidence", default="evidence")
    p.add_argument("--redteam", default="evidence/redteam")
    p.add_argument("--meta", default=None)
    p.add_argument("--out", default="evidence-pack.json")
    args = p.parse_args(argv)

    meta = _load_json(args.meta, {}) if args.meta else {}
    pack = build_pack(args.claims, args.evidence, args.redteam, meta)

    # Write both the pretty (human/debug) and canonical (signing) forms.
    with open(args.out, "w") as f:
        json.dump(pack, f, indent=2)
    with open(args.out + ".canonical", "wb") as f:
        f.write(canonical_bytes(pack))

    print(f"Evidence pack -> {args.out}  decision={pack['decision']}")
    print(f"  gates: {pack['gate_decisions']}")
    if pack["caught_false_claims"]:
        print(f"  CAUGHT {len(pack['caught_false_claims'])} false claim(s)")
    return 0 if pack["decision"] == "PROMOTED" else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
