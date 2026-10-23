"""Render an evidence pack into the Markdown comment the Reporter posts on the
issue. This is the artifact a judge reads first: a green/red line per gate, the
caught-lie callout, the Red-vs-Blue before/after, the verifier's own self-test,
and links to the signed pack and its verification job.

The comment only states what the pack proves: e.g. it says a follow-up issue
was opened only when it is given that issue's URL."""
from __future__ import annotations

import argparse
import json

CHECK = "✅"  # green check
CROSS = "❌"  # red cross
WARN = "⚠️"

TIER_ICON = {"autonomous": "🟢", "probation": "🟡", "suspended": "🔴"}


def _gate_lines(pack: dict) -> list[str]:
    lines = ["### Gate results"]
    for gate, verdict in pack.get("gate_decisions", {}).items():
        icon = CHECK if verdict == "PASS" else CROSS
        recs = pack.get("evidence_by_gate", {}).get(gate, [])
        n_pass = sum(1 for r in recs if r.get("verdict") == "PASS")
        # Lead with the first non-passing reason, else a passing one.
        reason = next((r.get("reason") for r in recs if r.get("verdict") != "PASS"),
                      next((r.get("reason") for r in recs), ""))
        lines.append(f"- {icon} **{gate}** — {verdict} ({n_pass}/{len(recs)} checks): {reason}")
    for gate in pack.get("required_gates", []):
        if gate not in pack.get("gate_decisions", {}) and gate != "verifier.canary":
            lines.append(f"- {CROSS} **{gate}** — no evidence (a gate that never ran did not pass)")
    return lines + [""]


def _lie_lines(pack: dict, followup_url: str) -> list[str]:
    lies = pack.get("caught_false_claims", [])
    if not lies:
        return []
    lines = [f"### {WARN} Caught false claim"]
    for r in lies:
        lines.append(f"- `{r.get('check_id')}`: {r.get('reason')} "
                     f"(observed: `{json.dumps(r.get('observed', {}))}`)")
    lines += ["", "> An agent asserted something the evidence contradicts. The gate "
              "blocked promotion" + (f" and opened a follow-up: {followup_url}" if followup_url
                                     else "."), ""]
    return lines


def _red_blue_lines(pack: dict) -> list[str]:
    rb = pack.get("red_vs_blue", {})
    if not rb.get("exploits_attempted"):
        return []
    lines = ["### Red vs Blue — the same exploit, before and after",
             "| Exploit | Pre-patch | Patched | Regression test fails before / passes after |",
             "| --- | --- | --- | --- |"]
    pre_ev = rb.get("pre_patch_evidence", {})
    replay = rb.get("replay_post_patch", {})
    proof = rb.get("regression_proof", {})
    for eid in rb.get("exploits_attempted", []):
        res = replay.get(eid, {})
        pre = f"{CROSS} {pre_ev[eid]}" if eid in pre_ev else "clean"
        if not res:
            post = "not replayed"
        elif res.get("succeeded"):
            post = f"{CROSS} STILL OPEN"
        else:
            post = f"{CHECK} {res.get('evidence', 'closed')}"
        vuln = (res.get("vuln_id") or "").lower()
        tests = {t: r for t, r in proof.items() if f"::test_{vuln}_" in t} if vuln else {}
        if tests:
            t, r = next(iter(tests.items()))
            ok = r == {"pre_patch": "failed", "post_patch": "passed"}
            disc = f"{CHECK if ok else CROSS} `{t.split('::')[-1]}`"
        else:
            disc = "—"
        lines.append(f"| `{eid}` | {pre} | {post} | {disc} |")
    return lines + [""]


def _integrity_lines(pack: dict) -> list[str]:
    lines = ["### Who verified the verifier"]
    st = pack.get("verifier_self_test")
    if st:
        icon = CHECK if st.get("verifier_alive") else CROSS
        lines.append(f"- {icon} Verifier self-test: planted {st.get('planted')} known lies, "
                     f"caught {st.get('caught')}.")
    else:
        lines.append(f"- {WARN} Verifier self-test did not run.")
    fp = pack.get("verifier_fingerprint")
    if isinstance(fp, str) and fp:
        lines.append(f"- Verifier + playbook + gate wiring fingerprint: `{fp[:16]}…`")
    elif fp:
        lines.append(f"- {CROSS} Gates were judged by {len(fp)} different verifier versions.")
    core = pack.get("trust_core")
    if core:
        if core.get("core_modified"):
            changed = ", ".join(core.get("changed") or []) or "unknown"
            lines.append(f"- {CROSS} Trust-critical core differs from protected main: {changed}")
        else:
            lines.append(f"- {CHECK} Trust-critical core identical to protected main.")
    return lines + [""]


def _trust_lines(trust: dict | None) -> list[str]:
    if not trust:
        return []
    lines = ["### Earned autonomy (agent trust ledger)",
             "| Agent | Verified | Caught lies | Tier |", "| --- | --- | --- | --- |"]
    for agent, a in sorted(trust.get("agents", {}).items()):
        tier = a.get("tier", "?")
        lines.append(f"| {agent} | {a.get('verified', 0)}/{a.get('claims', 0)} | "
                     f"{a.get('lies', 0)} | {TIER_ICON.get(tier, '')} {tier} |")
    return lines + [""]


def render(pack: dict, pack_url: str = "", verify_job_url: str = "",
           followup_url: str = "", trust: dict | None = None) -> str:
    decision = pack.get("decision", "UNKNOWN")
    banner = f"{CHECK} PROMOTED" if decision == "PROMOTED" else f"{CROSS} BLOCKED"
    lines = [f"## Proof-Carrying Pipeline — {banner}", ""]
    run = pack.get("run", {})
    if run:
        lines += [f"Issue #{run.get('issue_iid') or '?'} · MR !{run.get('mr_iid') or '?'} · "
                  f"commit `{str(run.get('commit_sha', ''))[:12]}` · "
                  f"digest `{str(run.get('image_digest', ''))[:19]}`", ""]
    reasons = pack.get("blocked_reasons", [])
    if reasons:
        lines += ["**Why it was blocked:**"] + [f"- {r}" for r in reasons] + [""]
    for notice in pack.get("notices", []):
        lines += [f"> {WARN} {notice}", ""]

    lines += _gate_lines(pack)
    lines += _lie_lines(pack, followup_url)
    lines += _red_blue_lines(pack)
    lines += _integrity_lines(pack)
    lines += _trust_lines(trust)

    lines.append("### Evidence")
    if pack_url:
        lines.append(f"- Signed evidence pack: {pack_url}")
    if verify_job_url:
        lines.append(f"- Signature verified in: {verify_job_url}")
    lines.append(f"- Pack content hash: `{pack.get('content_sha256', '')[:16]}…`")
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
    p.add_argument("--followup-url", default="")
    p.add_argument("--trust", default=None, help="trust summary JSON from trust.ledger")
    p.add_argument("--out", default="issue-comment.md")
    args = p.parse_args(argv)

    with open(args.pack) as f:
        pack = json.load(f)
    trust = None
    if args.trust:
        try:
            with open(args.trust) as f:
                trust = json.load(f)
        except FileNotFoundError:
            trust = None
    md = render(pack, args.pack_url, args.verify_job_url, args.followup_url, trust)
    with open(args.out, "w") as f:
        f.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
