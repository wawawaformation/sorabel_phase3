#!/bin/sh
set -e

[ -f data/sorabel.db ] || uv run --no-dev python scripts/seed.py
uv run --no-dev python scripts/ensure_ingested.py

exec uv run --no-dev python -m mcp_server.http_server
