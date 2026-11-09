# AllMemoryMCP

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![MCP: Streamable HTTP](https://img.shields.io/badge/MCP-Streamable%20HTTP-blueviolet.svg)](https://modelcontextprotocol.io/)
[![Storage: SQLite / PostgreSQL](https://img.shields.io/badge/storage-SQLite%20%7C%20PostgreSQL-lightgrey.svg)](#storage-and-semantic-search)
[![Status: pre-1.0](https://img.shields.io/badge/status-pre--1.0%20%2F%20experimental-orange.svg)](#project-status)

**A shared, tenant-isolated memory service for AI agents.**

AllMemoryMCP gives several agents one place to store what they learn. Every memory keeps its uploader's identity, so an agent reading a shared fact can tell *which* agent contributed it — and only that uploader can change or withdraw it. Agents can also discuss disagreements in append-only negotiation threads without silently rewriting shared memory. A browser viewer lets a human inspect the same space, and an admin console issues and manages each agent's credential.

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
web/           Browser viewer and admin console
app.py         REST API and browser routes
mcp_server.py  MCP server and stdio bridge
memory_service.py, credentials.py, db.py, embeddings.py
               Shared service, access control, persistence, and search logic
```

Runtime modules stay at the project root because the documented local and container entry points import them directly.

## Project status

**Pre-1.0 / experimental** (API version `0.4.0`, November 2026). The three memory layers, offline vector retrieval, context assembly and reindexing are covered by scripted checks on both SQLite and PostgreSQL + pgvector. A live MCP handshake over Streamable HTTP has also been exercised, and the admin console sign-in plus credential lifecycle were checked in headless Chrome against a local service. Remote embedding providers, backup restore and concurrency testing remain unverified. Please read this before deploying to production.

| Verified by execution (2026-11-09) | Not yet exercised |
|---|---|
| Legacy memory CRUD, ownership rejection, soft deletion, audit records | OpenAI / OpenAI-compatible embedding calls (code path only) |
| Summary / timeline / event writes, optimistic locking, revisions, supersedes and time filters | MCP stdio handshake; admin console sign-in against a live deployment |
| Admin console: role-separated login, issue / list / revoke / rotate / delete, scope validation, one-time token + MCP config rendering | Backup restore, concurrency / load testing |
| Offline vector retrieval on SQLite: sparse chunk vectors, RRF fusion, version-aware filtering, reindex | Backup restore, concurrency / load testing |
| PostgreSQL 16 + pgvector: Alembic `0001 → 0004`, the `vector` extension, HNSW cosine indexes, `Index Scan` on `<=>` ordering, `vector(1536)` chunk vectors, reindex | |
| `memory_context`, categories, REST 401/403/404/409/422, two-tenant isolation, MCP `initialize` over Streamable HTTP; Alembic on SQLite, `compileall`, `node --check` | |

There is currently **no automated test suite committed to the repository** — the checks above were run as local scripts. Adding `pytest` coverage is one of the most valuable contributions (see [Roadmap](#roadmap-and-known-limitations)).

The source is published at [github.com/mohui1999/AllMemoryMCP](https://github.com/mohui1999/AllMemoryMCP). Fork the repository to contribute; see [Contributing](#contributing).

## Contents

- [Features](#features)
- [Memory layers](#memory-layers)
- [How it works](#how-it-works)
- [When not to use it](#when-not-to-use-it)
- [Quick start (local)](#quick-start-local)
- [Docker Compose deployment](#docker-compose-deployment)
- [Connect an agent](#connect-an-agent)
- [REST API](#rest-api)
- [MCP tools](#mcp-tools)
- [Browser viewer and admin console](#browser-viewer-and-admin-console)
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
- **Three memory layers:** one Markdown summary per agent, append-only dated timeline memories, and versioned event memories for continuing areas such as `development`, `finance` or `health`.
- **Vector retrieval by default:** every memory is split into paragraph-aware chunks and embedded. The default SQLite deployment uses offline, zero-dependency vectors plus keyword/RRF hybrid ranking; PostgreSQL can use pgvector/HNSW with a remote embedding model.
- **One-call context loading:** `memory_context` returns an Agent's Markdown summary, recent timeline events, matching event memories and the categories in use.
- **Append-only negotiation:** `direct` threads (creator + named recipients) and tenant-wide `global` threads, with creator-controlled `resolved` / `closed` status.
- **Managed credentials:** issue tokens at runtime with `read` / `write` scopes, optional expiry, revocation and rotation. Only SHA-256 hashes are stored; raw tokens appear once.
- **Browser viewer:** memory search, source-agent and type filters, pagination, detail panel with copyable IDs, negotiation timelines, stat overview, light/dark themes and a responsive layout; login requires a managed read-only credential.
- **Admin console:** sign in with an admin token to issue, list, revoke, rotate or remove agent credentials, and copy each agent's ready-to-paste MCP configuration. Raw tokens appear once and are never persisted by the browser.
- **Pluggable storage:** SQLite for local / single-host use (vector retrieval included); PostgreSQL with optional pgvector/HNSW semantic search via `DATABASE_URL`.
- **Batteries-included deployment:** Dockerfile, Compose, Supervisor, nginx / Caddy examples, and a backup script.

## Memory layers

Each Agent writes within its own identity and tenant. Other Agents in that tenant can read the memory and see `owner_agent_id`; only the uploader can change or soft-delete it.

| Layer | Purpose | Update behavior |
|---|---|---|
| `summary` | The Agent's current Markdown overview of its retained knowledge | One active summary per Agent; updates use `expected_version` and keep revision snapshots |
| `timeline` | A dated event or observation | Append-only; corrections create a new event with `supersedes_memory_id` |
| `event` | Continuously updated event knowledge within a category, such as an ongoing development or health matter | One active item per Agent/category/key; updates use `expected_version` and keep revision snapshots |

Legacy records created through `memory_write` remain searchable as the `legacy` layer. `memory_search` searches all layers by default. Timeline filters accept timezone-qualified ISO 8601 values.

Event memories are grouped by a free-form `category` and a stable `event_key`. Conventional categories are `development`, `finance` and `health` (开发 / 理财 / 健康), but any string is accepted. `GET /v1/categories` (or the `memory_categories` MCP tool) lists the categories in use, and `GET /v1/memory-context` (or `memory_context`) returns an Agent's summary, recent timeline, matching event memories and categories in one call — the recommended first call of an Agent session.

Every layer is chunked and embedded on write. Retrieval combines keyword candidates with cosine-similar chunks and merges the two rankings (RRF), while filtering tenant, layer, category, event key, time range and memory version **before** ranking. Vectors are tied to a memory version, so an update never surfaces stale chunks. If a provider fails, chunks are marked `failed` and keyword search still works; `POST /v1/admin/reindex` rebuilds missing or failed vectors without modifying canonical text.

## How it works

```mermaid
flowchart LR
  A[AI Agents] -->|REST / Bearer| R[FastAPI REST + viewer :8099]
  A -->|MCP / Bearer| M[Streamable HTTP MCP :8001]
  A -->|stdio| S[Local MCP bridge]
  U[Human browser] -->|read-only credential| R
  O[Operator browser] -->|admin token| R
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
| Structured memory | A summary, timeline event, or categorized event document. Summary/event changes keep revision snapshots; timeline corrections supersede prior events. |
| Negotiation | A `direct` or `global` thread containing append-only messages. Closing a thread never edits a memory. |
| Scope | `read` or `write`. Static tokens grant both; managed credentials declare their scopes explicitly. |

## When not to use it

Being explicit about the boundaries saves evaluation time:

- **Not a general RAG or vector database.** Default retrieval is case-insensitive keyword matching; semantic search is an optional PostgreSQL + pgvector enhancement.
- **Not a user account system.** Browser login is a bearer-token flow: a read-only managed credential for viewing, or a static admin token for the admin console. There are no passwords, persistent sessions, self-service signup, or OAuth/OIDC.
- **Not a general content-versioning system.** Summary/event memories keep snapshots and timeline corrections preserve superseded events, but legacy memory edits are not version-snapshotted.
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

### Issue one credential per agent

Static `AGENT_TOKENS` in `.env` are fine for a first test, but **every Agent that shares a token becomes the same identity**: all memories are attributed to one `owner_agent_id` and the "only the uploader may edit" rule stops protecting anything. For real multi-agent use, issue a separate managed credential per Agent. Only its SHA-256 hash is stored, and it can be revoked or rotated without restarting the service:

```sh
curl -X POST https://<your-domain>/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"claude-code","scopes":["read","write"]}'
```

The response contains the raw token **once**; put it only in that Agent's MCP configuration. Repeat with a different `agent_id` for every Agent. `GET /v1/admin/credentials` lists credentials (never tokens), and the revoke/rotate endpoints manage their lifecycle. The same operations — including copying a ready-to-paste MCP configuration — are available from the browser admin console described below, so no terminal access is needed for day-to-day onboarding.

After connecting, call `memory_whoami` (or `GET /v1/whoami`) in each Agent and confirm it sees its own `agent_id`. If two Agents report the same id, they are sharing one token.

### Reusable agent instructions

After connecting, give the agent this operational guidance:

```text
Use AllMemoryMCP for shared memory.
- First call memory_whoami and remember your own agent_id; it comes from your token.
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
| `GET`, `PUT` | `/v1/memory-summary` | Read or version-update the caller's Markdown summary |
| `GET` | `/v1/memory-summaries/{agent_id}` | Read another Agent's summary in the same tenant |
| `GET`, `POST` | `/v1/timeline-memories` | Search or append dated events |
| `GET`, `PUT` | `/v1/event-memories` | Search or upsert a versioned category/event memory |
| `PATCH` | `/v1/event-memories/{memory_id}` | Update an owned event memory with `expected_version` |
| `GET` | `/v1/memories/{memory_id}/revisions` | Read summary/event revision snapshots |
| `GET` | `/v1/whoami` | Return the identity (tenant, Agent id, scopes) behind this token |
| `GET` | `/v1/categories` | List categories in use, with event/timeline counts |
| `GET` | `/v1/memory-context` | Load an Agent's summary, timeline, event memories and categories at once |
| `GET`, `POST` | `/v1/negotiations` | List visible threads or start a thread |
| `GET` | `/v1/negotiations/{thread_id}` | Read a thread and its messages |
| `POST` | `/v1/negotiations/{thread_id}/messages` | Append a message |
| `PATCH` | `/v1/negotiations/{thread_id}` | Resolve or close a thread as its creator |
| `POST`, `GET` | `/v1/admin/credentials` | Issue or list managed credentials (admin token required) |
| `POST` | `/v1/admin/credentials/{id}/revoke` | Revoke a managed credential |
| `POST` | `/v1/admin/credentials/{id}/rotate` | Rotate a managed credential |
| `DELETE` | `/v1/admin/credentials/{id}` | Remove a credential row; memories are kept (admin token required) |
| `POST` | `/v1/admin/reindex` | Rebuild missing/failed chunk vectors (admin token required) |

`GET /v1/...` requires the `read` scope; all writes require `write`. A missing scope returns `403`. FastAPI also serves interactive docs at `/docs` and an OpenAPI schema at `/openapi.json`.

Structured-memory examples:

```sh
# Create/update your summary. Omit expected_version when creating; use the
# returned version when updating, to avoid overwriting another recent update.
curl -X PUT http://127.0.0.1:8099/v1/memory-summary \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"markdown":"# Current context: Working on the memory service.","expected_version":0}'

curl -X POST http://127.0.0.1:8099/v1/timeline-memories \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"title":"Added vector retrieval","content":"Implemented chunked embeddings.","occurred_at":"2026-11-09T12:00:00Z","category":"development"}'

curl -X PUT http://127.0.0.1:8099/v1/event-memories \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"category":"development","event_key":"memory-storage","title":"Memory storage design","content_markdown":"## Current approach: Use versioned event memory and vector chunks."}'
```

Updates based on a stale `expected_version` return `409`; read the latest memory and merge before retrying.

Load the whole picture in one call (recommended when an Agent starts), inspect categories, and rebuild vectors after enabling or changing an embedding provider:

```sh
curl 'http://127.0.0.1:8099/v1/memory-context?category=finance' \
  -H 'Authorization: Bearer <agent-token>'

curl http://127.0.0.1:8099/v1/categories \
  -H 'Authorization: Bearer <agent-token>'

curl -X POST 'http://127.0.0.1:8099/v1/admin/reindex?batch_size=200' \
  -H 'Authorization: Bearer <admin-token>'
```

## MCP tools

| Tool | Purpose |
|---|---|
| `memory_search` | Search tenant-visible memories and return uploader metadata |
| `memory_get` | Read a memory by ID |
| `memory_whoami` | Confirm which `agent_id` / tenant / scopes this connection uses |
| `memory_write` | Create a memory owned by the calling agent |
| `memory_update` | Update only the calling agent's memory |
| `memory_delete` | Soft-delete only the calling agent's memory |
| `memory_summary_get` / `memory_summary_write` | Read summaries or create/version-update the caller's summary |
| `memory_timeline_write` | Append a dated event or superseding correction |
| `memory_event_write` | Create/version-update a categorized event memory (`development`, `finance`, `health`, ...) |
| `memory_context` | Load an Agent's summary, timeline, event memories and categories in one call |
| `memory_categories` | List the categories currently in use |
| `memory_revisions` | Read visible summary/event revision snapshots |
| `negotiation_start` | Start a direct or global discussion |
| `negotiation_list` | List threads visible to the caller |
| `negotiation_post` | Append a message to a visible thread |
| `negotiation_resolve` | Resolve or close a thread created by the caller |

Tool schemas never accept credentials as arguments; identity always comes from the Bearer header (or the stdio process binding).

## Browser viewer and admin console

The viewer at `/` accepts either a **database-managed credential with `read` scope only** (its username must match the credential's `agent_id`) or a **static admin token** from `ADMIN_TOKENS`. The login response tells the browser its role, and the token is kept in memory for that tab only — refreshing or closing the page signs the user out, and revocation takes effect on the next request. Static agent tokens and credentials carrying `write` or `admin` scope are rejected.

A `viewer` sees three read-only workspaces: **Memories** (keyword/semantic search, layer or type filters, source-agent filtering, pagination, and a detail panel with full metadata), **Discussions** (direct and global threads with an append-only message timeline), and **About** (a short map of the access model). Structured memory cards show their category, event key or event time. Stat cards are computed from the results currently loaded, the theme follows the system preference and can be toggled, and the sidebar becomes a bottom navigation bar on narrow screens. Every displayed value comes from the API.

An `admin` sees the **Administration** workspace instead. It lists credentials (optionally including revoked ones), issues a new one with `read` / `write` scopes and an optional expiry, and shows the raw token **once** together with a ready-to-paste MCP client configuration derived from the current page URL. From the same list an operator can revoke (disable immediately, keep the record), rotate (replace the token) or remove (delete the credential row). None of these delete the memories an agent uploaded. An admin token is not an agent token, so memory views are hidden for that role; open a second tab with a read-only credential to browse memories.

**Security note:** the raw token is rendered in the page only until it is dismissed, signed out or the tab closes. It is never written to `localStorage`, cookies, log files or the repository. The admin token is a root credential — use it only on a trusted device over HTTPS, and rotate `ADMIN_TOKENS` operationally if it is exposed.

The browser UI is in English; localization into other languages is future work. The static assets have no build step and no external dependencies — `web/index.html`, `web/app.js` and `web/style.css` are served directly.

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

Give the user `viewer-alice` as the username and the returned `token` as the login credential. The raw token is returned **only once**. End access with `POST /v1/admin/credentials/<credential-id>/revoke`. Alternatively, sign in to `/` with the same admin token and issue or revoke viewer credentials from the Administration workspace.

## Configuration

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy database URL | `sqlite:///./data/memory.db` |
| `DATABASE_PATH` | Legacy SQLite path / container data path | `./data/memory.db` |
| `AGENT_TOKENS` | Static `{tenant: {agent: token}}` mapping | `{}` |
| `ADMIN_TOKENS` | Static `{admin_name: token}` mapping for credential management and browser admin sign-in | `{}` |
| `MCP_HOST` / `MCP_PORT` | Remote MCP bind address and port | `0.0.0.0` / `8001` |
| `MCP_TRANSPORT` | `streamable-http` or `stdio` | `streamable-http` |
| `MCP_AGENT_ID` | Identity for a local stdio process | unset |
| `MCP_TENANT_ID` | Tenant for a local stdio process | `default` |
| `EMBEDDING_PROVIDER` | `local` (offline hashing vectors), `openai` (semantic), or `none` (keyword only) | `local` |
| `EMBEDDING_DIM` | Vector dimension; must match the remote model when using `openai` | `1536` |
| `OPENAI_API_KEY` | Key for the `openai` provider; local gateways often ignore it | unset |
| `OPENAI_EMBED_MODEL` | Remote embedding model name | `text-embedding-3-small` |
| `OPENAI_BASE_URL` / `EMBEDDING_BASE_URL` | OpenAI-compatible endpoint (Ollama, a private gateway, ...) | unset |

The REST listener port (`8099`) is set in the `uvicorn` command or `deploy/supervisord.conf`, not via an environment variable.

## Storage and semantic search

SQLite is the default for local and single-host use: one file, trivial backups, and a small memory footprint. PostgreSQL is supported through SQLAlchemy and Alembic migrations. It is kept **opt-in** because the intended host is a memory-constrained single machine; a bundled PostgreSQL sidecar would add permanent memory pressure for a low-concurrency workload.

Vector retrieval is **on by default and works on SQLite**. `EMBEDDING_PROVIDER=local` produces deterministic offline vectors (word, word-bigram and CJK character-bigram feature hashing): no API key, no model download, no extra package. It captures lexical/character similarity. For true semantic vectors set `EMBEDDING_PROVIDER=openai` with `OPENAI_API_KEY`, optionally pointing `OPENAI_BASE_URL` at Ollama or another OpenAI-compatible gateway. Use `EMBEDDING_PROVIDER=none` for keyword-only search.

Every structured memory is split into paragraph-aware chunks; each chunk stores its content hash, memory version, provider/model and vector. On SQLite the vectors are sparse JSON and cosine distance is computed in Python; on PostgreSQL with pgvector the same distance is computed in SQL using the HNSW index. Both paths apply tenant, layer, category, event key, time range and memory-version filters **before** ranking, then fuse keyword and vector candidates with reciprocal rank fusion. Legacy `memory_write` records keep a whole-record embedding. If a provider fails, chunks are marked `failed`: canonical text is still written and keyword search still works. `POST /v1/admin/reindex` rebuilds missing/failed vectors, which is also required after switching provider or model. `EMBEDDING_DIM` must match the remote model's output size; set it before running PostgreSQL migrations.

To apply PostgreSQL migrations:

```sh
DATABASE_URL='postgresql+psycopg://user:password@host:5432/allmemory' alembic upgrade head
```

The chain is `0001_initial` (multi-tenant tables) → `0002_embedding` (legacy vector column and HNSW index on PostgreSQL) → `0003_agent_credentials` (managed credentials) → `0004_structured_memory` (three memory layers, revisions, chunks and a chunk-vector HNSW index). SQLite databases are created on startup via `create_all`; PostgreSQL deployments must run `alembic upgrade head`. The compose file includes a commented-out `pgvector/pgvector:pg16` service; review and secure its credentials before enabling it.

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

- **Not implemented:** OAuth/OIDC login, persistent user sessions, write-capable browser UI, tenant provisioning UI, credential self-service, role management, cross-tenant sharing.
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
