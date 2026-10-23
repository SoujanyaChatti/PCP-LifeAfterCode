"""Verifier entrypoint. CI runs this per gate:

    python -m verifier.run --claim claims/verify.json --out evidence/verify/ \
        --fixtures tests/fixtures/pass_tests

It loads and schema-validates the claim, resolves the required checks from the
catalogue, runs each against the evidence source, writes one evidence record
per check, prints a gate summary, and exits non-zero if the gate does not PASS.
Fail closed: any FAIL or INCONCLUSIVE blocks the gate.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

import yaml

from verifier.core import CHECKS, FAIL, INCONCLUSIVE, PASS
# Importing the implementations module registers every check.
from verifier.checks import implementations  # noqa: F401
from verifier.sources import FixtureSource, GitLabSource

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_catalogue() -> dict:
    with open(os.path.join(HERE, "catalogue.yaml")) as f:
        return yaml.safe_load(f)


def validate_claim(claim: dict) -> None:
    try:
        import jsonschema
    except ImportError:
        return
    with open(os.path.join(ROOT, "schemas", "claim.schema.json")) as f:
        schema = json.load(f)
    jsonschema.validate(claim, schema)


def build_source(args) -> object:
    if args.fixtures:
        return FixtureSource(args.fixtures)
    base = os.environ.get("CI_SERVER_URL", "https://gitlab.com")
    pid = int(os.environ.get("CI_PROJECT_ID", "0"))
    token = os.environ.get("VERIFIER_READONLY_TOKEN", "")
    return GitLabSource(base, pid, token)


def run_gate(claim: dict, source, catalogue: dict) -> tuple[str, list[dict]]:
    claim_type = claim["type"]
    spec = catalogue["claims"].get(claim_type)
    if spec is None:
        raise SystemExit(f"unknown claim type: {claim_type}")
    tol = catalogue.get("tolerances", {})

    records: list[dict] = []
    overall = PASS
    for check_id in spec["checks"]:
        fn = CHECKS.get(check_id)
        if fn is None:
            rec = {"check_id": check_id, "verdict": INCONCLUSIVE,
                   "reason": "check not implemented"}
            records.append(rec)
            overall = FAIL
            continue
        try:
            record = fn(claim, source, tol)
            rec = record.to_dict()
        except Exception as exc:  # fail closed on any error
            rec = {"check_id": check_id, "verdict": FAIL,
                   "reason": f"check raised: {exc}", "claim_id": claim.get("claim_id", "")}
        records.append(rec)
        v = rec.get("verdict")
        if v == FAIL:
            overall = FAIL
        elif v == INCONCLUSIVE and overall != FAIL:
            overall = INCONCLUSIVE
    return overall, records


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Proof-Carrying Pipeline verifier")
    p.add_argument("--claim", required=True, help="path to claim JSON")
    p.add_argument("--out", default="evidence", help="directory for evidence records")
    p.add_argument("--fixtures", default=None, help="fixture dir (offline mode)")
    args = p.parse_args(argv)

    with open(args.claim) as f:
        claim = json.load(f)
    validate_claim(claim)

    catalogue = load_catalogue()
    source = build_source(args)
    overall, records = run_gate(claim, source, catalogue)

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, f"{claim['type']}.evidence.json")
    with open(out_path, "w") as f:
        json.dump({"claim": claim, "verdict": overall, "records": records}, f, indent=2)

    print(f"\n=== GATE {claim['type']} -> {overall} ===")
    for rec in records:
        tag = ""
        if "claim_mismatch" in rec.get("tags", []):
            tag = "  [CAUGHT LIE]"
        print(f"  [{rec.get('verdict'):13}] {rec.get('check_id'):40} {rec.get('reason','')}{tag}")
    print(f"evidence written to {out_path}\n")

    return 0 if overall == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
