"""MCP tool: run_check.

Exposes the deterministic verifier as a tool an agent can CALL but whose result
it cannot fabricate — the verdict is computed here, in code, from authoritative
evidence. This is the clean trust boundary the PRD requires: agents request a
check; this server returns the verdict.

Kept dependency-light: if the `mcp` package is installed it registers as a real
MCP server; otherwise the same function is importable/callable directly, and the
CI fallback uses it that way.
"""
from __future__ import annotations

from verifier.checks import implementations  # noqa: F401 (registers checks)
from verifier.run import load_catalogue, run_gate
from verifier.sources import FixtureSource, GitLabSource


def run_check(claim: dict, fixtures: str | None = None) -> dict:
    """Run the full gate for a claim and return {verdict, records}. The caller
    cannot influence the verdict; it only supplies the claim."""
    catalogue = load_catalogue()
    if fixtures:
        source = FixtureSource(fixtures)
    else:
        import os

        source = GitLabSource(
            os.environ.get("CI_SERVER_URL", "https://gitlab.com"),
            int(os.environ.get("CI_PROJECT_ID", "0")),
            os.environ.get("VERIFIER_READONLY_TOKEN", ""),
        )
    verdict, records = run_gate(claim, source, catalogue)
    return {"verdict": verdict, "records": records}


def _serve() -> None:  # pragma: no cover - only runs with mcp installed
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        raise SystemExit(
            "mcp package not installed; run_check is still importable for the CI fallback."
        )
    server = FastMCP("pcp-verifier")
    server.tool()(run_check)
    server.run()


if __name__ == "__main__":
    _serve()
