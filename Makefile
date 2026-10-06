.PHONY: episode lint

TOPIC ?= artificial intelligence
MINUTES ?= 3

# Phase 1: runs the standalone spike -> media/spike.mp3
episode:
	cd backend && uv run python scripts/spike.py "$(TOPIC)" --minutes $(MINUTES)

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
