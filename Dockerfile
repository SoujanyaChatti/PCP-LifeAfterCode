# Single container image for the team-notes app. Built once, promoted by digest.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PCP_DB_PATH=/tmp/pcp_notes.db

WORKDIR /app

# Install runtime deps. SECURE_MODE at runtime selects vulnerable vs patched
# behaviour; the build itself is identical, so the same digest is what gets
# promoted. (In a real split you'd build two tags; for the demo one image with
# the flag keeps the before/after replay crisp.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Cloud Run provides $PORT.
ENV PORT=8080
EXPOSE 8080

# Record the build SHA as an image label so the verifier can confirm the image
# was built from the claimed commit (passed at build time).
ARG COMMIT_SHA=unknown
LABEL org.opencontainers.image.revision=$COMMIT_SHA
ENV APP_VERSION=$COMMIT_SHA

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
