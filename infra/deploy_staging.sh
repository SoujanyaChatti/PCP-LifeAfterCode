#!/usr/bin/env bash
# Deploy the built digest to a uniquely-named ephemeral Cloud Run staging
# service for this MR, and export its URL as STAGING_URL (dotenv artifact) for
# the Red Team. Authenticates via Workload Identity Federation (OIDC) — no
# long-lived service-account key is stored.
set -euo pipefail

: "${GCP_PROJECT:?set GCP_PROJECT}"
: "${GCP_REGION:=us-central1}"
: "${IMAGE:?set IMAGE (registry:tag)}"
MR_IID="${CI_MERGE_REQUEST_IID:-local}"
SERVICE="pcp-staging-mr-${MR_IID}"

# Auth via WIF (expects google-github-actions-style or gcloud OIDC setup).
if [ -n "${GCP_WIF_PROVIDER:-}" ] && [ -n "${CI_JOB_JWT_V2:-}" ]; then
  echo "${CI_JOB_JWT_V2}" > /tmp/oidc.jwt
  gcloud iam workload-identity-pools create-cred-config "${GCP_WIF_PROVIDER}" \
    --service-account="${GCP_DEPLOY_SA}" \
    --credential-source-file=/tmp/oidc.jwt \
    --output-file=/tmp/gcp-creds.json
  gcloud auth login --cred-file=/tmp/gcp-creds.json
fi
gcloud config set project "${GCP_PROJECT}"

# Staging runs the VULNERABLE build so the Red Team has targets, with synthetic
# data only. (The Blue Team's patch flips SECURE_MODE=1 on redeploy.)
gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --region="${GCP_REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --set-env-vars="SECURE_MODE=${SECURE_MODE:-0},APP_VERSION=${CI_COMMIT_SHORT_SHA:-dev}" \
  --max-instances=2 \
  --quiet

URL=$(gcloud run services describe "${SERVICE}" --region="${GCP_REGION}" --format='value(status.url)')
echo "STAGING_URL=${URL}" | tee staging.env
echo "Deployed staging: ${URL}"
