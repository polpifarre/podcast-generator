.PHONY: dev test lint episode reset-db

# Backend at http://localhost:8000 (try it at /docs). Restarts when the code changes.
dev:
	cd backend && uv run uvicorn app.main:app --reload

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

# One episode with the profile's settings, from the command line.
episode:
	cd backend && uv run python -m app.pipeline

# Delete the default database file (needed after changing a table). The next start
# creates it again, empty, with the demo profile.
reset-db:
	rm -f podcast.db
