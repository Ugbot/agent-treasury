.PHONY: install api web dev demo test lint build check probe

install:
	uv sync
	cd frontend && npm install

# Backend on :8000. Mock gateway by default; set STRIPE_SECRET_KEY=sk_test_... for Stripe test mode.
api:
	uv run uvicorn treasury.api:app --factory --reload --port 8000

web:
	cd frontend && npm run dev

dev:
	$(MAKE) -j2 api web

# Starts API (:8000) and web (:5173), then (in another shell) `make demo-run` drives all four scenarios.
demo:
	$(MAKE) -j2 api web

# Runs the four scenarios over HTTP on a fresh DB; prints Stripe ids and the audit verify result.
demo-run:
	uv run python scripts/run_demo.py

demo-mock:
	uv run python scripts/run_demo.py --mock

test:
	uv run pytest -q
	cd frontend && npm test

lint:
	uv run ruff check src tests

build:
	cd frontend && npm run build

check: lint test build

# Reports which Stripe capabilities your sandbox actually has (needs STRIPE_SECRET_KEY).
probe:
	uv run python -m treasury.probe
