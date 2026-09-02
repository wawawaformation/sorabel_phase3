.PHONY: install up down seed ingest demo ui eval test fmt lint serve client journal

install:
	uv sync

seed:
	uv run python scripts/seed.py

ingest:
	uv run python scripts/run_ingest.py

demo:
	uv run python scripts/demo_agent.py $${Q:+"$$Q"}

ui:
	uv run streamlit run app.py

eval:
	uv run python scripts/eval_rag.py

up:
	docker compose up -d

down:
	docker compose down

test:
	uv run pytest

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .
	uv run mypy gateway ingest retrieval sql mcp_server

serve:
	uv run python -m mcp_server.server

client:
	uv run python scripts/mcp_client.py --profile $${PROFILE:-support}

journal:
	@tail -n 20 logs/journal.jsonl 2>/dev/null || echo "journal vide"
