#!/bin/sh
set -e

[ -f data/sorabel.db ] || uv run python scripts/seed.py
uv run python scripts/ensure_ingested.py

exec uv run python -m mcp_server.http_server
