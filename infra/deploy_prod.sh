#!/usr/bin/env bash
# Promote the EXACT verified digest (the hardened build the security gate
# replayed against) to production Cloud Run, probe it, and roll back on
# failure. Emits the Release agent's deploy.healthy claim; the verifier then
# checks it independently against the Cloud Run API and Cloud Monitoring
# (verify:deploy). Exports PROD_URL and PREV_REVISION (dotenv) for rollback.
set -euo pipefail
shopt -s inherit_errexit

: "${GCP_PROJECT:?}" "${GCP_REGION:=us-central1}"
SERVICE="${PCP_PROD_SERVICE:-pcp-prod}"
REFS=$(python3 -c 'import json;d=json.load(open("build/images.json"));print(d["ar_ref"],d["digest"])')
read -r REF DIGEST <<<"$REFS"
[ -n "${REF:-}" ] || { echo "no Artifact Registry ref in build/images.json" >&2; exit 1; }

# Roll back to what is SERVING now (the revision with 100% of traffic), not to
# the latest ready revision: after an earlier rollback those differ.
PREV=$(gcloud run services describe "$SERVICE" --region="$GCP_REGION" --format=json 2>/dev/null \
  | python3 -c 'import json,sys
try: t=json.load(sys.stdin).get("status",{}).get("traffic",[])
except ValueError: t=[]
print(next((x["revisionName"] for x in t if x.get("percent")==100 and x.get("revisionName")),""))' || true)

gcloud run deploy "$SERVICE" --image="$REF" --region="$GCP_REGION" --platform=managed \
  --allow-unauthenticated --max-instances=4 --labels=pcp=prod --quiet
# A previous rollback pinned traffic to an old revision; Cloud Run keeps that
# split for later deploys. Send 100% to the revision we just deployed.
gcloud run services update-traffic "$SERVICE" --region="$GCP_REGION" --to-latest --quiet

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
