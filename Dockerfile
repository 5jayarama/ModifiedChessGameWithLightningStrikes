# Chess with Lightning Strikes: one image with the Flask API and the built frontend.
# Works on Render, Fly.io, Railway or any server with Docker.

# ---- 1. build the TypeScript frontend
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---- 2. the Python server
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHESS_DB_PATH=/data/chess.db \
    TRUST_PROXY_HOPS=1 \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engine/ engine/
COPY server/ server/
COPY --from=web /web/dist web/dist
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# The server runs as this unprivileged user, never as root
RUN useradd --create-home --uid 10001 chess \
    && mkdir -p /data && chown chess:chess /data \
    && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod 755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/healthz', timeout=4)"
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
