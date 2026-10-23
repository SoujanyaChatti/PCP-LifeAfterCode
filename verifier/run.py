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
import json
import os
import sys

import yaml

from verifier.core import CHECKS, FAIL, INCONCLUSIVE, PASS
# Importing the implementations module registers every check.
from verifier.checks import implementations  # noqa: F401
from verifier.fingerprint import fingerprint
from verifier.sources import (CloudRunSource, CompositeSource, FixtureSource,
                              GitLabSource, RegistrySource)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_catalogue() -> dict:
    with open(os.path.join(HERE, "catalogue.yaml")) as f:
        return yaml.safe_load(f)


def validate_claim(claim: dict) -> None:
    # Hard dependency: a verifier that silently skips validation fails open.
    import jsonschema

    with open(os.path.join(ROOT, "schemas", "claim.schema.json")) as f:
        schema = json.load(f)
    jsonschema.validate(claim, schema)


def _gitlab() -> GitLabSource:
    return GitLabSource(os.environ.get("CI_SERVER_URL", "https://gitlab.com"),
                        int(os.environ.get("CI_PROJECT_ID", "0")),
                        os.environ.get("VERIFIER_READONLY_TOKEN", ""))


def _registry() -> RegistrySource:
    return RegistrySource(os.environ["CI_REGISTRY_IMAGE"], os.environ["CI_JOB_TOKEN"],
                          os.environ.get("CI_SERVER_URL", "https://gitlab.com"))


def _cloudrun() -> CloudRunSource:
    from verifier.gcp import access_token

    return CloudRunSource(os.environ["GCP_PROJECT"],
                          os.environ.get("GCP_REGION", "us-central1"),
                          access_token(os.environ["GCP_VERIFIER_SA"]))


# --live NAME -> (factory, evidence methods it is authoritative for)
LIVE_SOURCES = {
    "gitlab": (_gitlab, ["main_test_count"]),
    "registry": (_registry, ["registry_image"]),
    "cloudrun": (_cloudrun, ["deploy_digest", "health_probes", "error_rate"]),
}


def build_source(args) -> object:
    base = FixtureSource(args.fixtures) if args.fixtures else _gitlab()
    overrides = {}
    for name in filter(None, (args.live or "").split(",")):
        factory, methods = LIVE_SOURCES[name]
        src = factory()
        overrides.update({m: src for m in methods})
    return CompositeSource(base, **overrides) if overrides else base


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


def _out_name(claim: dict, same_type_count: int) -> str:
    if same_type_count > 1:
        return f"{claim['type']}.{claim['claim_id']}.evidence.json"
    return f"{claim['type']}.evidence.json"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Proof-Carrying Pipeline verifier")
    p.add_argument("--claim", required=True, action="append",
                   help="path to claim JSON (repeatable, e.g. one vuln.fixed per finding)")
    p.add_argument("--out", default="evidence", help="directory for evidence records")
    p.add_argument("--fixtures", default=None, help="fixture dir (offline mode)")
    p.add_argument("--live", default="",
                   help=f"comma list of live sources: {', '.join(LIVE_SOURCES)}")
    args = p.parse_args(argv)

    claims = []
    for path in args.claim:
        with open(path) as f:
            claim = json.load(f)
        claim.pop("_source", None)
        validate_claim(claim)
        claims.append(claim)

    catalogue = load_catalogue()
    source = build_source(args)
    core = fingerprint()
    os.makedirs(args.out, exist_ok=True)
    types = [c["type"] for c in claims]

    all_pass = True
    for claim in claims:
        overall, records = run_gate(claim, source, catalogue)
        all_pass = all_pass and overall == PASS
        out_path = os.path.join(args.out, _out_name(claim, types.count(claim["type"])))
        with open(out_path, "w") as f:
            json.dump({"claim": claim, "verdict": overall, "records": records,
                       "verifier_fingerprint": core}, f, indent=2)

        print(f"\n=== GATE {claim['type']} ({claim['agent']}) -> {overall} ===")
        for rec in records:
            tag = "  [CAUGHT LIE]" if "claim_mismatch" in rec.get("tags", []) else ""
            print(f"  [{rec.get('verdict'):13}] {rec.get('check_id'):40} "
                  f"{rec.get('reason','')}{tag}")
        print(f"evidence written to {out_path}")

    if not claims:
        print("no claims supplied; failing closed")
        return 1
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
