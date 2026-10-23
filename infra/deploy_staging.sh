#!/usr/bin/env bash
# Deploy this run's two images to ephemeral Cloud Run staging services:
#   pcp-stg-<id>-pre  the pre-patch twin   -> the Red Team's target
#   pcp-stg-<id>      the hardened build   -> where the verifier replays
# Both are deployed BY DIGEST from Artifact Registry. Behaviour is baked into
# each image, so no SECURE_MODE is set here. Exports STAGING_URL_PRE and
# STAGING_URL (dotenv). Auth: the job's WIF token (CLOUDSDK_AUTH_ACCESS_TOKEN).
set -euo pipefail

: "${GCP_PROJECT:?}" "${GCP_REGION:=us-central1}" "${STAGING_ID:?}"
read -r POST_REF PRE_REF < <(python3 -c 'import json;d=json.load(open("build/images.json"));print(d["ar_ref"],d["ar_twin_ref"])')
: "${POST_REF:?no Artifact Registry ref in build/images.json}"

deploy() {  # service image
  gcloud run deploy "$1" --image="$2" --region="$GCP_REGION" --platform=managed \
    --allow-unauthenticated --max-instances=1 --memory=256Mi \
    --labels="pcp=staging,pcp-id=${STAGING_ID}" --quiet >/dev/null
  gcloud run services describe "$1" --region="$GCP_REGION" --format='value(status.url)'
}

SVC="pcp-stg-${STAGING_ID}"
PRE_URL=$(deploy "${SVC:0:44}-pre" "$PRE_REF")
POST_URL=$(deploy "${SVC:0:48}" "$POST_REF")
printf 'STAGING_URL_PRE=%s\nSTAGING_URL=%s\n' "$PRE_URL" "$POST_URL" | tee staging.env
