"""Shared helper every agent uses to emit its structured claim.

An agent's only trusted output is a claim written here: a JSON file under
claims/ (a CI artifact) plus an MR note carrying a fixed marker. Free-text the
agent writes elsewhere is never used for gating. This keeps the agent/verifier
boundary clean: agents assert, the verifier judges.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

CLAIM_MARKER = "<!-- pcp-claim -->"


def _claims_dir() -> str:
    # Read at call time so callers can set PCP_CLAIMS_DIR after import.
    return os.environ.get("PCP_CLAIMS_DIR", "claims")


def emit_claim(
    *,
    claim_type: str,
    agent: str,
    stage: str,
    subject: dict,
    statement: str,
    asserted: dict,
) -> dict:
    """Build, validate-shape and persist a claim. Returns the claim dict."""
    claim = {
        "claim_id": str(uuid.uuid4()),
        "type": claim_type,
        "agent": agent,
        "stage": stage,
        "subject": subject,
        "statement": statement,
        "asserted": asserted,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    claims_dir = _claims_dir()
    os.makedirs(claims_dir, exist_ok=True)
    path = os.path.join(claims_dir, f"{claim_type}.json")
    with open(path, "w") as f:
        json.dump(claim, f, indent=2)
    return claim


def claim_note_body(claim: dict) -> str:
    """Render the MR note body for a claim, with the marker the pipeline greps.
    The full claim is embedded so the pipeline can read it back verbatim."""
    return (
        f"{CLAIM_MARKER}\n"
        f"**Claim `{claim['type']}`** by `{claim['agent']}`\n\n"
        f"> {claim['statement']}\n\n"
        f"```json\n{json.dumps(claim, indent=2)}\n```\n"
    )


_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def parse_claim_notes(notes: list[dict]) -> list[dict]:
    """Extract claims from MR notes carrying the marker, oldest first. Notes
    without the marker, or whose JSON is not a full claim, are ignored."""
    claims = []
    for note in sorted(notes, key=lambda n: n.get("created_at", "")):
        body = note.get("body", "")
        if CLAIM_MARKER not in body:
            continue
        m = _JSON_BLOCK.search(body)
        if not m:
            continue
        try:
            claim = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(claim, dict) and {"type", "agent", "claim_id"} <= claim.keys():
            claim.setdefault("_source", {"mr_note_id": note.get("id"),
                                         "author": note.get("author", {}).get("username")})
            claims.append(claim)
    return claims
