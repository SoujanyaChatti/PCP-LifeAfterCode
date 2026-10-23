"""Print the run metadata block for the evidence pack as JSON (built in Python
so values are always correctly quoted)."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.gitlab_report import find_issue_iid  # noqa: E402


def _int(name: str):
    v = os.environ.get(name, "")
    return int(v) if v.isdigit() else None


def main() -> int:
    images = {}
    if os.path.exists("build/images.json"):
        with open("build/images.json") as f:
            images = json.load(f)
    print(json.dumps({
        "project": os.environ.get("CI_PROJECT_PATH"),
        "kind": os.environ.get("PCP_KIND"),
        "ref": os.environ.get("CI_COMMIT_REF_NAME"),
        "issue_iid": find_issue_iid(),
        "mr_iid": _int("CI_MERGE_REQUEST_IID"),
        "pipeline_id": _int("CI_PIPELINE_ID"),
        "pipeline_url": os.environ.get("CI_PIPELINE_URL"),
        "commit_sha": os.environ.get("CI_COMMIT_SHA"),
        "image_digest": images.get("digest"),
        "twin_digest": images.get("twin_digest"),
        "demo": {"lie": os.environ.get("PCP_DEMO") == "lie",
                 "unpatched": os.environ.get("PCP_UNPATCHED") or None},
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
