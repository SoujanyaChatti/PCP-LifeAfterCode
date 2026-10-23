"""Live evidence sources (GitLab API, container registry, Cloud Run, Cloud
Monitoring, GCP auth) against mocked HTTP, plus the verifier CLI wiring."""
import json
import os

import httpx
import pytest

from verifier import gcp, run as vrun
from verifier.sources import (CloudRunSource, CompositeSource, FixtureSource,
                              GitLabSource, RegistrySource)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
_RealClient = httpx.Client


def _mock_client(monkeypatch, handler):
    def factory(*args, **kwargs):
        return _RealClient(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_gitlab_source_reads_api(monkeypatch):
    def handler(req):
        p = req.url.path
        if p.endswith("/pipelines") and req.url.params.get("ref") == "main":
            return httpx.Response(200, json=[{"id": 77}])
        if p.endswith("/pipelines/77/test_report_summary"):
            return httpx.Response(200, json={"total": {"count": 140}})
        if p.endswith("/merge_requests/3/changes"):
            return httpx.Response(200, json={"changes": [
                {"new_path": "app/x.py", "diff": "+x"},
                {"new_path": ".gitlab-ci.yml", "diff": "+  allow_failure: true"}]})
        if p.endswith("/merge_requests/3"):
            return httpx.Response(200, json={"iid": 3, "sha": "abc"})
        if p.endswith("/issues/9"):
            return httpx.Response(200, json={"iid": 9})
        if p.endswith("/pipelines/5/test_report"):
            return httpx.Response(200, json={"total_count": 1})
        if p.endswith("/pipelines/5"):
            return httpx.Response(200, json={"status": "success", "coverage": "81.5"})
        return httpx.Response(404)

    _mock_client(monkeypatch, handler)
    src = GitLabSource("https://gl", 1, "tok")
    assert src.main_test_count() == 140
    assert "allow_failure" in src.mr_ci_diff(3)
    assert src.mr(3)["sha"] == "abc" and src.issue(9)["iid"] == 9
    assert src.test_report(5)["total_count"] == 1
    assert src.pipeline(5)["status"] == "success" and src.coverage(5) == "81.5"
    assert src.registry_image("d") == {} and src.replay_result("x") == {}
    assert src.regression_tests() == [] and src.regression_proof() == {}
    assert src.deploy_digest("p") is None and src.error_rate("p") is None


def test_gitlab_source_no_main_baseline(monkeypatch):
    _mock_client(monkeypatch, lambda req: httpx.Response(200, json=[]))
    assert GitLabSource("https://gl", 1, "tok").main_test_count() is None


def test_registry_source_reads_labels_from_manifest_and_config(monkeypatch):
    digest = "sha256:" + "1" * 64

    def auth(url, params, auth, timeout):
        assert params["scope"] == "repository:grp/proj:pull"
        return httpx.Response(200, json={"token": "jwt"}, request=httpx.Request("GET", url))

    def handler(req):
        assert req.headers["Authorization"] == "Bearer jwt"
        p = req.url.path
        if p.endswith(f"/manifests/{digest}"):
            return httpx.Response(200, headers={"Docker-Content-Digest": digest}, json={
                "manifests": [{"digest": "sha256:arm", "platform": {"architecture": "arm64"}},
                              {"digest": "sha256:amd", "platform": {"architecture": "amd64"}}]})
        if p.endswith("/manifests/sha256:amd"):
            return httpx.Response(200, json={"config": {"digest": "sha256:cfg"}})
        if p.endswith("/blobs/sha256:cfg"):
            return httpx.Response(200, json={"config": {"Labels": {
                "org.opencontainers.image.revision": "abc1234567",
                "dev.pcp.secure-mode": "1", "dev.pcp.unpatched": ""}}})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "get", auth)
    _mock_client(monkeypatch, handler)
    img = RegistrySource("registry.gitlab.com/grp/proj", "jobtok").registry_image(digest)
    assert img == {"digest": digest, "labels": {"commit_sha": "abc1234567",
                                                "secure_mode": "1", "unpatched": ""}}


def test_registry_source_missing_digest(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
        200, json={"token": "t"}, request=httpx.Request("GET", url)))
    _mock_client(monkeypatch, lambda req: httpx.Response(404))
    assert RegistrySource("registry.gitlab.com/g/p", "t").registry_image("sha256:x") == {}


def _cloudrun_handler(traffic, series):
    def handler(req):
        p = req.url.path
        if p.endswith("/services/pcp-prod"):
            return httpx.Response(200, json={"status": {"traffic": traffic}})
        if p.endswith("/revisions/rev-2"):
            return httpx.Response(200, json={"status": {
                "imageDigest": "us-docker.pkg.dev/p/pcp/app@sha256:abc"}})
        if "timeSeries" in p:
            assert 'service_name="pcp-prod"' in req.url.params["filter"]
            return httpx.Response(200, json={"timeSeries": series})
        return httpx.Response(404)

    return handler


