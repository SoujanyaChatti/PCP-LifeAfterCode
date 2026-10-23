"""Google Cloud access tokens from a GitLab CI OIDC id_token, via Workload
Identity Federation. No service-account key is ever stored.

Stdlib only, so it runs in any job image (python:slim, google/cloud-sdk):

    export CLOUDSDK_AUTH_ACCESS_TOKEN=$(python3 -m verifier.gcp token --sa "$GCP_DEPLOY_SA")

Env:
  GCP_ID_TOKEN      the job's id_token (declared with `id_tokens:` in CI)
  GCP_WIF_PROVIDER  projects/<num>/locations/global/workloadIdentityPools/<pool>/providers/<id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _post(url: str, body: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def access_token(service_account: str, id_token: str | None = None,
                 provider: str | None = None) -> str:
    """Exchange the CI id_token for a federated token, then impersonate SA."""
    id_token = id_token or os.environ["GCP_ID_TOKEN"]
    provider = provider or os.environ["GCP_WIF_PROVIDER"]
    federated = _post("https://sts.googleapis.com/v1/token", {
        "audience": f"//iam.googleapis.com/{provider}",
        "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
        "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
        "scope": SCOPE,
        "subjectTokenType": "urn:ietf:params:oauth:token-type:jwt",
        "subjectToken": id_token,
    })["access_token"]
    return _post(
        "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
        f"{service_account}:generateAccessToken",
        {"scope": [SCOPE]}, token=federated)["accessToken"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["token"])
    p.add_argument("--sa", required=True, help="service account email to impersonate")
    args = p.parse_args(argv)
    print(access_token(args.sa))
    return 0


if __name__ == "__main__":
    sys.exit(main())
