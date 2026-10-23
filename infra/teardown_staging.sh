#!/usr/bin/env bash
# Tear down the ephemeral staging service for this MR, to control spend.
set -euo pipefail
: "${GCP_REGION:=us-central1}"
MR_IID="${CI_MERGE_REQUEST_IID:-local}"
SERVICE="pcp-staging-mr-${MR_IID}"
gcloud run services delete "${SERVICE}" --region="${GCP_REGION}" --quiet || true
echo "Torn down ${SERVICE}"