def test_cloudrun_source_digest_and_error_rate(monkeypatch):
    series = [{"metric": {"labels": {"response_code_class": "2xx"}},
               "points": [{"value": {"int64Value": "98"}}]},
              {"metric": {"labels": {"response_code_class": "5xx"}},
               "points": [{"value": {"int64Value": "2"}}]}]
    _mock_client(monkeypatch, _cloudrun_handler(
        [{"revisionName": "rev-2", "percent": 100}], series))
    src = CloudRunSource("p", "us-central1", "tok")
    assert src.deploy_digest("pcp-prod") == "sha256:abc"
    # Checks ask for the environment "production"; it must map to pcp-prod.
    assert src.deploy_digest("production") == "sha256:abc"
    assert src.error_rate("production") == {"rate": 0.02, "requests": 100,
                                          "source": "cloud_monitoring"}


def test_cloudrun_split_traffic_and_no_metrics(monkeypatch):
    _mock_client(monkeypatch, _cloudrun_handler(
        [{"revisionName": "rev-1", "percent": 50}, {"revisionName": "rev-2", "percent": 50}], []))
    src = CloudRunSource("p", "us-central1", "tok")
    assert src.deploy_digest("pcp-prod") is None  # no single revision serves 100%
    assert src.error_rate("pcp-prod") is None


def test_health_probes_record_failures(monkeypatch):
    calls = iter([httpx.Response(200), httpx.Response(503), httpx.ConnectError("down")])

    def get(url, timeout):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(httpx, "get", get)
    assert CloudRunSource.health_probes(None, "https://x", 3) == [200, 503, 0]


def test_gcp_token_exchange(monkeypatch):
    seen = []

    def post(url, body, token=None):
        seen.append((url, token))
        if "sts.googleapis.com" in url:
            assert body["subjectToken"] == "jwt"
            assert body["audience"] == "//iam.googleapis.com/projects/1/providers/p"
            return {"access_token": "federated"}
        return {"accessToken": "sa-token"}

    monkeypatch.setattr(gcp, "_post", post)
    assert gcp.access_token("sa@x", "jwt", "projects/1/providers/p") == "sa-token"
    assert seen[1][1] == "federated" and "sa@x:generateAccessToken" in seen[1][0]


def test_composite_routes_methods():
    base, other = FixtureSource(os.path.join(FIX, "tests_pass")), FixtureSource(
        os.path.join(FIX, "deploy_healthy"))
    src = CompositeSource(base, deploy_digest=other)
    assert src.test_report(0)["total_count"] == 142
    assert src.deploy_digest("p") == "sha256:deadbeef"


def _write_claim(path, ctype="tests.pass", asserted=None, claim_id="c-1"):
    path.write_text(json.dumps({
        "claim_id": claim_id, "type": ctype, "agent": "builder", "stage": "verify",
        "subject": {"project_id": 1, "commit_sha": "abc123def456", "mr_iid": 45,
                    "pipeline_id": 9, "image_digest": "sha256:deadbeef"},
        "statement": "x", "asserted": asserted or {}, "created_at": "2026-10-23T00:00:00Z",
        "_source": {"mr_note_id": 1}}))


def test_cli_pass_fail_and_multi_claim_naming(tmp_path, capsys):
    good, lie = tmp_path / "a.json", tmp_path / "b.json"
    _write_claim(good, asserted={"tests_passed": 142, "tests_failed": 0, "coverage_pct": 87.0})
    _write_claim(lie, asserted={"tests_passed": 142, "tests_failed": 0}, claim_id="c-2")
    out = tmp_path / "ev"
    fx = os.path.join(FIX, "tests_pass")
    assert vrun.main(["--claim", str(good), "--out", str(out), "--fixtures", fx]) == 0
    assert (out / "tests.pass.evidence.json").exists()
    rec = json.loads((out / "tests.pass.evidence.json").read_text())
    assert rec["verifier_fingerprint"]
    lie_fx = os.path.join(FIX, "tests_lie")
    assert vrun.main(["--claim", str(good), "--claim", str(lie), "--out", str(out),
                      "--fixtures", lie_fx]) == 1
    assert (out / "tests.pass.c-2.evidence.json").exists()
    assert "CAUGHT LIE" in capsys.readouterr().out


def test_cli_rejects_schema_violations(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"type": "tests.pass"}))
    with pytest.raises(Exception):
        vrun.main(["--claim", str(bad), "--fixtures", str(tmp_path)])


def test_cli_live_sources_are_composed(tmp_path, monkeypatch):
    fake = FixtureSource(os.path.join(FIX, "image_built"))
    monkeypatch.setitem(vrun.LIVE_SOURCES, "registry", (lambda: fake, ["registry_image"]))
    claim = tmp_path / "c.json"
    _write_claim(claim, ctype="image.built")
    assert vrun.main(["--claim", str(claim), "--out", str(tmp_path / "ev"),
                      "--fixtures", str(tmp_path), "--live", "registry"]) == 0
