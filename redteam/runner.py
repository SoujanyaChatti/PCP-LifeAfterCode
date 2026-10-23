"""Exploit runner — the safety-gated harness for the white-hat security probes.

Every probe is a small function returning an ExploitResult. The runner refuses
to target any host that is not the current MR's own ephemeral staging URL,
passed in explicitly. This is the single choke point that enforces the PRD's
safety rail: probes run only against our own throwaway staging deployment with
synthetic data, never production and never a third-party host.
"""
from __future__ import annotations

import dataclasses
import os
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx


@dataclasses.dataclass
class ExploitResult:
    exploit_id: str
    title: str
    succeeded: bool  # True == the weakness is present and reachable
    evidence: str    # short, human-readable proof string
    vuln_id: str = ""  # e.g. "V1"


class HostNotAllowed(Exception):
    """Raised when a probe is pointed at anything but the allowed staging URL."""


def _normalize(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}".rstrip("/")


def assert_allowed(target: str, allowed: Optional[str]) -> None:
    """Fail closed: the target must exactly match the allowed staging origin."""
    allowed = allowed or os.environ.get("STAGING_URL", "")
    if not allowed:
        raise HostNotAllowed("no STAGING_URL configured; refusing to run")
    if _normalize(target) != _normalize(allowed):
        raise HostNotAllowed(
            f"target {target!r} is not the allowed staging origin {allowed!r}"
        )


def client_for(target: str, allowed: Optional[str] = None) -> httpx.Client:
    assert_allowed(target, allowed)
    return httpx.Client(base_url=_normalize(target), timeout=10.0)


# Registry of all probes, filled by @register.
PLAYBOOK: dict[str, Callable[[str, Optional[str]], ExploitResult]] = {}


def register(fn: Callable[[str, Optional[str]], ExploitResult]):
    PLAYBOOK[fn.__name__] = fn
    return fn
