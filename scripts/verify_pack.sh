#!/usr/bin/env bash
# Independently verify the evidence pack. Runs in its own CI job
# (verify-evidence) so "signed" is demonstrated, not asserted.
#
#  1. Re-canonicalize the human-readable pack; it must equal the signed blob.
#  2. Recompute content_sha256; it must match.
#  3. cosign verify-blob against the bundle, pinned to THIS project's CI
#     identity and GitLab's OIDC issuer — a signature from anyone else fails.
#
# Usage: scripts/verify_pack.sh evidence-pack.json
set -euo pipefail

PACK="${1:-evidence-pack.json}"
CANON="$PACK.canonical"
BUNDLE="${PACK}.bundle"

python3 - "$PACK" "$CANON" <<'PY'
import hashlib, json, sys
pack = json.load(open(sys.argv[1]))
canon = json.dumps(pack, sort_keys=True, separators=(",", ":")).encode()
if canon != open(sys.argv[2], "rb").read():
    sys.exit("TAMPER DETECTED: pack does not match the signed canonical blob.")
body = {k: v for k, v in pack.items() if k != "content_sha256"}
want = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
if want != pack.get("content_sha256"):
    sys.exit("TAMPER DETECTED: content_sha256 does not match the pack body.")
print("canonical form and content hash OK")
PY

if [ -f "$BUNDLE" ]; then
  : "${CI_SERVER_URL:?}" "${CI_PROJECT_PATH:?}"
  cosign verify-blob \
    --bundle "$BUNDLE" \
    --certificate-identity-regexp "^${CI_SERVER_URL}/${CI_PROJECT_PATH}//\.gitlab-ci\.yml@refs/" \
    --certificate-oidc-issuer "${CI_SERVER_URL}" \
    "$CANON"
  echo "Signature verifies: signed by ${CI_PROJECT_PATH}'s own pipeline. Evidence pack is authentic."
  exit 0
fi

if [ "${PCP_ALLOW_UNSIGNED:-0}" = "1" ] && [ -f "${PACK}.unsigned-digest" ]; then
  calc=$(python3 -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$CANON")
  if [ "$calc" = "$(cat "${PACK}.unsigned-digest")" ]; then
    echo "UNSIGNED pack: digest matches (local rehearsal; integrity only, not authenticity)."
    exit 0
  fi
  echo "digest MISMATCH" >&2
  exit 1
fi

echo "ERROR: no signature bundle; an unsigned pack is not verifiable." >&2
exit 1
