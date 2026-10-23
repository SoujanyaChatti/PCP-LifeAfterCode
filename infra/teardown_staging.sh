#!/usr/bin/env bash
# Tear down this run's ephemeral staging services, to control spend.
set -euo pipefail
: "${GCP_REGION:=us-central1}" "${STAGING_ID:?}"
SVC="pcp-stg-${STAGING_ID}"
for s in "${SVC:0:44}-pre" "${SVC:0:48}"; do
  gcloud run services delete "$s" --region="$GCP_REGION" --quiet || true
  echo "Torn down $s"
done
