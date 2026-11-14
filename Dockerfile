# syntax=docker/dockerfile:1
ARG TARGETARCH

# ---------- 1. build the React UI ----------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 2. unit tests (database tests need a live PostgreSQL: run scripts/test-db.sh) ----------
FROM python:3.12-slim AS test
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
WORKDIR /app
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY backend/ ./
RUN PYTHONPATH=. python -m pytest -q

# ---------- 2b. curl-impersonate v0.6.1 (github.com/lwthiker/curl-impersonate), checked against pinned hashes ----------
FROM scratch AS ci-arm64
ADD --checksum=sha256:b45f14366e4766fbd70a54a07182d2ffaf1e8aed0f0d85aa4e0183fa9d041694 https://github.com/lwthiker/curl-impersonate/releases/download/v0.6.1/curl-impersonate-v0.6.1.aarch64-linux-gnu.tar.gz /ci.tgz
FROM scratch AS ci-amd64
ADD --checksum=sha256:fa1e1614f7ba69ccc66721a0f38be457a3647eb64c75d66974b56186e3316b12 https://github.com/lwthiker/curl-impersonate/releases/download/v0.6.1/curl-impersonate-v0.6.1.x86_64-linux-gnu.tar.gz /ci.tgz
FROM ci-${TARGETARCH} AS ci

# ---------- 3. runtime ----------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10 \
    NA_DATA_DIR=/data NA_STATIC_DIR=/app/static
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid 10001 --home /app app \
 && mkdir -p /data && chown app:app /data
COPY --from=ci /ci.tgz /tmp/ci.tgz
RUN mkdir -p /opt/curl-impersonate && tar xzf /tmp/ci.tgz -C /opt/curl-impersonate && rm /tmp/ci.tgz
ENV NA_CURL_IMPERSONATE_DIR=/opt/curl-impersonate
COPY backend/app ./app
COPY --from=ui /ui/dist ./static
# make sure the test stage ran (and passed) before an image is produced
COPY --from=test /app/requirements.txt /tmp/tested
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
