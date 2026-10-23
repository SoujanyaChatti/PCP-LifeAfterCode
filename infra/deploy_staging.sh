#!/usr/bin/env bash
# Deploy this run's two images to ephemeral Cloud Run staging services:
#   <svc>-pre  the pre-patch twin   -> the Red Team's target
#   <svc>      the hardened build   -> where the verifier replays
# Both are deployed BY DIGEST from Artifact Registry. Behaviour is baked into
# each image, so no SECURE_MODE is set here. Exports STAGING_URL_PRE and
# STAGING_URL (dotenv). Auth: the job's WIF token (CLOUDSDK_AUTH_ACCESS_TOKEN).
set -euo pipefail
shopt -s inherit_errexit

: "${GCP_PROJECT:?}" "${GCP_REGION:=us-central1}"
source "$(dirname "$0")/staging_name.sh"
REFS=$(python3 -c 'import json;d=json.load(open("build/images.json"));print(d["ar_ref"],d["ar_twin_ref"])')
read -r POST_REF PRE_REF <<<"$REFS"
[ -n "${POST_REF:-}" ] && [ -n "${PRE_REF:-}" ] || { echo "no Artifact Registry refs in build/images.json" >&2; exit 1; }

deploy() {  # service image -> prints URL; any failure aborts (no stale URL)
  gcloud run deploy "$1" --image="$2" --region="$GCP_REGION" --platform=managed \
    --allow-unauthenticated --max-instances=1 --memory=256Mi \
    --labels="pcp=staging" --quiet >&2 || return 1
  gcloud run services update-traffic "$1" --region="$GCP_REGION" --to-latest --quiet >&2 || return 1
  gcloud run services describe "$1" --region="$GCP_REGION" --format='value(status.url)'
}

PRE_URL=$(deploy "${STAGING_SVC}-pre" "$PRE_REF")
POST_URL=$(deploy "$STAGING_SVC" "$POST_REF")
printf 'STAGING_URL_PRE=%s\nSTAGING_URL=%s\n' "$PRE_URL" "$POST_URL" | tee staging.env
