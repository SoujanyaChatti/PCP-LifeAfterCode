#!/usr/bin/env bash
# Sign the canonicalized evidence pack with Sigstore cosign.
#
# Prefers keyless mode (OIDC from the CI job identity) so no long-lived key is
# stored. Falls back to a CI-managed key if keyless is unavailable. Produces a
# detached signature (.sig) and, in keyless mode, a certificate (.pem).
#
# Usage: scripts/sign_pack.sh evidence-pack.json.canonical
set -euo pipefail

BLOB="${1:-evidence-pack.json.canonical}"
SIG="${BLOB%.canonical}.sig"
CERT="${BLOB%.canonical}.pem"

if ! command -v cosign >/dev/null 2>&1; then
  echo "cosign not installed; skipping signing (demo fallback)." >&2
  # Fallback for local demos without cosign: a plain sha256 'signature'.
  sha256sum "$BLOB" | awk '{print $1}' > "$SIG"
  echo "wrote unsigned digest to $SIG (install cosign for real signatures)"
  exit 0
fi

if [ "${COSIGN_KEYLESS:-1}" = "1" ] && [ -n "${SIGSTORE_ID_TOKEN:-${CI_JOB_JWT_V2:-}}" ]; then
  echo "Signing keyless (OIDC) ..."
  COSIGN_EXPERIMENTAL=1 cosign sign-blob --yes \
    --output-signature "$SIG" \
    --output-certificate "$CERT" \
    "$BLOB"
  echo "Signed: $SIG (+ cert $CERT)"
else
  echo "Signing with CI-managed key ..."
  cosign sign-blob --yes \
    --key "${COSIGN_KEY:-cosign.key}" \
    --output-signature "$SIG" \
    "$BLOB"
  echo "Signed: $SIG"
fi
