"""Evidence sources — the only places the verifier reads truth from.

An EvidenceSource exposes narrow, read-only methods over authoritative systems.
Checks call these methods; they never make raw requests themselves. Two
implementations exist: GitLabSource (real, read-only token) and FixtureSource
(records on disk), so every check can be unit-tested against recorded PASS /
FAIL / INCONCLUSIVE responses.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional, Protocol


class EvidenceSource(Protocol):
    def test_report(self, pipeline_id: int) -> dict: ...
    def coverage(self, pipeline_id: int) -> Optional[float]: ...
    def main_test_count(self) -> Optional[int]: ...
    def pipeline(self, pipeline_id: int) -> dict: ...
    def mr(self, mr_iid: int) -> dict: ...
    def mr_ci_diff(self, mr_iid: int) -> str: ...
    def issue(self, issue_iid: int) -> dict: ...
    def registry_image(self, digest: str) -> dict: ...
    def replay_result(self, exploit_id: str) -> dict: ...
    def regression_tests(self) -> list[str]: ...
    def deploy_digest(self, service: str) -> Optional[str]: ...
    def health_probes(self, url: str, n: int) -> list[int]: ...
    def error_rate(self, service: str) -> Optional[float]: ...


class FixtureSource:
    """Reads recorded evidence from a directory of JSON files. Used in tests
    and in the e2e rehearsal so runs are deterministic and offline."""

    def __init__(self, root: str):
        self.root = root

    def _load(self, name: str, default: Any = None) -> Any:
        path = os.path.join(self.root, name)
        if not os.path.exists(path):
            return default
        with open(path) as f:
            return json.load(f)

    def test_report(self, pipeline_id: int) -> dict:
        return self._load("test_report.json", {})

    def coverage(self, pipeline_id: int) -> Optional[float]:
        return self._load("coverage.json", {}).get("coverage_pct")

    def main_test_count(self) -> Optional[int]:
        return self._load("main_baseline.json", {}).get("test_count")

    def pipeline(self, pipeline_id: int) -> dict:
        return self._load("pipeline.json", {})

    def mr(self, mr_iid: int) -> dict:
        return self._load("mr.json", {})

    def mr_ci_diff(self, mr_iid: int) -> str:
        return self._load("mr_ci_diff.json", {}).get("diff", "")

    def issue(self, issue_iid: int) -> dict:
        return self._load("issue.json", {})

    def registry_image(self, digest: str) -> dict:
        return self._load("registry_image.json", {})

    def replay_result(self, exploit_id: str) -> dict:
        results = self._load("replay_results.json", {})
        return results.get(exploit_id, {})

    def regression_tests(self) -> list[str]:
        return self._load("regression_tests.json", [])

    def deploy_digest(self, service: str) -> Optional[str]:
        return self._load("deploy.json", {}).get("digest")

    def health_probes(self, url: str, n: int) -> list[int]:
        return self._load("health.json", {}).get("probes", [])

    def error_rate(self, service: str) -> Optional[float]:
        return self._load("health.json", {}).get("error_rate")


# A thin real GitLab source. Networked methods are kept minimal and read-only;
# in the hackathon the CI job supplies most evidence as fixtures/artifacts, and
# this class fills the gaps where a live API call is cheap and authoritative.
class GitLabSource:
    def __init__(self, base_url: str, project_id: int, token: str):
        import httpx

        self._project_id = project_id
        self._client = httpx.Client(
            base_url=f"{base_url}/api/v4",
            headers={"PRIVATE-TOKEN": token},
            timeout=15.0,
        )

    def _get(self, path: str) -> Any:
        r = self._client.get(path)
        r.raise_for_status()
        return r.json()

    def test_report(self, pipeline_id: int) -> dict:
        return self._get(f"/projects/{self._project_id}/pipelines/{pipeline_id}/test_report")

    def coverage(self, pipeline_id: int) -> Optional[float]:
        data = self._get(f"/projects/{self._project_id}/pipelines/{pipeline_id}")
        return data.get("coverage")

    def main_test_count(self) -> Optional[int]:
        return None  # supplied via fixture baseline in CI

    def pipeline(self, pipeline_id: int) -> dict:
        return self._get(f"/projects/{self._project_id}/pipelines/{pipeline_id}")

    def mr(self, mr_iid: int) -> dict:
        return self._get(f"/projects/{self._project_id}/merge_requests/{mr_iid}")

    def mr_ci_diff(self, mr_iid: int) -> str:
        changes = self._get(
            f"/projects/{self._project_id}/merge_requests/{mr_iid}/changes"
        )
        for ch in changes.get("changes", []):
            if ch.get("new_path") == ".gitlab-ci.yml":
                return ch.get("diff", "")
        return ""

    def issue(self, issue_iid: int) -> dict:
        return self._get(f"/projects/{self._project_id}/issues/{issue_iid}")

    def registry_image(self, digest: str) -> dict:
        return {}  # supplied via fixture/artifact in CI

    def replay_result(self, exploit_id: str) -> dict:
        return {}

    def regression_tests(self) -> list[str]:
        return []

    def deploy_digest(self, service: str) -> Optional[str]:
        return None

    def health_probes(self, url: str, n: int) -> list[int]:
        import httpx

        codes = []
        for _ in range(n):
            try:
                codes.append(httpx.get(f"{url}/healthz", timeout=10.0).status_code)
            except Exception:
                codes.append(0)
        return codes

    def error_rate(self, service: str) -> Optional[float]:
        return None
