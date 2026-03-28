# astronomIA UI Backend (BFF)

Backend for Frontend that connects **astronomia-ui-frontend** with **astronomia-galaxy-api** (or n8n). A lightweight FastAPI proxy that forwards requests and SSE streams.

## Orchestration modes

| Mode | `ORCHESTRATOR_MODE` | Flow |
|------|---------------------|------|
| Direct | `direct` | BFF → Galaxy API (`/analyze/stream`, `/artifacts`) |
| n8n | `n8n` | BFF → n8n webhook → Galaxy API or other services |
| Auto | `auto` | LLM classifier: observation planning → n8n; object analysis → Galaxy API (requires `OPENAI_API_KEY`) |

## Project structure

```
app/
├── __init__.py
├── main.py       # FastAPI app, CORS, routes: /health, /analyze, /analyze/stream, /artifacts
├── config.py     # Settings loaded from environment variables
├── schemas.py    # AnalyzeRequest (+ ChatMessage), AnalyzeResponse
└── gateways/
    ├── __init__.py
    ├── base.py   # AnalysisGateway (ABC): analyze() + analyze_stream()
    ├── direct.py # DirectGalaxyGateway — proxy to Galaxy API with SSE streaming
    └── n8n.py    # N8nGateway — webhook to n8n, converts response to SSE
```

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) or pip

## Configuration

Create a `.env` file in the root (see `.env.example`):

```env
ORCHESTRATOR_MODE=direct
GALAXY_API_URL=http://localhost:8000
GALAXY_API_KEY=
N8N_WEBHOOK_URL=
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

## Running locally

1. Start the Galaxy API on `:8000` (in its repo: `make run`).
2. Start the BFF:
   ```bash
   uv sync && uv run uvicorn app.main:app --reload --port 3000
   ```
3. Point the frontend at `VITE_API_URL=http://localhost:3000`.

**Docker:**

Copy `.env.example` to `.env` and set `ORCHESTRATOR_MODE`. Use `auto` or `n8n` only when `OPENAI_API_KEY` or `N8N_WEBHOOK_URL` is configured. `docker-compose.yml` loads `.env` and overrides only `GALAXY_API_URL` so the container can reach the host Galaxy API at `host.docker.internal:8000`.

```bash
docker compose up --build
```

The BFF listens on `http://localhost:3000`. The Galaxy API must be reachable on host port `8000`; Docker overrides `GALAXY_API_URL`, so you do not need to set it in `.env` for container runs.

## n8n mode

Use n8n as a routing layer between the frontend and backend services.

1. Set `ORCHESTRATOR_MODE=n8n` and `N8N_WEBHOOK_URL=<url>` in `.env`.
2. The BFF forwards all requests to the webhook. n8n decides how to route them.
3. n8n must respond with the same JSON schema as the Galaxy API:

```json
{
  "request_id": "...",
  "status": "success",
  "summary": "Response text",
  "results": {},
  "artifacts": [],
  "warnings": []
}
```

If there is an image and the BFF artifact proxy is not used, include `image_url` with a public URL — the frontend uses it directly.

## Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/health` | `{"status":"ok"}` |
| `POST` | `/analyze` | Synchronous analysis. Body: `request_id`, `message`/`messages`, `target`, `task`, `image_url`, `options`. Returns JSON. |
| `POST` | `/analyze/stream` | Same body, SSE response (`status`, `summary`, `artifacts`, `end`, `error`). In n8n mode the BFF converts the JSON response to SSE. |
| `GET` | `/artifacts/{request_id}/image` | Proxy to Galaxy API image (direct mode only). |

## Quality

```bash
make install # uv sync --group dev
make run     # uvicorn --reload on :3000
make test    # pytest
make lint    # ruff
```

### Request body normalisation

Gateways automatically normalise `message` and `messages` fields: if only `messages` is provided, the last user message is extracted as `message`; if only `message` is provided, it is wrapped in a `messages` array.

### Authentication

Requests to the Galaxy API include the `X-API-Key` header with the value of `GALAXY_API_KEY`.

### Timeouts

| Route | Timeout |
|-------|---------|
| `POST /analyze` | 120s |
| `POST /analyze/stream` | connect 15s, write 30s, read unlimited |
| `GET /artifacts/.../image` | 30s |
