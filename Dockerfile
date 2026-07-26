# AsterMem image: multi-stage build
# Stage 1 builds the web UI with Node; stage 2 ships only the Python runtime, so the final image has no Node.
# The port is fixed at 8768 (ASTERMEM_PORT) and config.yaml lives in the /app/data volume,
# so "backing up = copying the data/ directory" holds inside containers too.

FROM node:20-alpine AS ui-builder
WORKDIR /build/web-ui
COPY web-ui/package.json web-ui/package-lock.json ./
RUN npm ci
COPY web-ui/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    ASTERMEM_PORT=8768 \
    ASTERMEM_CONFIG=/app/data/config.yaml

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./
COPY backend/ ./backend/
COPY skill/ ./skill/
COPY docs/methodology/ ./docs/methodology/
COPY --from=ui-builder /build/web-ui/dist ./web-ui/dist

VOLUME /app/data
EXPOSE 8768

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/auth/check' % os.environ.get('ASTERMEM_PORT', '8768'), timeout=4)"

CMD ["python", "server.py"]
