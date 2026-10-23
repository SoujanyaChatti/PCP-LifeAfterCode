# Single container image for the team-notes app, promoted by digest.
#
# SECURE_MODE is baked in at build time, never set at deploy time, so the
# digest alone determines behaviour: the digest the security gate replayed
# against is byte-for-byte what production runs. The pipeline also builds a
# pre-patch twin (SECURE_MODE=0 + the vulnerable dependency pin) from the same
# commit as the Red Team's target; that twin is never promoted.
FROM python:3.12-slim

ARG SECURE_MODE=1
ARG COMMIT_SHA=unknown
# Demo only: weaknesses left unfixed in a secure build (e.g. "V1"), to show the
# security gate catching an incomplete patch. Declared in the image labels.
ARG PCP_UNPATCHED=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PCP_DB_PATH=/tmp/pcp_notes.db \
    SECURE_MODE=$SECURE_MODE \
    PCP_UNPATCHED=$PCP_UNPATCHED \
    APP_VERSION=$COMMIT_SHA \
    PORT=8080

WORKDIR /app

COPY requirements.txt requirements-vuln.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && case "$SECURE_MODE,$PCP_UNPATCHED" in \
      0,*|*V6*) pip install --no-cache-dir -r requirements-vuln.txt ;; \
    esac

COPY app/ ./app/

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8080

# The verifier reads these labels from the registry to bind the image to the
# claimed commit and to tell the shipped build from the pre-patch twin.
LABEL org.opencontainers.image.revision=$COMMIT_SHA \
      dev.pcp.secure-mode=$SECURE_MODE \
      dev.pcp.unpatched=$PCP_UNPATCHED

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
