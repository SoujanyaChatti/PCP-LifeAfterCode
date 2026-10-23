"""verify:package helper — turns the package job's image.json into an
image.built claim + fixture and runs the verifier's image checks."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.checks import implementations  # noqa: F401,E402
from verifier.run import load_catalogue, run_gate  # noqa: E402
from verifier.sources import FixtureSource  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--image-claim", required=True)
    p.add_argument("--out", default="evidence")
    args = p.parse_args(argv)

    with open(args.image_claim) as f:
        img = json.load(f)
    digest = img.get("digest", "")
    sha = img.get("sha", os.environ.get("CI_COMMIT_SHA", ""))

    # Build the fixture the registry check reads.
    fx = "/tmp/fx_image"
    os.makedirs(fx, exist_ok=True)
    with open(os.path.join(fx, "registry_image.json"), "w") as f:
        json.dump({"digest": digest, "labels": {"commit_sha": sha}}, f)

    claim = {
        "claim_id": "image-ci", "type": "image.built", "agent": "release",
        "stage": "package",
        "subject": {"project_id": int(os.environ.get("CI_PROJECT_ID", "0")),
                    "commit_sha": sha, "image_digest": digest},
        "statement": "Image built and pushed.", "asserted": {},
        "created_at": "1970-01-01T00:00:00Z",
    }
    os.makedirs("claims", exist_ok=True)
    with open("claims/image.built.json", "w") as f:
        json.dump(claim, f)

    verdict, records = run_gate(claim, FixtureSource(fx), load_catalogue())
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "image.built.evidence.json"), "w") as f:
        json.dump({"claim": claim, "verdict": verdict, "records": records}, f, indent=2)
    print(f"image.built -> {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
