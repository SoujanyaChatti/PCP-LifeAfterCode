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
    def regression_proof(self) -> dict: ...
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
        tests = self._load("regression_tests.json")
        if tests is None:
            # The fail-before/pass-after run also proves which tests exist.
            tests = list(self.regression_proof())
        return tests

    def regression_proof(self) -> dict:
        return self._load("regression_proof.json", {})

    def deploy_digest(self, service: str) -> Optional[str]:
        return self._load("deploy.json", {}).get("digest")

    def health_probes(self, url: str, n: int) -> list[int]:
        return self._load("health.json", {}).get("probes", [])

    def error_rate(self, service: str) -> Optional[float]:
        return self._load("health.json", {}).get("error_rate")


class CompositeSource:
    """Routes each evidence method to the first source that implements it as an
    override, falling back to a base source. Lets a gate read the test report
    from the CI artifact while reading the main baseline from the GitLab API."""

    def __init__(self, base, **overrides):
        self._base = base
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return getattr(self._overrides[name], name)
        return getattr(self._base, name)


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
        """Test count of the latest successful pipeline on main."""
        pls = self._get(f"/projects/{self._project_id}/pipelines"
                        "?ref=main&status=success&per_page=1")
        if not pls:
            return None
        summary = self._get(
            f"/projects/{self._project_id}/pipelines/{pls[0]['id']}/test_report_summary")
        count = summary.get("total", {}).get("count")
        return int(count) if count else None

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

    def regression_proof(self) -> dict:
        return {}

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


class RegistrySource:
    """Reads an image's manifest and config labels straight from the GitLab
    Container Registry (OCI distribution API), so image.built is checked
    against the registry itself, not against what the package job wrote."""

    _ACCEPT = ", ".join([
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ])

    def __init__(self, image_repo: str, job_token: str,
                 gitlab_url: str = "https://gitlab.com"):
        # image_repo: e.g. registry.gitlab.com/group/project
        self._host, _, self._path = image_repo.partition("/")
        self._job_token = job_token
        self._gitlab_url = gitlab_url

    def _client(self):
        import httpx

        auth = httpx.get(
            f"{self._gitlab_url}/jwt/auth",
            params={"service": "container_registry",
                    "scope": f"repository:{self._path}:pull"},
            auth=("gitlab-ci-token", self._job_token), timeout=15.0)
        auth.raise_for_status()
        return httpx.Client(
            base_url=f"https://{self._host}/v2/{self._path}",
            headers={"Authorization": f"Bearer {auth.json()['token']}",
                     "Accept": self._ACCEPT},
            timeout=30.0, follow_redirects=True)

    def registry_image(self, digest: str) -> dict:
        with self._client() as c:
            r = c.get(f"/manifests/{digest}")
            if r.status_code == 404:
                return {}
            r.raise_for_status()
            manifest = r.json()
            if "manifests" in manifest:  # image index: pick linux/amd64
                sub = next((m for m in manifest["manifests"]
                            if m.get("platform", {}).get("architecture") == "amd64"),
                           manifest["manifests"][0])
                manifest = c.get(f"/manifests/{sub['digest']}").json()
            config = c.get(f"/blobs/{manifest['config']['digest']}").json()
        labels = (config.get("config") or {}).get("Labels") or {}
        return {
            "digest": r.headers.get("Docker-Content-Digest", digest),
            "labels": {
                "commit_sha": labels.get("org.opencontainers.image.revision", ""),
                "secure_mode": labels.get("dev.pcp.secure-mode", ""),
                "unpatched": labels.get("dev.pcp.unpatched", ""),
            },
        }


class CloudRunSource:
    """Reads what production actually serves from the Cloud Run admin API and
    the 5xx rate from Cloud Monitoring, using the verifier's own read-only
    identity (see verifier/gcp.py)."""

    def __init__(self, project: str, region: str, token: str):
        import httpx

        self._project, self._region = project, region
        self._client = httpx.Client(headers={"Authorization": f"Bearer {token}"},
                                    timeout=30.0)

    def _run(self, kind: str, name: str) -> dict:
        r = self._client.get(
            f"https://{self._region}-run.googleapis.com/apis/serving.knative.dev/v1/"
            f"namespaces/{self._project}/{kind}/{name}")
        r.raise_for_status()
        return r.json()

    def deploy_digest(self, service: str) -> Optional[str]:
        """Digest of the revision receiving 100% of traffic, or None."""
        svc = self._run("services", service)
        serving = [t for t in svc.get("status", {}).get("traffic", [])
                   if t.get("percent") == 100]
        if not serving:
            return None
        rev = self._run("revisions", serving[0]["revisionName"])
        ref = rev.get("status", {}).get("imageDigest", "")
        return ref.split("@", 1)[1] if "@" in ref else None

    def health_probes(self, url: str, n: int) -> list[int]:
        import httpx

        codes = []
        for _ in range(n):
            try:
                codes.append(httpx.get(f"{url}/healthz", timeout=10.0).status_code)
            except Exception:
                codes.append(0)
        return codes

    def error_rate(self, service: str, window_s: int = 600):
        """5xx share of requests over the window, from Cloud Monitoring."""
        from datetime import datetime, timedelta, timezone

        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=window_s)
        r = self._client.get(
            f"https://monitoring.googleapis.com/v3/projects/{self._project}/timeSeries",
            params={
                "filter": ('metric.type="run.googleapis.com/request_count" AND '
                           f'resource.labels.service_name="{service}"'),
                "interval.startTime": start.isoformat().replace("+00:00", "Z"),
                "interval.endTime": end.isoformat().replace("+00:00", "Z"),
                "aggregation.alignmentPeriod": f"{window_s}s",
                "aggregation.perSeriesAligner": "ALIGN_DELTA",
                "aggregation.crossSeriesReducer": "REDUCE_SUM",
                "aggregation.groupByFields": "metric.labels.response_code_class",
            })
        r.raise_for_status()
        total = errors = 0
        for ts in r.json().get("timeSeries", []):
            cls = ts.get("metric", {}).get("labels", {}).get("response_code_class", "")
            count = sum(int(p["value"].get("int64Value", 0)) for p in ts.get("points", []))
            total += count
            if cls == "5xx":
                errors += count
        if total == 0:
            return None
        return {"rate": errors / total, "requests": total, "source": "cloud_monitoring"}
