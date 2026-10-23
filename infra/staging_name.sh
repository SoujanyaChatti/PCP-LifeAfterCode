# Shared by deploy/teardown: a short, always-valid, collision-free Cloud Run
# service name for this run's staging pair (names: lowercase, <=49 chars, no
# trailing dash).
: "${STAGING_ID:?}"
STAGING_SVC="pcp-stg-$(printf %s "$STAGING_ID" | sha1sum | cut -c1-12)"
