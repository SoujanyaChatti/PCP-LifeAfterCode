"""Fail-before / pass-after proof for the Blue Team's regression tests.

A regression test is itself a claim ("this test detects the weakness"), so it
gets verified like one: the verifier runs every regression test against the
pre-patch twin, where it must FAIL, and against the patched build, where it
must PASS. A test that passes on both (e.g. `assert True`) is rejected.

    # against the deployed staging services (CI)
    python -m redteam.prove_regressions --pre-url $STAGING_URL_PRE \
        --post-url $STAGING_URL --out evidence/redteam
    # in-process (offline rehearsal / unit tests)
    python -m redteam.prove_regressions --in-process --out evidence/redteam

Writes regression_proof.json: {test_id: {"pre_patch": ..., "post_patch": ...}}
where each outcome is "passed", "failed" or "error".
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGRESSIONS = "tests/test_regressions.py"


def _run(env_overrides: dict) -> dict:
    """Run the regression module once; return {test_id: outcome}."""
    fd, report = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    env = {k: v for k, v in os.environ.items()
           if k not in ("PCP_TARGET_URL", "PCP_TARGET_BUILD")}
    env.update(env_overrides)
    subprocess.run(
        [sys.executable, "-m", "pytest", REGRESSIONS, f"--junitxml={report}",
         "-q", "-p", "no:cacheprovider", "-o", "addopts="],
        cwd=ROOT, env=env, capture_output=True)
    outcomes = {}
    try:
        for case in ET.parse(report).getroot().iter("testcase"):
            test_id = f"{REGRESSIONS}::{case.get('name')}"
            if case.find("failure") is not None:
                outcomes[test_id] = "failed"
            elif case.find("error") is not None or case.find("skipped") is not None:
                outcomes[test_id] = "error"
            else:
                outcomes[test_id] = "passed"
    except (ET.ParseError, FileNotFoundError):
        pass
    finally:
        if os.path.exists(report):
            os.remove(report)
    return outcomes


def prove(pre_env: dict, post_env: dict) -> dict:
    pre, post = _run(pre_env), _run(post_env)
    return {t: {"pre_patch": pre.get(t, "error"), "post_patch": post.get(t, "error")}
            for t in sorted(set(pre) | set(post))}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pre-url", default=os.environ.get("STAGING_URL_PRE", ""))
    p.add_argument("--post-url", default=os.environ.get("STAGING_URL", ""))
    p.add_argument("--in-process", action="store_true")
    p.add_argument("--out", default="evidence/redteam")
    args = p.parse_args(argv)

    if args.in_process:
        proof = prove({"PCP_TARGET_BUILD": "pre_patch"}, {"PCP_TARGET_BUILD": "patched"})
    else:
        if not (args.pre_url and args.post_url):
            print("ERROR: need --pre-url and --post-url (fail closed).", file=sys.stderr)
            return 2
        proof = prove({"PCP_TARGET_URL": args.pre_url}, {"PCP_TARGET_URL": args.post_url})

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "regression_proof.json"), "w") as f:
        json.dump(proof, f, indent=2)

    weak = [t for t, r in proof.items() if r != {"pre_patch": "failed", "post_patch": "passed"}]
    print(f"Fail-before/pass-after proof for {len(proof)} regression test(s):")
    for t, r in proof.items():
        ok = "proven " if t not in weak else "WEAK   "
        print(f"  [{ok}] {t}: pre-patch {r['pre_patch']}, patched {r['post_patch']}")
    return 1 if (weak or not proof) else 0


if __name__ == "__main__":
    sys.exit(main())
