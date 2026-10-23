#!/usr/bin/env bash
# Tear down this run's ephemeral staging services, to control spend.
set -euo pipefail
: "${GCP_REGION:=us-central1}"
source "$(dirname "$0")/staging_name.sh"
for s in "${STAGING_SVC}-pre" "$STAGING_SVC"; do
  gcloud run services delete "$s" --region="$GCP_REGION" --quiet || true
  echo "Torn down $s"
done
