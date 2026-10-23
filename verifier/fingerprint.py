"""Fingerprint of the trust-critical core — "who verified the verifier?"

Every evidence record and every evidence pack carries the SHA-256 of the exact
verifier, exploit playbook, schemas, flows and gate wiring that produced it.
`python -m verifier.fingerprint --against origin/main` exits non-zero when the
current checkout's core differs from the protected branch, so a run graded by
a modified grader is flagged instead of trusted.

    python -m verifier.fingerprint                      # print fingerprint
    python -m verifier.fingerprint --against origin/main --out core.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Must mirror the CODEOWNERS-protected paths (tests/test_ci.py checks this).
TRUST_CORE = ["verifier", "redteam", "schemas", ".gitlab-ci.yml", "ci", "scripts",
              "evidence_pack", "infra", "pytest.ini", "tests/conftest.py", "agents", "duo",
              ".gitlab/duo", "CODEOWNERS"]


def _core_files(root: str) -> list[str]:
    files = []
    for entry in TRUST_CORE:
        path = os.path.join(root, entry)
        if os.path.isfile(path):
            files.append(entry)
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            # Skip caches and local tool state (e.g. infra/.terraform, tfstate).
            dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith(".")]
            for name in filenames:
                if name.endswith((".pyc", ".pyo", ".tfstate", ".tfstate.backup")):
                    continue
                files.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(files)


def fingerprint(root: str = ROOT) -> str:
    h = hashlib.sha256()
    for rel in _core_files(root):
        with open(os.path.join(root, rel), "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        h.update(f"{rel}\0{digest}\n".encode())
    return h.hexdigest()


def changed_against(ref: str, root: str = ROOT) -> list[str] | None:
    """Trust-core paths that differ from `ref`, or None if git can't tell."""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", ref, "--", *TRUST_CORE],
            cwd=root, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line for line in out.splitlines() if line]


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--against", default=None, help="git ref of the protected core")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    result = {"fingerprint": fingerprint(), "paths": TRUST_CORE}
    rc = 0
    if args.against:
        changed = changed_against(args.against)
        result["against"] = args.against
        result["changed"] = changed
        # Fail closed: unknown counts as modified.
        result["core_modified"] = changed is None or bool(changed)
        rc = 1 if result["core_modified"] else 0
    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
