"""Bind the security gate to THIS commit's images before attacking anything.

Staging services are reused across pipelines of the same MR, so a newer or
older pipeline may have redeployed them. Each image bakes its commit SHA and
mode into /health; this refuses to proceed unless the twin and the hardened
build both report the commit under test and the expected mode.

    python -m redteam.check_targets --pre $STAGING_URL_PRE --post $STAGING_URL
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import httpx


def healthz(url: str, attempts: int = 10) -> dict:
    for i in range(attempts):
        try:
            r = httpx.get(f"{url}/health", timeout=10.0)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(min(2 ** i, 10))
    return {}


def check(url: str, commit: str, secure: bool) -> str | None:
    h = healthz(url)
    if not h:
        return f"{url} is not answering /health"
    if h.get("version") != commit:
        return f"{url} serves commit {h.get('version')!r}, not {commit!r} (stale or racing deploy)"
    if bool(h.get("secure_mode")) != secure:
        return f"{url} secure_mode={h.get('secure_mode')}, expected {secure}"
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pre", default="")
    p.add_argument("--post", default="")
    p.add_argument("--commit", default=os.environ.get("CI_COMMIT_SHA", ""))
    args = p.parse_args(argv)

    problems = []
    for url, secure in ((args.pre, False), (args.post, True)):
        if url:
            err = check(url, args.commit, secure)
            if err:
                problems.append(err)
            else:
                print(f"{url}: serves {args.commit[:12]} ({'hardened' if secure else 'pre-patch twin'})")
    for err in problems:
        print(f"REFUSING: {err}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
