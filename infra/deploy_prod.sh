#!/usr/bin/env bash
# Promote the EXACT verified digest (the hardened build the security gate
# replayed against) to production Cloud Run, probe it, and roll back on
# failure. Emits the Release agent's deploy.healthy claim; the verifier then
# checks it independently against the Cloud Run API and Cloud Monitoring
# (verify:deploy). Exports PROD_URL and PREV_REVISION (dotenv) for rollback.
set -euo pipefail

: "${GCP_PROJECT:?}" "${GCP_REGION:=us-central1}"
SERVICE="pcp-prod"
read -r REF DIGEST < <(python3 -c 'import json;d=json.load(open("build/images.json"));print(d["ar_ref"],d["digest"])')
: "${REF:?no Artifact Registry ref in build/images.json}"

PREV=$(gcloud run services describe "$SERVICE" --region="$GCP_REGION" \
  --format='value(status.latestReadyRevisionName)' 2>/dev/null || echo "")

gcloud run deploy "$SERVICE" --image="$REF" --region="$GCP_REGION" --platform=managed \
  --allow-unauthenticated --max-instances=4 --labels=pcp=prod --quiet

URL=$(gcloud run services describe "$SERVICE" --region="$GCP_REGION" --format='value(status.url)')
printf 'PROD_URL=%s\nPREV_REVISION=%s\n' "$URL" "$PREV" | tee prod.env

GOOD=0
for i in 1 2 3 4 5; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "${URL}/healthz" || echo 000)
  echo "probe $i: $CODE"
  [ "$CODE" = "200" ] && GOOD=$((GOOD+1))
  sleep 2
done
if [ "$GOOD" -lt 5 ]; then
  echo "HEALTH CHECK FAILED ($GOOD/5); rolling back to ${PREV:-<none>}." >&2
  [ -n "$PREV" ] && gcloud run services update-traffic "$SERVICE" --region="$GCP_REGION" \
    --to-revisions="${PREV}=100" --quiet
  exit 1
fi

mkdir -p claims
python3 - "$DIGEST" "$URL" <<'PY'
import json, os, sys, uuid
from datetime import datetime, timezone
digest, url = sys.argv[1], sys.argv[2]
claim = {
  "claim_id": str(uuid.uuid4()), "type": "deploy.healthy", "agent": "release",
  "stage": "release",
  "subject": {"project_id": int(os.environ.get("CI_PROJECT_ID", "0")),
              "commit_sha": os.environ.get("CI_COMMIT_SHA", ""),
              "pipeline_id": int(os.environ.get("CI_PIPELINE_ID", "0")),
              "image_digest": digest, "service_url": url},
  "statement": "Promoted the verified digest to production; 5/5 health probes OK.",
  "asserted": {"service": "pcp-prod", "probes_ok": 5},
  "created_at": datetime.now(timezone.utc).isoformat(),
}
json.dump(claim, open("claims/deploy.healthy.json", "w"), indent=2)
print("wrote deploy.healthy claim for", digest)
PY
echo "Production at ${URL}"
