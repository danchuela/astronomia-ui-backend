.PHONY: run test lint install

run:
	uv run uvicorn app.main:app --reload --port 3000

test:
	uv run pytest

lint:
	uv run ruff check .

install:
	uv sync --group dev
