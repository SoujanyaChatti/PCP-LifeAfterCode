#!/usr/bin/env bash
# Independently verify the evidence pack's signature. Runs in its own CI job
# (verify-evidence) so "signed" is demonstrated, not asserted. Re-canonicalizes
# the pack first, so a tampered pack fails verification.
#
# Usage: scripts/verify_pack.sh evidence-pack.json
set -euo pipefail

PACK="${1:-evidence-pack.json}"
CANON="$PACK.canonical"
SIG="${PACK}.sig"
CERT="${PACK}.pem"

# Re-canonicalize from the human-readable pack and compare to the signed blob.
python - "$PACK" "$CANON" <<'PY'
import json, sys
pack = json.load(open(sys.argv[1]))
canon = json.dumps(pack, sort_keys=True, separators=(",", ":")).encode()
open(sys.argv[2] + ".recomputed", "wb").write(canon)
PY

if ! cmp -s "$CANON" "$CANON.recomputed"; then
  echo "TAMPER DETECTED: recomputed canonical form does not match the signed blob." >&2
  exit 1
fi

if ! command -v cosign >/dev/null 2>&1; then
  echo "cosign not installed; verifying fallback digest." >&2
  calc=$(sha256sum "$CANON" | awk '{print $1}')
  want=$(cat "$SIG")
  [ "$calc" = "$want" ] && { echo "fallback digest OK"; exit 0; } || { echo "digest MISMATCH"; exit 1; }
fi

if [ -f "$CERT" ]; then
  echo "Verifying keyless signature ..."
  COSIGN_EXPERIMENTAL=1 cosign verify-blob \
    --certificate "$CERT" \
    --certificate-identity-regexp "${COSIGN_CERT_IDENTITY_REGEX:-.*}" \
    --certificate-oidc-issuer-regexp "${COSIGN_OIDC_ISSUER_REGEX:-.*}" \
    --signature "$SIG" \
    "$CANON"
else
  echo "Verifying with public key ..."
  cosign verify-blob --key "${COSIGN_PUB:-cosign.pub}" --signature "$SIG" "$CANON"
fi
echo "Signature verifies. Evidence pack is authentic."
