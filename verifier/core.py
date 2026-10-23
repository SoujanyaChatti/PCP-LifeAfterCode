"""Core types and helpers for the deterministic verifier.

A check is a pure function: (claim, source, tol) -> EvidenceRecord. It reads
from an authoritative EvidenceSource (GitLab API, registry, live service,
replay result) and returns a signed-off verdict. No check ever reads a file an
agent wrote in the same job, and no check consults an LLM.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_of(obj: Any) -> str:
    """Stable hash of a response object, for reproducibility."""
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"

# Shortest SHA prefix accepted as an identifier (git's own default abbrev).
MIN_SHA_LEN = 7


def sha_matches(a: str | None, b: str | None) -> bool:
    """True only if both SHAs are present, at least MIN_SHA_LEN long, and one
    is a prefix of the other. Fail closed: an empty or too-short SHA never
    matches anything (an empty string is a prefix of every string)."""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if len(a) < MIN_SHA_LEN or len(b) < MIN_SHA_LEN:
        return False
    n = min(len(a), len(b))
    return a[:n] == b[:n]


@dataclasses.dataclass
class EvidenceRecord:
    check_id: str
    claim_id: str
    source: str
    query: str
    response_sha256: str
    observed: Dict[str, Any]
    subject_sha: str
    verdict: str
    reason: str
    checked_at: str = dataclasses.field(default_factory=now_iso)
    subject_digest: str | None = None
    tags: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d


def evidence(
    check_id: str,
    claim: dict,
    *,
    source: str,
    query: str,
    raw_response: Any,
    observed: Dict[str, Any],
    verdict: str,
    reason: str,
    tags: list[str] | None = None,
) -> EvidenceRecord:
    """Build an evidence record, hashing the raw response and binding it to the
    claim's subject SHA/digest."""
    subj = claim.get("subject", {})
    return EvidenceRecord(
        check_id=check_id,
        claim_id=claim.get("claim_id", ""),
        source=source,
        query=query,
        response_sha256=sha256_of(raw_response),
        observed=observed,
        subject_sha=subj.get("commit_sha", ""),
        subject_digest=subj.get("image_digest"),
        verdict=verdict,
        reason=reason,
        tags=tags or [],
    )


# Registry of checks by id.
CHECKS: Dict[str, Callable] = {}


def check(check_id: str):
    def deco(fn: Callable) -> Callable:
        CHECKS[check_id] = fn
        fn.check_id = check_id
        return fn

    return deco
