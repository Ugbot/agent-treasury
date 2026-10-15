# Running with Docker

    docker compose up --build

- UI: http://localhost:8080 (nginx serves the Vite build and proxies `/api` to the API)
- API: http://localhost:8000 (`/health`, `/api/...`)

## Configuration

Create a `.env` next to `docker-compose.yml` (optional; it is loaded at runtime and never baked into an image, and `.dockerignore` excludes it):

    STRIPE_SECRET_KEY=sk_test_...      # test mode keys only
    ANTHROPIC_API_KEY=sk-ant-...       # optional, enables the LLM chat/agent

If no Stripe key is present the API uses `MockGateway`: payments are simulated in-process and nothing touches Stripe. With a `sk_test_` key, transfers go to Stripe test mode.

## Data

SQLite files live in the `treasury-data` volume mounted at `/data` (`TREASURY_DB`, `TREASURY_DBOS_DB`). `docker compose down -v` wipes it.

## Images

- `Dockerfile`: multi-stage, python 3.12-slim, `uv sync --frozen --no-dev`, runs `uvicorn treasury.api:app --factory`.
- `frontend/Dockerfile`: node 22 build, nginx runtime (`frontend/nginx.conf`).
