"""Agent trust ledger — autonomy is earned, one verified claim at a time.

Every pipeline appends one entry: the run, its decision, the signed pack's
hash, and every claim the verifier judged (agent, type, verdict, caught lie).
Entries are hash-chained (each carries the previous entry's hash), so editing
or deleting history breaks the chain and `verify` reports where.

From that history each agent gets a tier (thresholds in catalogue.yaml):

  autonomous  claims keep verifying; may promote to production unattended
  probation   a caught lie in recent runs, or not enough history yet;
              the next production deploy needs a human (PCP_HUMAN_APPROVED=1)
  suspended   repeated caught lies; production is blocked for this agent

    python -m verifier.ledger append  --ledger ledger.json --pack evidence-pack.json
    python -m verifier.ledger summary --ledger ledger.json --out trust.json
    python -m verifier.ledger policy  --ledger ledger.json --agents builder,blueteam
    python -m verifier.ledger verify  --ledger ledger.json
    python -m verifier.ledger pull|push --ledger ledger.json   # CI package registry
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import yaml

GENESIS = "0" * 64
HERE = os.path.dirname(os.path.abspath(__file__))


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _entry_hash(entry: dict) -> str:
    return hashlib.sha256(_canonical({k: v for k, v in entry.items() if k != "hash"})).hexdigest()


def trust_config() -> dict:
    with open(os.path.join(HERE, "catalogue.yaml")) as f:
        cfg = yaml.safe_load(f).get("trust", {})
    return {"window_runs": 20, "min_verified": 2, "probation_lookback": 5,
            "suspend_lies": 3, **cfg}


def load(path: str) -> dict:
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"schema": "pcp/ledger/v1", "entries": []}


def save(ledger: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(ledger, f, indent=2)


def entry_from_pack(pack: dict) -> dict:
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "run": {k: pack.get("run", {}).get(k) for k in
                ("pipeline_id", "issue_iid", "mr_iid", "commit_sha", "image_digest", "ref")},
        "decision": pack.get("decision"),
        "pack_sha256": pack.get("content_sha256"),
        "verifier_fingerprint": pack.get("verifier_fingerprint"),
        "claims": [{k: c.get(k) for k in ("agent", "type", "verdict", "lie", "origin")}
                   for c in pack.get("judged_claims", [])],
    }


def append(ledger: dict, entry: dict) -> dict:
    entries = ledger.setdefault("entries", [])
    entry = dict(entry, prev_hash=entries[-1]["hash"] if entries else GENESIS)
    entry["hash"] = _entry_hash(entry)
    entries.append(entry)
    return entry


def verify_chain(ledger: dict) -> tuple[bool, int | None]:
    """(ok, index of the first broken entry)."""
    prev = GENESIS
    for i, e in enumerate(ledger.get("entries", [])):
        if e.get("prev_hash") != prev or e.get("hash") != _entry_hash(e):
            return False, i
        prev = e["hash"]
    return True, None


def summarize(ledger: dict, cfg: dict | None = None) -> dict:
    cfg = cfg or trust_config()
    ok, broken_at = verify_chain(ledger)
    entries = ledger.get("entries", [])[-cfg["window_runs"]:]
    agents: dict[str, dict] = {}
    for idx, e in enumerate(entries):
        recent = idx >= len(entries) - cfg["probation_lookback"]
        for c in e.get("claims", []):
            a = agents.setdefault(c["agent"], {"claims": 0, "verified": 0, "lies": 0,
                                               "recent_lies": 0})
            a["claims"] += 1
            a["verified"] += c.get("verdict") == "PASS"
            if c.get("lie"):
                a["lies"] += 1
                a["recent_lies"] += recent
    for a in agents.values():
        if a["lies"] >= cfg["suspend_lies"]:
            a["tier"], a["why"] = "suspended", f"{a['lies']} caught lies in the last {len(entries)} runs"
        elif a["recent_lies"]:
            a["tier"], a["why"] = "probation", "caught lying in a recent run"
        elif a["verified"] < cfg["min_verified"]:
            a["tier"], a["why"] = "probation", (f"only {a['verified']} verified claim(s); "
                                                f"{cfg['min_verified']} needed")
        else:
            a["tier"], a["why"] = "autonomous", "claims keep verifying"
        a["score"] = round(a["verified"] / a["claims"], 3) if a["claims"] else 0.0
    return {"chain_ok": ok, "chain_broken_at": broken_at, "runs": len(ledger.get("entries", [])),
            "head": ledger["entries"][-1]["hash"] if ledger.get("entries") else GENESIS,
            "agents": agents}


def policy(summary: dict, agents: list[str], human_approved: bool) -> tuple[bool, list[str]]:
    """May this run promote to production without a human?"""
    reasons = []
    if not summary["chain_ok"]:
        reasons.append(f"ledger hash chain broken at entry {summary['chain_broken_at']}")
    for name in agents:
        a = summary["agents"].get(name)
        tier = a["tier"] if a else "probation"
        why = a["why"] if a else "no history yet"
        if tier == "suspended":
            reasons.append(f"{name} is suspended ({why})")
        elif tier == "probation" and not human_approved:
            reasons.append(f"{name} is on probation ({why}); set PCP_HUMAN_APPROVED=1 to approve")
    return not reasons, reasons


# --- CI storage: the project's generic package registry -------------------
def _registry_url() -> str:
    return (f"{os.environ['CI_API_V4_URL']}/projects/{os.environ['CI_PROJECT_ID']}"
            "/packages/generic/pcp-ledger/1.0.0/ledger.json")


def pull(path: str) -> None:
    import httpx

    r = httpx.get(_registry_url(), headers={"JOB-TOKEN": os.environ["CI_JOB_TOKEN"]},
                  timeout=30.0, follow_redirects=True)
    if r.status_code == 404:
        print("no ledger yet; starting a new chain")
        return
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)


def push(path: str) -> None:
    import httpx

    with open(path, "rb") as f:
        r = httpx.put(_registry_url(), content=f.read(),
                      headers={"JOB-TOKEN": os.environ["CI_JOB_TOKEN"]}, timeout=30.0)
    r.raise_for_status()


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["append", "summary", "policy", "verify", "pull", "push"])
    p.add_argument("--ledger", default="ledger.json")
    p.add_argument("--pack", default="evidence-pack.json")
    p.add_argument("--agents", default="")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    if args.cmd == "pull":
        pull(args.ledger)
        return 0
    if args.cmd == "push":
        push(args.ledger)
        return 0

    ledger = load(args.ledger)
    if args.cmd == "append":
        with open(args.pack) as f:
            entry = append(ledger, entry_from_pack(json.load(f)))
        save(ledger, args.ledger)
        print(f"ledger entry {len(ledger['entries'])}: {entry['decision']} {entry['hash'][:16]}")
        return 0

    summary = summarize(ledger)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(summary, f, indent=2)
    if args.cmd == "verify":
        print("ledger chain OK" if summary["chain_ok"]
              else f"LEDGER TAMPERED: chain broken at entry {summary['chain_broken_at']}")
        return 0 if summary["chain_ok"] else 1
    if args.cmd == "summary":
        print(json.dumps(summary, indent=2))
        return 0

    agents = [a for a in args.agents.split(",") if a]
    ok, reasons = policy(summary, agents, os.environ.get("PCP_HUMAN_APPROVED") == "1")
    for name in agents:
        a = summary["agents"].get(name, {})
        print(f"  {name:10} tier={a.get('tier', 'probation'):10} {a.get('why', 'no history yet')}")
    if ok:
        print("Autonomy policy: PASS — every agent in this run has earned unattended promotion.")
        return 0
    print("Autonomy policy: HOLD —\n  " + "\n  ".join(reasons))
    return 1


if __name__ == "__main__":
    sys.exit(main())
