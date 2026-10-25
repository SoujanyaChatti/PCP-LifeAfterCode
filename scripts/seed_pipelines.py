"""Seed visible pipeline history: honest runs, caught lies, incomplete patches.

Creates (or reuses) a planned demo issue, then triggers pipelines on the
default branch through the GitLab API, each with the demo inputs declared at the
top of .gitlab-ci.yml. Every run is judged by the real gates, so the history shows
genuine PROMOTED and BLOCKED outcomes, and the trust ledger / dashboard fill in.

    export GITLAB_TOKEN=glpat-...            # api scope
    python scripts/seed_pipelines.py --project gitlab-ai-hackathon/transcend-october-2026/39453199/showcase --plan default --wait

Plans:
  default  2x human-approved bootstrap (agents start on probation), 3x lie,
           3x incomplete patch (V1, V3, V7), then honest runs until 20 total.
  quick    1 honest, 1 lie, 1 incomplete patch.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.parse

import httpx

ISSUE_BODY = """Add tags to notes so the team can filter search results by tag.

## Acceptance Criteria
- [ ] Notes carry a comma-separated list of tags.
- [ ] Searching for a tag returns the notes that have it.
- [ ] Search input is never interpreted as SQL and never echoed unescaped.
"""

# Pipeline inputs (spec at the top of .gitlab-ci.yml). Inputs, not variables:
# the hackathon workspace doesn't let Developers pass variables to pipelines.
PLANS = {
    "quick": [("honest", {"human_approved": "1"}),
              ("lie", {"demo": "lie"}),
              ("incomplete patch V1", {"unpatched": "V1"})],
}
PLANS["default"] = (
    [("bootstrap (human-approved)", {"human_approved": "1"})] * 2
    + [("lie", {"demo": "lie"})] * 3
    + [(f"incomplete patch {v}", {"unpatched": v}) for v in ("V1", "V3", "V7")]
    + [("honest", {})] * 12
)


class GitLab:
    def __init__(self, url: str, project: str, token: str):
        pid = urllib.parse.quote(project, safe="")
        self.c = httpx.Client(base_url=f"{url}/api/v4/projects/{pid}",
                              headers={"PRIVATE-TOKEN": token}, timeout=30.0)

    def req(self, method: str, path: str, **kw):
        r = self.c.request(method, path, **kw)
        r.raise_for_status()
        return r.json()


def demo_issue(gl: GitLab) -> int:
    for issue in gl.req("GET", "/issues", params={"search": "Add note tagging", "state": "opened"}):
        return issue["iid"]
    issue = gl.req("POST", "/issues", json={"title": "Add note tagging", "description": ISSUE_BODY,
                                            "labels": "risk::low"})
    print(f"created demo issue #{issue['iid']}: {issue['web_url']}")
    return issue["iid"]


def wait(gl: GitLab, pipeline_id: int) -> str:
    while True:
        status = gl.req("GET", f"/pipelines/{pipeline_id}")["status"]
        if status in ("success", "failed", "canceled", "skipped"):
            return status
        time.sleep(20)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--gitlab", default=os.environ.get("CI_SERVER_URL", "https://gitlab.com"))
    p.add_argument("--project", required=True, help="group/project path or numeric id")
    p.add_argument("--ref", default="main")
    p.add_argument("--plan", choices=list(PLANS), default="quick")
    p.add_argument("--wait", action="store_true", help="run one at a time (clean ledger order)")
    args = p.parse_args(argv)

    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        print("set GITLAB_TOKEN (api scope)", file=sys.stderr)
        return 2
    gl = GitLab(args.gitlab, args.project, token)
    iid = demo_issue(gl)

    for i, (label, extra) in enumerate(PLANS[args.plan], 1):
        inputs = {"issue_iid": str(iid), **extra}
        pl = gl.req("POST", "/pipeline", json={"ref": args.ref, "inputs": inputs})
        line = f"[{i:2}] {label:28} pipeline {pl['id']}  {pl['web_url']}"
        if args.wait:
            line += f"  -> {wait(gl, pl['id'])}"
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
