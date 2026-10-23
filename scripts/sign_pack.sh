#!/usr/bin/env bash
# Sign the canonicalized evidence pack with Sigstore cosign, keyless.
#
# In GitLab CI the job declares `id_tokens: SIGSTORE_ID_TOKEN: aud: sigstore`;
# cosign exchanges it for a short-lived certificate bound to this project's
# pipeline identity and records the signature in the public Rekor
# transparency log. No signing key exists anywhere. Output: a bundle holding
# the signature, certificate and transparency-log entry.
#
# Fails closed: without cosign + an OIDC token it refuses, unless
# PCP_ALLOW_UNSIGNED=1 (local rehearsal only), in which case it writes a bare
# SHA-256 digest and says plainly that the pack is UNSIGNED.
#
# Usage: scripts/sign_pack.sh evidence-pack.json.canonical
set -euo pipefail

BLOB="${1:-evidence-pack.json.canonical}"
BASE="${BLOB%.canonical}"
BUNDLE="${BASE}.bundle"

sha256() { python3 -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }

if command -v cosign >/dev/null 2>&1 && [ -n "${SIGSTORE_ID_TOKEN:-}" ]; then
  echo "Signing keyless with the CI job's OIDC identity ..."
  cosign sign-blob --yes --bundle "$BUNDLE" "$BLOB"
  python3 - "$BUNDLE" <<'PY'
import json, sys
b = json.load(open(sys.argv[1]))
idx = (b.get("rekorBundle", {}).get("Payload", {}).get("logIndex")
       or next((e.get("logIndex") for e in b.get("verificationMaterial", {}).get("tlogEntries", [])), None))
if idx is not None:
    print(f"Rekor transparency log entry: https://search.sigstore.dev/?logIndex={idx}")
    open(sys.argv[1] + ".rekor", "w").write(str(idx))
PY
  echo "Signed: $BUNDLE"
  exit 0
fi

if [ "${PCP_ALLOW_UNSIGNED:-0}" = "1" ]; then
  sha256 "$BLOB" > "${BASE}.unsigned-digest"
  echo "UNSIGNED: no cosign/OIDC here; wrote a plain digest (local rehearsal only)."
  exit 0
fi

echo "ERROR: cannot sign (cosign missing or no SIGSTORE_ID_TOKEN). Refusing to emit an unsigned pack." >&2
exit 1
