#!/bin/sh
# Starts the server. If the container starts as root, it only stays root long enough to make
# the data folder writable (a mounted volume is usually owned by root), then switches to the
# unprivileged "chess" user. Hosts that already start containers as non-root skip that step.
set -e

DATA_DIR=$(dirname "${CHESS_DB_PATH:-/data/chess.db}")

set -- gunicorn \
    --worker-class gthread \
    --workers "${WEB_CONCURRENCY:-2}" \
    --threads "${GUNICORN_THREADS:-32}" \
    --timeout 60 \
    --bind "0.0.0.0:${PORT:-8000}" \
    "server.app:create_app()"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown chess:chess "$DATA_DIR"
    # HOME too: gunicorn keeps a control socket in the home folder, and root's isn't writable
    HOME=/home/chess exec setpriv --reuid=chess --regid=chess --init-groups "$@"
fi
exec "$@"
