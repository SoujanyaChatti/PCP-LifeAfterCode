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
        # Only structured claims; other build outputs are not claims.
        if isinstance(c, dict) and {"claim_id", "type", "agent"} <= c.keys():
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


def _evidence_files(evidence_dir: str) -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(evidence_dir, "**", "*.evidence.json"), recursive=True)):
        data = _load_json(p)
        if data:
            out.append(data)
    return out


def build_pack(claims_dir, evidence_dir, redteam_dir, meta,
               required_gates: list[str] | None = None) -> dict:
    """required_gates: gates this pipeline must have evidence for. A gate that
    never ran is not a gate that passed, so a missing one blocks (fail closed).
    Defaults to the gates present, which must be at least one."""
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
        "pre_patch_evidence": {r.get("exploit_id"): r.get("evidence") for r in pre_patch},
        "replay_post_patch": replay,
        "all_closed": bool(replay) and all(not v.get("succeeded") for v in replay.values()),
    }
    proof = _load_json(os.path.join(redteam_dir, "regression_proof.json"), {})
    if proof:
        red_blue["regression_proof"] = proof

    # Verifier self-test (planted lies) and trust-core provenance.
    canary = _load_json(os.path.join(evidence_dir, "canary.json"))
    trust_core = _load_json(os.path.join(evidence_dir, "trust_core.json"))
    fingerprints = sorted({d.get("verifier_fingerprint") for d in _evidence_files(evidence_dir)
                           if d.get("verifier_fingerprint")})

    # Every claim the verifier judged, with its agent and outcome; this is what
    # the trust ledger scores (verifier/ledger.py).
    judged = []
    for data in _evidence_files(evidence_dir):
        claim = data.get("claim", {})
        recs = data.get("records", [])
        if not claim.get("agent"):
            continue
        judged.append({
            "claim_id": claim.get("claim_id"), "type": claim.get("type"),
            "agent": claim.get("agent"), "verdict": data.get("verdict"),
            "lie": any("claim_mismatch" in r.get("tags", []) for r in recs),
            "origin": claim.get("subject", {}).get("origin"),
        })

    required = list(required_gates) if required_gates else list(evidence_by_gate)
    blocked: list[str] = []
    if not required:
        blocked.append("no gate produced any evidence")
    for gate in required:
        if gate == "verifier.canary":
            if canary is None:
                blocked.append("verifier self-test did not run")
            continue
        if gate not in gate_decisions:
            blocked.append(f"required gate {gate} has no evidence")
    for gate, verdict in gate_decisions.items():
        if verdict != "PASS":
            blocked.append(f"gate {gate} is {verdict}")
    if caught_lies:
        blocked.append(f"{len(caught_lies)} false claim(s) caught")
    if pre_patch and not red_blue["all_closed"]:
        blocked.append("an exploit still succeeds after the patch")
    if canary is not None and not canary.get("verifier_alive"):
        blocked.append("verifier self-test failed: a planted lie got through")
    # A change to the grader itself is surfaced, not silently trusted. GitLab
    # enforces the human gate: CODEOWNERS approval on the protected branch.
    notices: list[str] = []
    if trust_core and trust_core.get("core_modified"):
        notices.append("this change modifies the trust-critical core; it cannot merge "
                       "without CODEOWNER approval")
    if len(fingerprints) > 1:
        blocked.append("gates were judged by different verifier versions")

    overall = "BLOCKED" if blocked else "PROMOTED"

    pack = {
        "schema": "pcp/evidence-pack/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": meta,
        "claims": claims,
        "evidence_by_gate": evidence_by_gate,
        "gate_decisions": gate_decisions,
        "red_vs_blue": red_blue,
        "caught_false_claims": caught_lies,
        "judged_claims": judged,
        "verifier_self_test": canary,
        "verifier_fingerprint": fingerprints[0] if len(fingerprints) == 1 else fingerprints,
        "trust_core": trust_core,
        "required_gates": required,
        "blocked_reasons": blocked,
        "notices": notices,
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
    p.add_argument("--require", default="",
                   help="comma list of gates that must have evidence, e.g. tests.pass,image.built")
    args = p.parse_args(argv)

    meta = _load_json(args.meta, {}) if args.meta else {}
    required = [g for g in args.require.split(",") if g]
    pack = build_pack(args.claims, args.evidence, args.redteam, meta, required or None)

    # Write both the pretty (human/debug) and canonical (signing) forms.
    with open(args.out, "w") as f:
        json.dump(pack, f, indent=2)
    with open(args.out + ".canonical", "wb") as f:
        f.write(canonical_bytes(pack))

    print(f"Evidence pack -> {args.out}  decision={pack['decision']}")
    print(f"  gates: {pack['gate_decisions']}")
    if pack["caught_false_claims"]:
        print(f"  CAUGHT {len(pack['caught_false_claims'])} false claim(s)")
    for reason in pack["blocked_reasons"]:
        print(f"  blocked: {reason}")
    return 0 if pack["decision"] == "PROMOTED" else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
