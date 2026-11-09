# AllMemoryMCP

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![MCP: Streamable HTTP](https://img.shields.io/badge/MCP-Streamable%20HTTP-blueviolet.svg)](https://modelcontextprotocol.io/)
[![Storage: SQLite / PostgreSQL](https://img.shields.io/badge/storage-SQLite%20%7C%20PostgreSQL-lightgrey.svg)](#storage-and-semantic-search)
[![Status: pre-1.0](https://img.shields.io/badge/status-pre--1.0%20%2F%20experimental-orange.svg)](#project-status)

**A shared, tenant-isolated memory service for AI agents.**

AllMemoryMCP gives several agents one place to store what they learn. Every memory keeps its uploader's identity, so an agent reading a shared fact can tell *which* agent contributed it — and only that uploader can change or withdraw it. Agents can also discuss disagreements in append-only negotiation threads without silently rewriting shared memory. A read-only web viewer lets a human inspect the same space.

### Copy to your AGENT

Paste this prompt into your coding agent — it will read the repository, install and deploy the service, and guide you through using it (GitHub's copy button is in the top-right corner of the box):

```markdown
Read this GitHub repository, install and deploy it for me, and guide me step by step through using it: https://github.com/mohui1999/AllMemoryMCP
```

**Languages:** English (canonical) | [简体中文](docs/i18n/README.zh-CN.md) | [日本語](docs/i18n/README.ja.md) | [한국어](docs/i18n/README.ko.md) | [Deutsch](docs/i18n/README.de.md) | [Français](docs/i18n/README.fr.md) | [Русский](docs/i18n/README.ru.md)

> Localized guides live in [`docs/i18n/`](docs/i18n/). Simplified Chinese mirrors this document in full; the other five languages are condensed guides. This English README is the reference for the current implementation — see [Contributing](#contributing) if you would like to help keep translations in sync.

## Why

Agents that work on the same project usually keep separate, private notes. The result is duplicated research, contradictory assumptions, and no way to tell where a claim came from. A shared vector store alone does not solve this: it has no notion of *who* wrote a fact or *who* is allowed to retract it.

AllMemoryMCP is a small, self-hostable service built around three rules:

1. **Shared within a tenant, isolated across tenants.** Any authenticated agent in a tenant can read all of that tenant's memories; no agent can see another tenant's data.
2. **Provenance and ownership are first-class.** Reads return `owner_agent_id`; writes are only permitted to the uploader.
3. **Discussion is not memory.** Negotiation threads are append-only records. Reaching a conclusion never mutates a memory by itself — an agent must explicitly write the conclusion.

It speaks both REST and the [Model Context Protocol](https://modelcontextprotocol.io/), so it works with MCP-capable agents, plain HTTP clients, and a browser.

## Repository layout

```text
alembic/       Database schema migrations
deploy/        Reverse proxy, process manager, and backup examples
docs/i18n/     Localized README files
web/           Read-only memory viewer
app.py         REST API and browser routes
mcp_server.py  MCP server and stdio bridge
memory_service.py, credentials.py, db.py, embeddings.py
               Shared service, access control, persistence, and search logic
```

Runtime modules stay at the project root because the documented local and container entry points import them directly.

## Project status

**Pre-1.0 / experimental** (API version `0.3.0`, November 2026). The core service is implemented and scripted checks pass on SQLite, but several paths have not been exercised against real infrastructure. Please read this before deploying to production.

| Verified by execution (2026-11-09) | Not yet exercised |
|---|---|
| Memory CRUD, ownership rejection, soft deletion, audit records | PostgreSQL backend and the pgvector / HNSW path |
| Two-tenant isolation across reads, writes, threads and messages | OpenAI embedding calls (code path only) |
| Managed credentials: issue, verify, revoke, rotate, read-only scope | MCP handshake over Streamable HTTP and stdio |
| REST routes via FastAPI `TestClient`, including 401/403 paths | Full browser sign-in against a live server (UI rendering checked in headless Chrome with API fixtures) |
| Alembic `0001 → 0002 → 0003` on SQLite; `compileall`; `node --check` | Backup restore, concurrency / load testing |

There is currently **no automated test suite committed to the repository** — the checks above were run as local scripts. Adding `pytest` coverage is one of the most valuable contributions (see [Roadmap](#roadmap-and-known-limitations)).

The source is published at [github.com/mohui1999/AllMemoryMCP](https://github.com/mohui1999/AllMemoryMCP). Fork the repository to contribute; see [Contributing](#contributing).

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [When not to use it](#when-not-to-use-it)
- [Quick start (local)](#quick-start-local)
- [Docker Compose deployment](#docker-compose-deployment)
- [Connect an agent](#connect-an-agent)
- [REST API](#rest-api)
- [MCP tools](#mcp-tools)
- [Read-only browser viewer](#read-only-browser-viewer)
- [Configuration](#configuration)
- [Storage and semantic search](#storage-and-semantic-search)
- [Contributing](#contributing)
- [Roadmap and known limitations](#roadmap-and-known-limitations)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Acknowledgements](#acknowledgements)
- [License](#license)

## Features

- **Two agent interfaces:** REST API and remote MCP over Streamable HTTP, plus a local stdio bridge for clients that launch MCP servers as subprocesses.
- **Tenant isolation enforced in SQL**, not just in application checks — every read and write carries a `tenant_id` condition.
- **Provenance and ownership:** every result includes `owner_agent_id`; only the uploader can update or soft-delete a memory; all writes produce audit records.
- **Append-only negotiation:** `direct` threads (creator + named recipients) and tenant-wide `global` threads, with creator-controlled `resolved` / `closed` status.
- **Managed credentials:** issue tokens at runtime with `read` / `write` scopes, optional expiry, revocation and rotation. Only SHA-256 hashes are stored; raw tokens appear once.
- **Read-only browser viewer:** memory search, source-agent and type filters, pagination, detail panel with copyable IDs, negotiation timelines, stat overview, light/dark themes and a responsive layout; login requires a managed read-only credential.
- **Pluggable storage:** SQLite for local / single-host use; PostgreSQL with optional pgvector semantic search via `DATABASE_URL`.
- **Batteries-included deployment:** Dockerfile, Compose, Supervisor, nginx / Caddy examples, and a backup script.

## How it works

```mermaid
flowchart LR
  A[AI Agents] -->|REST / Bearer| R[FastAPI REST + viewer :8099]
  A -->|MCP / Bearer| M[Streamable HTTP MCP :8001]
  A -->|stdio| S[Local MCP bridge]
  U[Human browser] -->|read-only credential| R
  R --> C[Shared service layer<br/>tenant + scope + ownership]
  M --> C
  S --> C
  C --> D[(SQLite or PostgreSQL)]
```

REST, MCP, and the viewer call the same service layer and database — there is no second source of truth.

| Concept | Meaning |
|---|---|
| Tenant | An isolated memory space, identified by `tenant_id` carried on every row. |
| Principal | An authenticated identity: `(tenant_id, agent_id, scopes)`. Derived from the Bearer token, never supplied by the caller. |
| Memory | A text record with `memory_type`, `tags`, `version`, timestamps and an `owner_agent_id`. Deletion is soft (`deleted_at`). |
| Negotiation | A `direct` or `global` thread containing append-only messages. Closing a thread never edits a memory. |
| Scope | `read` or `write`. Static tokens grant both; managed credentials declare their scopes explicitly. |

## When not to use it

Being explicit about the boundaries saves evaluation time:

- **Not a general RAG or vector database.** Default retrieval is case-insensitive keyword matching; semantic search is an optional PostgreSQL + pgvector enhancement.
- **Not a user account system.** The browser login is a read-only managed-token flow. There are no passwords, persistent sessions, self-service signup, or OAuth/OIDC.
- **Not a content-versioning system.** Soft deletion and a `version` counter exist, but historical content snapshots are not retained.
- **Not a high-concurrency datastore.** SQLite is the default for a small single-host deployment; there is no rate limiting, write idempotency, or message pagination.
- **Not multi-organization ready out of the box.** Tenants isolate data, but there is no tenant provisioning UI, membership model, or per-tenant admin delegation.

Treat memory content as untrusted input when feeding it into an agent: another agent may have written stale claims or prompt-injection text.

## Quick start (local)

Requirements: Python 3.11+ (the Docker image uses 3.12).

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`. Use a distinct, high-entropy token for each agent. `AGENT_TOKENS` is a nested JSON map of tenant IDs to agent IDs and tokens:

```dotenv
AGENT_TOKENS={"default":{"codex":"REPLACE_WITH_RANDOM_TOKEN","cursor":"REPLACE_WITH_ANOTHER_RANDOM_TOKEN"}}
DATABASE_URL=sqlite:///./data/memory.db
DATABASE_PATH=./data/memory.db
MCP_HOST=0.0.0.0
MCP_PORT=8001
MCP_TRANSPORT=streamable-http
```

Generate tokens with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. The service loads `.env` automatically; keep it private (it is excluded by `.gitignore`).

Start REST and MCP in separate terminals from the project directory:

```sh
# Terminal 1: REST API and browser viewer
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8099
```

```sh
# Terminal 2: remote MCP server
source .venv/bin/activate
python mcp_server.py
```

| Service | Local address |
|---|---|
| Browser viewer | <http://127.0.0.1:8099/> |
| REST API | <http://127.0.0.1:8099/v1> |
| MCP endpoint | <http://127.0.0.1:8001/mcp> |
| Health check | <http://127.0.0.1:8099/health> |
| Interactive API docs | <http://127.0.0.1:8099/docs> |

Both processes must point at the same `DATABASE_URL` / `DATABASE_PATH`.

## Docker Compose deployment

Compose runs REST and MCP under Supervisor in one container, persists data under `./data`, and publishes service ports on loopback only. A reverse proxy must provide public HTTPS. SQLite is the default to keep the single-host footprint small; PostgreSQL remains an opt-in backend.

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
cp .env.production.example .env
```

Before starting, replace **all** placeholder values in `.env`, especially `AGENT_TOKENS` and `ADMIN_TOKENS`. Then:

```sh
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8099/health
```

Configure one of the supplied reverse proxy examples:

| Proxy | Viewer | MCP | REST |
|---|---|---|---|
| [Caddy](deploy/Caddyfile) (automatic HTTPS) | `/` | `/mcp` | `/api/v1/...` |
| [nginx](deploy/nginx-allmemory.conf) (subpath) | `/allmemory/` | `/allmemory/mcp` | `/allmemory/api/v1/...` |

**Do not expose ports 8099 or 8001 directly to the internet.** [deploy/backup.sh](deploy/backup.sh) creates atomic SQLite `.backup` snapshots with WAL handling, or uses `pg_dump` for PostgreSQL, and retains the latest 30 snapshots by default. Verify restores as part of deployment operations — restore has not been exercised yet.

## Connect an agent

Configure the agent's MCP client with the remote endpoint and that agent's own Bearer token:

```text
MCP URL: https://<your-domain>/mcp
HTTP header: Authorization: Bearer <agent-token>
```

For the nginx subpath setup use `https://<your-domain>/allmemory/mcp`.

Give each agent a separate credential. **Never put the token in a tool argument, prompt, source file, or shared agent configuration.** Some MCP clients cannot set custom HTTP headers; use the local stdio bridge below (or a compatible OAuth flow, which is not implemented yet) for those clients.

### Reusable agent instructions

After connecting, give the agent this operational guidance:

```text
Use AllMemoryMCP for shared memory.
- Search with memory_search before creating a duplicate.
- Every memory result includes owner_agent_id; preserve that source when summarizing.
- Use memory_write to add a memory. You may update or delete only memories you uploaded.
- Use negotiation_start with channel="direct" and recipients=[...] for a private discussion, or channel="global" for a tenant-wide discussion.
- Use negotiation_post to append replies. Negotiation messages do not change memories automatically.
- Use negotiation_resolve only when the thread creator has reached a conclusion; update a memory separately if needed.
- Never claim a write succeeded until the tool confirms it.
```

### Local stdio bridge

For clients that only launch local MCP subprocesses, run one process per agent with the same database URL and tenant as the service:

```sh
MCP_TRANSPORT=stdio \
MCP_AGENT_ID=codex \
MCP_TENANT_ID=default \
DATABASE_URL=sqlite:////absolute/path/to/AllMemoryMCP/data/memory.db \
.venv/bin/python /absolute/path/to/AllMemoryMCP/mcp_server.py
```

`MCP_AGENT_ID` binds that process to one identity. Never share a single stdio process between agents.

## REST API

Replace `<agent-token>` with a token assigned to an agent in the `default` tenant.

Create a memory:

```sh
curl -X POST http://127.0.0.1:8099/v1/memories \
  -H 'Authorization: Bearer <agent-token>' \
  -H 'Content-Type: application/json' \
  -d '{"content":"The service uses PostgreSQL in production.","memory_type":"fact","tags":["deployment"]}'
```

Search memories, optionally filtering by source agent and paginating:

```sh
curl 'http://127.0.0.1:8099/v1/memories?q=PostgreSQL&owner_agent_id=codex&limit=20&offset=0' \
  -H 'Authorization: Bearer <agent-token>'
```

Start a global negotiation:

```sh
curl -X POST http://127.0.0.1:8099/v1/negotiations \
  -H 'Authorization: Bearer <agent-token>' \
  -H 'Content-Type: application/json' \
  -d '{"subject":"Review deployment choice","channel":"global","first_message":"Should we use SQLite or PostgreSQL for this workload?"}'
```

Route summary (all `/v1/*` routes require a Bearer token):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Service health (unauthenticated) |
| `GET`, `POST` | `/v1/memories` | Search/list and create memories |
| `GET`, `PATCH`, `DELETE` | `/v1/memories/{memory_id}` | Read, update, or soft-delete a memory |
| `GET`, `POST` | `/v1/negotiations` | List visible threads or start a thread |
| `GET` | `/v1/negotiations/{thread_id}` | Read a thread and its messages |
| `POST` | `/v1/negotiations/{thread_id}/messages` | Append a message |
| `PATCH` | `/v1/negotiations/{thread_id}` | Resolve or close a thread as its creator |
| `POST`, `GET` | `/v1/admin/credentials` | Issue or list managed credentials (admin token required) |
| `POST` | `/v1/admin/credentials/{id}/revoke` | Revoke a managed credential |
| `POST` | `/v1/admin/credentials/{id}/rotate` | Rotate a managed credential |

`GET /v1/...` requires the `read` scope; all writes require `write`. A missing scope returns `403`. FastAPI also serves interactive docs at `/docs` and an OpenAPI schema at `/openapi.json`.

## MCP tools

| Tool | Purpose |
|---|---|
| `memory_search` | Search tenant-visible memories and return uploader metadata |
| `memory_get` | Read a memory by ID |
| `memory_write` | Create a memory owned by the calling agent |
| `memory_update` | Update only the calling agent's memory |
| `memory_delete` | Soft-delete only the calling agent's memory |
| `negotiation_start` | Start a direct or global discussion |
| `negotiation_list` | List threads visible to the caller |
| `negotiation_post` | Append a message to a visible thread |
| `negotiation_resolve` | Resolve or close a thread created by the caller |

Tool schemas never accept credentials as arguments; identity always comes from the Bearer header (or the stdio process binding).

## Read-only browser viewer

The viewer at `/` accepts a username and a **database-managed credential with `read` scope only**. The username must match the credential's `agent_id`. The viewer rejects static environment tokens, credentials carrying `write` or `admin` scope, and admin tokens. The browser keeps the credential in memory only — refreshing or closing the page signs the user out, and revocation takes effect on the next request.

The viewer has three workspaces: **Memories** (search, source-agent and type filters, pagination, and a detail panel with full metadata), **Discussions** (direct and global threads with an append-only message timeline), and **About** (a short map of the access model). Stat cards are computed from the results currently loaded, the theme follows the system preference and can be toggled, and the sidebar becomes a bottom navigation bar on narrow screens. Every displayed value comes from the API.

The browser viewer UI is in English; localization into other languages is future work. The static assets have no build step and no external dependencies — `web/index.html`, `web/app.js` and `web/style.css` are served directly.

Set an admin token in `.env`:

```dotenv
ADMIN_TOKENS={"memory-admin":"REPLACE_WITH_RANDOM_ADMIN_TOKEN"}
```

An authorized operator issues one read-only credential per viewer:

```sh
curl -X POST http://127.0.0.1:8099/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"viewer-alice","scopes":["read"]}'
```

Give the user `viewer-alice` as the username and the returned `token` as the login credential. The raw token is returned **only once**. End access with `POST /v1/admin/credentials/<credential-id>/revoke`.

## Configuration

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy database URL | `sqlite:///./data/memory.db` |
| `DATABASE_PATH` | Legacy SQLite path / container data path | `./data/memory.db` |
| `AGENT_TOKENS` | Static `{tenant: {agent: token}}` mapping | `{}` |
| `ADMIN_TOKENS` | Static `{admin_name: token}` mapping for credential management | `{}` |
| `MCP_HOST` / `MCP_PORT` | Remote MCP bind address and port | `0.0.0.0` / `8001` |
| `MCP_TRANSPORT` | `streamable-http` or `stdio` | `streamable-http` |
| `MCP_AGENT_ID` | Identity for a local stdio process | unset |
| `MCP_TENANT_ID` | Tenant for a local stdio process | `default` |
| `EMBEDDING_PROVIDER` | Optional semantic embedding provider (`openai`) | unset |
| `OPENAI_API_KEY` | API key used only when semantic embeddings are enabled | unset |
| `OPENAI_EMBED_MODEL` | Embedding model name | `text-embedding-3-small` |
| `EMBEDDING_DIM` | Vector dimension; must match the model | `1536` |

The REST listener port (`8099`) is set in the `uvicorn` command or `deploy/supervisord.conf`, not via an environment variable.

## Storage and semantic search

SQLite is the default for local and single-host use: one file, trivial backups, and a small memory footprint. PostgreSQL is supported through SQLAlchemy and Alembic migrations. It is kept **opt-in** because the intended host is a memory-constrained single machine; a bundled PostgreSQL sidecar would add permanent memory pressure for a low-concurrency workload.

Default retrieval is case-insensitive keyword matching over `content`, `tags` and `memory_type`, ordered by update time (`limit` capped at 100, `offset` for paging). For semantic search, use PostgreSQL with pgvector plus `EMBEDDING_PROVIDER=openai` and `OPENAI_API_KEY`; embeddings are computed remotely, so no local model is loaded. If embeddings are disabled or the provider fails, search falls back to keyword matching. SQLite stores embeddings as JSON text and does not offer the vector path.

To apply PostgreSQL migrations:

```sh
DATABASE_URL='postgresql+psycopg://user:password@host:5432/allmemory' alembic upgrade head
```

The chain is `0001_initial` (multi-tenant tables) → `0002_embedding` (vector column and HNSW index on PostgreSQL) → `0003_agent_credentials` (managed credentials). SQLite development databases are created on startup via `create_all`. The compose file includes a commented-out `pgvector/pgvector:pg16` service; review and secure its credentials before enabling it.

## Contributing

Contributions are welcome, especially the ones listed in the [roadmap](#roadmap-and-known-limitations). The project is small and has no build step for the frontend, so a change can usually be validated locally in minutes.

**Development setup**

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # use throwaway tokens locally
uvicorn app:app --host 127.0.0.1 --port 8099
```

**Before opening a pull request**

- Run the checks that exist today:
  ```sh
  python -m compileall -q app.py memory_service.py credentials.py db.py embeddings.py mcp_server.py
  node --check web/app.js
  rm -f /tmp/allmemory-check.db && DATABASE_URL=sqlite:////tmp/allmemory-check.db alembic upgrade head
  ```
- Keep `tenant_id` and scope checks in **every** new query and write path. A feature that bypasses `memory_service.py` will leak data across tenants.
- Never commit `.env`, `data/`, backups, or real tokens. Both `.gitignore` and `.dockerignore` exclude them — do not weaken those rules.
- Follow the existing style: Python type hints, SQLAlchemy ORM only (no raw SQL), JSON-valued fields serialized as text, and no hardcoded credentials or hostnames.
- For a schema change, add an Alembic migration with a correct `down_revision`; do not edit an already-published migration.
- Update the README (and, where relevant, the translations) when behavior or configuration changes.

**Reporting a bug**: include the backend (SQLite or PostgreSQL), the transport used (REST / remote MCP / stdio), the exact request or tool call, the response, and a **redacted** version of the relevant configuration. Never paste a real token into an issue.

The highest-impact contribution right now is a committed `pytest` suite covering tenant isolation, ownership, scopes and the REST routes. There is no CI pipeline yet, so local verification is part of the review process.

## Roadmap and known limitations

These are candidates, not commitments or scheduled releases:

- **Planned:** Multi-user team and private memory spaces with explicit space-level access, user invitations and roles, per-space Agent grants, a scoped administration area, explicit publishing from personal to team spaces, and a safe migration of existing data into `legacy_shared` spaces.
- **Not implemented:** OAuth/OIDC login, persistent user sessions, write-capable browser UI, tenant provisioning UI, credential self-service, cross-tenant sharing.
- **Needs real-world verification:** PostgreSQL + pgvector, OpenAI embeddings, MCP handshake over Streamable HTTP and stdio, the full browser sign-in flow against a live server, backup restore, concurrency / load testing.
- **Hardening:** committed `pytest` suite and CI, write idempotency, message pagination, semantic reranking, credential rate limiting, automated restore checks.
- **Experience:** browser viewer localization beyond English.
- **Ecosystem:** an official MCP client configuration example for each major agent, and keeping the translated READMEs in sync with this one.

Remote MCP clients must be able to set an `Authorization` header until an OAuth-compatible flow exists. `ADMIN_TOKENS` remain a static root credential; rotate them operationally.

## Security

- Use HTTPS for any non-local access and keep ports 8099/8001 behind the reverse proxy, bound to `127.0.0.1`.
- Use separate high-entropy credentials for each agent and tenant. Issue `read`-only credentials for human viewers — never send a write-capable token to a viewer.
- Static `AGENT_TOKENS` / `ADMIN_TOKENS` live in environment variables. Managed credential records store only SHA-256 hashes and support expiry, revocation and rotation.
- Memory content is untrusted data. Agents consuming it should treat it as potentially stale or containing prompt injection.
- **Reporting a vulnerability:** this project does not yet have a dedicated private reporting channel. Please do not post exploit details in a public issue; open a minimal issue requesting a private channel (or contact the maintainers through the repository once it is published).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 Provide a valid Agent bearer token` | Token missing, revoked, expired, or not present in `AGENT_TOKENS` / managed credentials. |
| `403 requires scope 'write'` | The credential is `read`-only (typical for viewer credentials). Issue a read/write credential for agents. |
| Viewer login returns 401 | The username must exactly match `agent_id`, the credential must be DB-managed and `read`-only, and static tokens are rejected. |
| `404` on update/delete | The memory belongs to another agent or another tenant; ownership is intentionally not disclosed. |
| MCP client cannot connect | Confirm the client sends `Authorization`; check the reverse proxy preserves the header and keeps SSE unbuffered (`proxy_buffering off` in nginx). |
| REST and MCP see different data | The two processes are using different `DATABASE_URL` / `DATABASE_PATH` values. |
| Tables missing after switching to PostgreSQL | Run `alembic upgrade head` with the PostgreSQL `DATABASE_URL`; `create_all` only runs for SQLite. |
| `Thread is closed` on `negotiation_post` | The creator set the thread to `closed`; `resolved` threads still accept messages. |

## Acknowledgements

Built on [Model Context Protocol](https://modelcontextprotocol.io/), [FastAPI](https://fastapi.tiangolo.com/), [SQLAlchemy](https://www.sqlalchemy.org/) and [Alembic](https://alembic.sqlalchemy.org/). Semantic search uses [pgvector](https://github.com/pgvector/pgvector) when enabled; the deployment examples use [Caddy](https://caddyserver.com/), nginx and [Supervisor](http://supervisord.org/).

## License

Distributed under the [MIT License](LICENSE).

Copyright (c) 2026 AllMemoryMCP contributors.
