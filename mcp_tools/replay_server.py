"""MCP tool: replay_exploit.

Re-runs a single pinned white-hat probe against the current MR's own staging
URL and returns its structured result. Agents (e.g. Blue Team) may request a
replay, but the pinned exploit scripts are under CODEOWNERS and cannot be edited
by any agent — so a replay is honest evidence, not something an agent can fake.
The host guard refuses any target but the configured STAGING_URL.
"""
from __future__ import annotations

import os

from redteam.playbook import exploits  # noqa: F401 (registers probes)
from redteam.runner import PLAYBOOK


def replay_exploit(exploit_id: str, staging_url: str | None = None) -> dict:
    target = staging_url or os.environ.get("STAGING_URL", "")
    probe = PLAYBOOK.get(exploit_id)
    if probe is None:
        return {"exploit_id": exploit_id, "error": "unknown exploit id"}
    result = probe(target, target)
    return {
        "exploit_id": result.exploit_id,
        "vuln_id": result.vuln_id,
        "succeeded": result.succeeded,
        "evidence": result.evidence,
    }


def replay_all(staging_url: str | None = None, only: list[str] | None = None) -> dict:
    """Replay every probe (or a subset), returning id -> result."""
    target = staging_url or os.environ.get("STAGING_URL", "")
    ids = only or list(PLAYBOOK.keys())
    out = {}
    for eid in ids:
        out[eid] = replay_exploit(eid, target)
    return out


def _serve() -> None:  # pragma: no cover
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise SystemExit("mcp package not installed; replay_exploit is still importable.")
    server = FastMCP("pcp-replay")
    server.tool()(replay_exploit)
    server.run()


if __name__ == "__main__":
    _serve()
