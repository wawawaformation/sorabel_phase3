#!/bin/sh
set -e

[ -f data/sorabel.db ] || uv run --no-dev python scripts/seed.py
uv run --no-dev python scripts/ensure_ingested.py

# Sans commande explicite (service gateway) : le serveur HTTP. Avec une commande
# (service ui : streamlit run ...), on la relaie — sinon docker-compose "command:"
# est silencieusement ignoré (vérifié empiriquement).
if [ "$#" -eq 0 ]; then
    exec uv run --no-dev python -m mcp_server.http_server
else
    exec "$@"
fi
