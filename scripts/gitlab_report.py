"""Reporter (CI side): post the evidence comment, open the follow-up issue.

Deterministic — it renders the signed pack; it never decides a verdict.

  - BLOCKED: opens a `pcp::blocked` follow-up issue listing why, linked to the
    original issue, then states that link in the comment.
  - Always: posts the rendered pack on the originating issue and on the MR.

The issue is found from $ISSUE_IID, else "Closes #N" in the MR description or
commit message, else a branch named like feature/issue-N.

    python scripts/gitlab_report.py --pack evidence-pack.json --trust trust.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence_pack.report import render  # noqa: E402


def find_issue_iid() -> int | None:
    if os.environ.get("ISSUE_IID", "").isdigit():
        return int(os.environ["ISSUE_IID"])
    for text in (os.environ.get("CI_MERGE_REQUEST_DESCRIPTION", ""),
                 os.environ.get("CI_COMMIT_MESSAGE", "")):
        m = re.search(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|implements?)\s+#(\d+)", text, re.I)
        if m:
            return int(m.group(1))
    m = re.search(r"issue-(\d+)", os.environ.get("CI_COMMIT_REF_NAME", ""))
    return int(m.group(1)) if m else None


class GitLab:
    def __init__(self, token: str):
        import httpx

        self._c = httpx.Client(
            base_url=f"{os.environ['CI_API_V4_URL']}/projects/{os.environ['CI_PROJECT_ID']}",
            headers={"PRIVATE-TOKEN": token}, timeout=30.0)

    def post(self, path: str, **data) -> dict:
        r = self._c.post(path, json=data)
        r.raise_for_status()
        return r.json()


def followup_body(pack: dict, issue_iid: int | None) -> str:
    lines = [f"Polygraph **blocked** pipeline "
             f"{os.environ.get('CI_PIPELINE_URL', '')}"]
    if issue_iid:
        lines.append(f"for #{issue_iid}")
    lines += ["", "**Why:**"] + [f"- {r}" for r in pack.get("blocked_reasons", [])]
    for lie in pack.get("caught_false_claims", []):
        lines.append(f"- caught false claim `{lie.get('check_id')}`: {lie.get('reason')}")
    lines += ["", "A human needs to look at this: the attempt budget for autonomous "
              "fixes is exhausted or a claim was contradicted by evidence.",
              "", "/label ~\"pcp::blocked\" ~\"needs-human\""]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pack", default="evidence-pack.json")
    p.add_argument("--trust", default=None)
    p.add_argument("--out", default="issue-comment.md")
    args = p.parse_args(argv)

    with open(args.pack) as f:
        pack = json.load(f)
    trust = None
    if args.trust and os.path.exists(args.trust):
        with open(args.trust) as f:
            trust = json.load(f)

    token = os.environ.get("PCP_REPORTER_TOKEN")
    gl = GitLab(token) if token else None
    issue_iid = find_issue_iid()
    followup_url = ""

    if gl and pack.get("decision") != "PROMOTED":
        title = f"[pcp] Blocked: {(pack.get('blocked_reasons') or ['unproven claim'])[0]}"
        issue = gl.post("/issues", title=title[:250],
                        description=followup_body(pack, issue_iid),
                        labels="pcp::blocked,needs-human")
        followup_url = issue["web_url"]
        print(f"opened follow-up issue {followup_url}")

    md = render(pack, pack_url=f"{os.environ.get('CI_JOB_URL', '')}/artifacts/browse",
                verify_job_url=os.environ.get("VERIFY_JOB_URL", ""),
                followup_url=followup_url, trust=trust)
    with open(args.out, "w") as f:
        f.write(md)

    if not gl:
        print("PCP_REPORTER_TOKEN not set; comment rendered to", args.out, "but not posted")
        return 0
    if issue_iid:
        gl.post(f"/issues/{issue_iid}/notes", body=md)
        print(f"posted evidence comment on issue #{issue_iid}")
    mr = os.environ.get("CI_MERGE_REQUEST_IID")
    if mr:
        gl.post(f"/merge_requests/{mr}/notes", body=md)
        print(f"posted evidence comment on MR !{mr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
