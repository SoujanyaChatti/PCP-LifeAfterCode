"""Render an evidence pack into the Markdown comment the Reporter posts on the
issue. This is the artifact a judge reads first: a green/red line per gate, the
caught-lie callout, the Red-vs-Blue before/after, and links to the signed pack
and its verification job."""
from __future__ import annotations

import argparse
import json

CHECK = "✅"  # green check
CROSS = "❌"  # red cross
WARN = "⚠️"


def render(pack: dict, pack_url: str = "", verify_job_url: str = "") -> str:
    lines = []
    decision = pack.get("decision", "UNKNOWN")
    banner = f"{CHECK} PROMOTED" if decision == "PROMOTED" else f"{CROSS} BLOCKED"
    lines.append(f"## Proof-Carrying Pipeline — {banner}")
    lines.append("")
    run = pack.get("run", {})
    if run:
        lines.append(
            f"Issue #{run.get('issue_iid','?')} · MR !{run.get('mr_iid','?')} · "
            f"commit `{str(run.get('commit_sha',''))[:12]}` · "
            f"digest `{str(run.get('image_digest',''))[:19]}`"
        )
        lines.append("")

    lines.append("### Gate results")
    for gate, verdict in pack.get("gate_decisions", {}).items():
        icon = CHECK if verdict == "PASS" else CROSS
        # pull a representative reason
        recs = pack.get("evidence_by_gate", {}).get(gate, [])
        reason = next((r.get("reason") for r in recs if r.get("verdict") == verdict), "")
        lines.append(f"- {icon} **{gate}** — {verdict}: {reason}")
    lines.append("")

    lies = pack.get("caught_false_claims", [])
    if lies:
        lines.append(f"### {WARN} Caught false claim")
        for r in lies:
            obs = r.get("observed", {})
            lines.append(
                f"- `{r.get('check_id')}`: {r.get('reason')} "
                f"(observed: `{json.dumps(obs)}`)"
            )
        lines.append("")
        lines.append("> An agent asserted a number the evidence contradicts. "
                     "The gate blocked promotion and a follow-up issue was opened.")
        lines.append("")

    rb = pack.get("red_vs_blue", {})
    if rb.get("succeeded_pre_patch"):
        lines.append("### Red vs Blue")
        lines.append(f"- Exploits that succeeded pre-patch: "
                     f"`{', '.join(rb['succeeded_pre_patch'])}`")
        replay = rb.get("replay_post_patch", {})
        for eid, res in replay.items():
            icon = CROSS if res.get("succeeded") else CHECK
            state = "STILL OPEN" if res.get("succeeded") else "closed after patch"
            lines.append(f"  - {icon} `{eid}` — {state}")
        lines.append("")

    lines.append("### Evidence")
    if pack_url:
        lines.append(f"- Signed evidence pack: {pack_url}")
    if verify_job_url:
        lines.append(f"- Signature verified in: {verify_job_url}")
    lines.append(f"- Pack content hash: `{pack.get('content_sha256','')[:16]}…`")
    lines.append("")
    lines.append("_Every line above is backed by a machine-checkable evidence "
                 "record. The verifier is deterministic code under CODEOWNERS; "
                 "no agent can edit it or decide a verdict._")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pack", default="evidence-pack.json")
    p.add_argument("--pack-url", default="")
    p.add_argument("--verify-job-url", default="")
    p.add_argument("--out", default="issue-comment.md")
    args = p.parse_args(argv)

    with open(args.pack) as f:
        pack = json.load(f)
    md = render(pack, args.pack_url, args.verify_job_url)
    with open(args.out, "w") as f:
        f.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
