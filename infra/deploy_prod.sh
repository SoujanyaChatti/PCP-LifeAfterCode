#!/usr/bin/env bash
# Promote the EXACT verified image digest to production Cloud Run (patched
# build), run post-deploy health probes, emit the deploy.healthy claim, and
# roll back automatically if health checks fail (M5: Monitor stage).
set -euo pipefail

: "${GCP_PROJECT:?}"; : "${GCP_REGION:=us-central1}"; : "${IMAGE:?}"
SERVICE="pcp-prod"

gcloud config set project "${GCP_PROJECT}"

# Capture the currently-serving revision so we can roll back to it.
PREV=$(gcloud run services describe "${SERVICE}" --region="${GCP_REGION}" \
  --format='value(status.latestReadyRevisionName)' 2>/dev/null || echo "")

gcloud run deploy "${SERVICE}" \
  --image="${IMAGE}" \
  --region="${GCP_REGION}" --platform=managed --allow-unauthenticated \
  --set-env-vars="SECURE_MODE=1,APP_VERSION=${CI_COMMIT_SHORT_SHA:-prod}" \
  --max-instances=4 --quiet

URL=$(gcloud run services describe "${SERVICE}" --region="${GCP_REGION}" --format='value(status.url)')
echo "PROD_URL=${URL}" | tee prod.env

# --- Monitor: 5 health probes ---
GOOD=0
for i in $(seq 1 5); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "${URL}/healthz" || echo 000)
  echo "probe $i: $CODE"
  [ "$CODE" = "200" ] && GOOD=$((GOOD+1))
  sleep 2
done

if [ "$GOOD" -lt 5 ]; then
  echo "HEALTH CHECK FAILED ($GOOD/5). Rolling back to ${PREV}." >&2
  if [ -n "$PREV" ]; then
    gcloud run services update-traffic "${SERVICE}" --region="${GCP_REGION}" \
      --to-revisions="${PREV}=100" --quiet
  fi
  # Signal the flow to open an incident issue.
  echo "ROLLED_BACK=1" >> prod.env
  exit 1
fi

# Emit the deploy.healthy claim bound to the promoted digest.
python - <<PY
import json, os
digest = os.environ.get("IMAGE","")
claim = {
  "claim_id": "deploy-ci", "type": "deploy.healthy", "agent": "release",
  "stage": "release",
  "subject": {"project_id": int(os.environ.get("CI_PROJECT_ID","0")),
              "commit_sha": os.environ.get("CI_COMMIT_SHA",""),
              "image_digest": digest, "staging_url": "${URL}"},
  "statement": "Promoted digest to production; 5/5 health probes OK.",
  "asserted": {"service": "pcp-prod"},
  "created_at": "1970-01-01T00:00:00Z",
}
os.makedirs("claims", exist_ok=True)
json.dump(claim, open("claims/deploy.healthy.json","w"), indent=2)
print("wrote deploy.healthy claim")
PY

echo "Production healthy at ${URL}"
