# Deployment Guide

This document describes how to run the Evidence-Backed Procurement Decision &
Verification System in a production-like environment. It was verified against a
real `docker compose up` run on **Windows 11 with Docker Desktop 29.x** (see
[Release Report](release_report.md) for the measured results).

> **Status note:** the stack below was built, started, and its `/health`,
> `/ready`, nginx proxy and API endpoints verified against localhost. It is not
> deployed to a public cloud; to go live you must add the networking/domain
> steps in [Going live](#5-going-live) yourself.

---

## 1. Architecture

```
                        ┌────────────────────────────────────────────┐
                        │              nginx (frontend)              │
                        │  :8080  SPA static + proxy                 │
                        │  /api  /health  /ready  ──────────────┐    │
                        └────────────────────────────────────────┼────┘
                                                                 │ http
   Browser / MCP client ───────────────────────────────┐         ▼
                                                        │  ┌──────────────────────┐
                                                        └──│  backend (uvicorn)   │
                                                           │  :8000              │
                                                           └─────┬────────┬──────┘
                                                                 │        │
                                             SQLAlchemy (psycopg)│        │ Qdrant client
                                                                 ▼        ▼
                                                        ┌────────────┐ ┌──────────────┐
                                                        │ PostgreSQL │ │   Qdrant    │
                                                        │  :5432     │ │  :6333      │
                                                        └────────────┘ └──────────────┘
```

- **backend** — FastAPI application. Delegates vector work to Qdrant, SQL to
  PostgreSQL. Runs the deterministic (LLM-free) verification engine; an optional
  LLM provider can be enabled via env vars but is never required.
- **frontend** — nginx serving the built React SPA and proxying `/api`,
  `/health`, `/ready` to the backend (same-origin, no CORS needed in prod).
- **postgres** — schema, memory entries, execution/audit logs.
- **qdrant** — persistent vector store for evidence retrieval.
- **MCP server** — lives inside the backend package and is exposed over stdio
  locally only. It is **not** exposed as a network endpoint in this compose
  file.

---

## 2. Prerequisites

- Docker Engine **24+** and Docker Compose **v2.20+** (Compose v5 works).
- `git` to clone the repository.
- (Optional) an OpenAI-compatible or Ollama endpoint if you want LLM-assisted
  explanation; the system runs fully without one (`LLM_PROVIDER=none`).

---

## 3. Environment configuration

Copy `.env.example` to `.env` for the containerized deployment **or** set the
variables in the shell. Compose already wires production defaults; the
variables below override them when needed.

| Variable | Default (compose) | Production guidance |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | keep `production` |
| `DEBUG` | unset | keep unset (never `true` in prod) |
| `DATABASE_URL` | `postgresql+psycopg://pv:pv@postgres:5432/procurement` | keep; change the `pv:pv` credentials and export them as a secret |
| `VECTOR_STORE_MODE` | `server` | keep `server` (persistent Qdrant) |
| `QDRANT_URL` | `http://qdrant:6333` | keep; use `https://...` + `QDRANT_API_KEY` for a remote instance |
| `QDRANT_COLLECTION` | `procurement_documents` | set to a cluster-specific name |
| `LLM_PROVIDER` | `none` | `none` for deterministic-only; `ollama`/`api` optional |
| `LLM_MODEL` / `LLM_BASE_URL` | — | only when an LLM provider is used |
| `AUTH_ENABLED` | `false` | **set `true`** when exposing beyond localhost; see [Security](#4-security) |
| `CORS_ORIGINS` | `["http://localhost:8080"]` | list the exact public origins when serving browser traffic |
| `UPLOAD_DIR` | `/app/data/uploads` | inside the `./data` bind mount |
| `RATE_LIMIT_ENABLED` | `false` | `true` for public exposure |

Never commit a real `.env`. `.gitignore` excludes it and `.env.example` contains
placeholders only.

---

## 4. Build and run

```bash
# 1. Build images (back + front)
docker compose build

# 2. Start the stack (postgres, qdrant, backend, frontend)
docker compose up -d

# 3. Check status — all four services should be `healthy`
docker compose ps

# 4. Verify health/readiness
curl http://localhost:8000/health   # backend directly
curl http://localhost:8080/health   # via nginx proxy
curl http://localhost:8080/ready    # full readiness probe

# 5. Open the UI
open http://localhost:8080
```

Expected `/health` response:

```json
{"status":"ok","version":"0.1.0","environment":"production",
 "database":"connected","vector_store":"server","llm_provider":"none"}
```

The frontend image embeds the built SPA; it proxies `/api`, `/health`, `/ready`
to the backend so no CORS/Origin handling is needed when both are behind nginx.

### Rebuild after code changes

```bash
docker compose build backend frontend
docker compose up -d
```

### Logs & lifecycle

```bash
docker compose logs -f backend      # structured application logs
docker compose ps                   # watch health status
docker compose down                 # stop (keeps volumes)
docker compose down -v              # stop AND wipe DB + vectors (irreversible)
```

---

## 5. Going live

Not performed here (no cloud credentials available); the remaining steps are:

1. **TLS / domain** — put the frontend behind a reverse proxy with a real
   domain + TLS (e.g. Traefik, Caddy, or a cloud LB). Serve the SPA on `:443`
   and proxy `/api` to the backend container.
2. **Set `AUTH_ENABLED=true`** and a strong `AUTH_JWT_SECRET` +
   `AUTH_BOOTSTRAP_TOKEN`. Mint a token and restrict browser clients to the new
   origin in `CORS_ORIGINS`.
3. **Secrets** — rotate the default `pv:pv` DB password, `SESSION_SECRET`
   equivalents, and any `QDRANT_API_KEY` before exposure.
4. **Rate limiting** — enable `RATE_LIMIT_ENABLED=true`.
5. **Backups** — snapshot the `pgdata` and `qdrant_storage` named volumes plus
   the `./data` bind mount (uploaded documents and memory entries are in the
   SQL DB; opinion retrieval is in Qdrant).
6. **Observability** — point `OTEL_ENABLED`/`OTEL_ENDPOINT` at an OTel
   collector if you operate one.

---

## 6. Security in the deployed stack

- **MCP is local-only by design.** The MCP server speaks stdio; it is not part
  of the compose network and has no inbound port. Never expose it with an HTTP
  adapter without adding authentication and authorization.
- Uploads: only `.pdf`, size-capped at 25 MB, magic-byte checked (`%PDF`),
  stored under `UPLOAD_DIR` with hashed filenames, and scanned for path
  traversal.
- Unhandled exceptions return a generic message while full detail goes to the
  structured log (`DETAIL_ERRORS=false` default; never disable in prod).
- See [security_evaluation.md](security_evaluation.md) (Phase 22) and the
  threat model for the adversarial test results.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `backend` stays `(health: starting)` | DB schema migration / connection error | `docker compose logs backend`; check `DATABASE_URL`, Postgres reachable |
| `function datetime(unknown) does not exist` | (fixed in current images) schema_migrations default was SQLite-only | rebuild images with `docker compose build backend` |
| `/ready` reports `vector_store: unavailable` | Qdrant not up | `docker compose up -d qdrant`; wait for healthy |
| `Database is unreachable at startup` | Postgres not healthy before backend | restart backend: `docker compose restart backend` |
| Upload fails | file > 25 MB or not a real PDF | check nginx `client_max_body_size 30m` and backend `MAX_UPLOAD_SIZE_MB` |
| Empty case list on fresh deploy | production DB starts empty by design | upload cases via the UI or load a dataset through the API |

## 8. Rollback

- Database: restore the last `pgdata` snapshot before resuming.
- Vectors: restore `qdrant_storage`; a mismatched DB/vector version can be
  reconciled by re-running indexing on the stored documents.
- Code: keep the last tagged image, then `docker compose up -d backend
  procurement-verifier-backend:<previous-tag>` (or re-tag the previous build).