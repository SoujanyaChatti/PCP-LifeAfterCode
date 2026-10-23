"""Shared helper every agent uses to emit its structured claim.

An agent's only trusted output is a claim written here: a JSON file under
claims/ (a CI artifact) plus an MR note carrying a fixed marker. Free-text the
agent writes elsewhere is never used for gating. This keeps the agent/verifier
boundary clean: agents assert, the verifier judges.
"""
from __future__ import annotations

import json
import os
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
    """Render the MR note body for a claim, with the marker the pipeline greps."""
    return (
        f"{CLAIM_MARKER}\n"
        f"**Claim `{claim['type']}`** by `{claim['agent']}`\n\n"
        f"> {claim['statement']}\n\n"
        f"```json\n{json.dumps(claim['asserted'], indent=2)}\n```\n"
    )
