.PHONY: install dev backend frontend test lint episode reset-db

# Backend packages (Python, with uv) and website packages (Node, with pnpm).
install:
	cd backend && uv sync
	cd frontend && pnpm install

# Website at http://localhost:5173, backend at http://localhost:8000 (API page at /docs).
# Both restart when their code changes; Ctrl+C stops both.
dev:
	$(MAKE) -j 2 backend frontend

backend:
	cd backend && uv run uvicorn app.main:app --reload

frontend:
	cd frontend && pnpm dev

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && pnpm lint && pnpm exec tsc -b

# One episode with the profile's settings, from the command line.
episode:
	cd backend && uv run python -m app.pipeline

# Delete the default database file (needed after changing a table). The next start
# creates it again, empty, with the demo profile.
reset-db:
	rm -f podcast.db
