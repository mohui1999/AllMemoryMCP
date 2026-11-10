# AllMemoryMCP

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![MCP: Streamable HTTP](https://img.shields.io/badge/MCP-Streamable%20HTTP-blueviolet.svg)](https://modelcontextprotocol.io/)
[![Storage: SQLite / PostgreSQL](https://img.shields.io/badge/storage-SQLite%20%7C%20PostgreSQL-lightgrey.svg)](#storage-and-semantic-search)
[![Status: pre-1.0](https://img.shields.io/badge/status-pre--1.0%20%2F%20experimental-orange.svg)](#project-status)

**A shared, tenant-isolated memory service for AI agents.**

AllMemoryMCP gives several agents one place to store what they learn. Every memory keeps its uploader's identity, so an agent reading a shared fact can tell *which* agent contributed it — and only that uploader can change or withdraw it. Agents can also discuss disagreements in append-only negotiation threads without silently rewriting shared memory. A single browser console lets an administrator inspect the same space, delete records and manage each agent's credential, with a session that survives a page refresh.

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
web/           Browser console (single account, cookie session)
app.py         REST API and browser routes
mcp_server.py  MCP server and stdio bridge
memory_service.py, credentials.py, db.py, embeddings.py
               Shared service, access control, persistence, and search logic
```

Runtime modules stay at the project root because the documented local and container entry points import them directly.

## Project status

**Pre-1.0 / experimental** (API version `0.6.0`, November 2026). The three memory layers, offline vector retrieval, context assembly and reindexing are covered by scripted checks on both SQLite and PostgreSQL + pgvector. A live MCP handshake over Streamable HTTP has also been exercised, and the unified browser console (cookie session, CSRF guard, credential lifecycle, memory deletion) is covered by scripted checks against a local service. Remote embedding providers, backup restore and concurrency testing remain unverified. Please read this before deploying to production.

| Verified by execution (2026-11-09) | Not yet exercised |
|---|---|
| Legacy memory CRUD, ownership rejection, soft deletion, audit records | OpenAI / OpenAI-compatible embedding calls (code path only) |
| Summary / timeline / event writes, optimistic locking, revisions, supersedes and time filters | MCP stdio handshake; browser session restore against a live deployment |
| Unified console: one admin account, HttpOnly cookie session, refresh restore, CSRF guard, issue / list / revoke / rotate / delete credentials, admin memory deletion | Backup restore, concurrency / load testing |
| Offline vector retrieval on SQLite: sparse chunk vectors, RRF fusion, version-aware filtering, reindex | Backup restore, concurrency / load testing |
| PostgreSQL 16 + pgvector: Alembic `0001 → 0004`, the `vector` extension, HNSW cosine indexes, `Index Scan` on `<=>` ordering, `vector(1536)` chunk vectors, reindex | |
| `memory_context`, categories, REST 401/403/404/409/422, two-tenant isolation, MCP `initialize` over Streamable HTTP; Alembic on SQLite, `compileall`, `node --check` | |
| `0.5.0`: `memory_guide`, `memory_agents` / `/v1/agents`, summary-first ordering, one-summary auto-version replacement, `detail_level` context, scripted checks on SQLite | |
| `0.6.0`: unified admin account, HttpOnly cookie session with refresh restore, CSRF guard, console memory deletion, all four views for one identity; 52 session checks | |

There is currently **no automated test suite committed to the repository** — the checks above were run as local scripts. Adding `pytest` coverage is one of the most valuable contributions (see [Roadmap](#roadmap-and-known-limitations)).

The source is published at [github.com/mohui1999/AllMemoryMCP](https://github.com/mohui1999/AllMemoryMCP). Fork the repository to contribute; see [Contributing](#contributing).

## Contents

- [Features](#features)
- [Memory layers](#memory-layers)
- [Agent reading protocol](#agent-reading-protocol)
- [How it works](#how-it-works)
- [When not to use it](#when-not-to-use-it)
- [Quick start (local)](#quick-start-local)
- [Docker Compose deployment](#docker-compose-deployment)
- [Connect an agent](#connect-an-agent)
- [REST API](#rest-api)
- [MCP tools](#mcp-tools)
- [Browser console](#browser-console)
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
- **Three memory layers with an explicit contract:** one long, whole-memory Markdown summary per agent, append-only dated timeline memories, and versioned event memories for continuing areas such as `development`, `finance` or `health`. Agents can fetch the contract at runtime with `memory_guide`.
- **Summary-first reading:** `memory_agents` lists every agent together with its single overview, so another agent knows what it is doing before pulling detailed event/timeline rows. Summaries also rank first in layer-free listing and receive a small boost in hybrid search.
- **Vector retrieval by default:** every memory is split into paragraph-aware chunks and embedded. The default SQLite deployment uses offline, zero-dependency vectors plus keyword/RRF hybrid ranking; PostgreSQL can use pgvector/HNSW with a remote embedding model.
- **One-call context loading:** `memory_context` returns an Agent's Markdown summary, categories, counts and ready-to-use follow-up calls; `detail_level=summary` keeps the first read small, while `detail_level=full` also returns timeline and event rows.
- **Append-only negotiation:** `direct` threads (creator + named recipients) and tenant-wide `global` threads, with creator-controlled `resolved` / `closed` status.
- **Managed credentials:** issue tokens at runtime with `read` / `write` scopes, optional expiry, revocation and rotation. Only SHA-256 hashes are stored; raw tokens appear once.
- **Unified browser console:** one administrator credential opens every workspace — memory search and filters, discussion timelines, soft-deleting any memory, and issuing / revoking / rotating / removing agent credentials. Sign-in is kept in an HttpOnly `SameSite=Lax` cookie, so refreshing the page does not require entering the token again.
- **Session CSRF guard:** browser writes must carry an `X-AllMemory-Web: 1` header and a matching `Origin`; Bearer clients (Agents / MCP) are unaffected.
- **Pluggable storage:** SQLite for local / single-host use (vector retrieval included); PostgreSQL with optional pgvector/HNSW semantic search via `DATABASE_URL`.
- **Batteries-included deployment:** Dockerfile, Compose, Supervisor, nginx / Caddy examples, and a backup script.

## Memory layers

Each Agent writes within its own identity and tenant. Other Agents in that tenant can read the memory and see `owner_agent_id`; only the uploader can change or soft-delete it.

| Layer | What it actually means | How many | Update behavior |
|---|---|---|---|
| `summary` | **One overview of ALL of that Agent's memories.** A long Markdown digest: who it is, what it is working on, key decisions, conventions, current state and pointers to details. It is *not* a single memory entry — it is the whole picture, and the first thing another Agent should read. | **Exactly ONE active row per `(tenant_id, agent_id)`**, enforced by a partial unique index | Writing **replaces the entire document** and keeps a revision snapshot. Up to 100 000 characters. `expected_version` is optional for a single writer. |
| `timeline` | **Time memory.** An append-only, dated log entry answering "what happened, and when" (`occurred_at` must be timezone-qualified ISO 8601; optional `category`). | Many rows per Agent | Immutable; a correction appends a new row with `supersedes_memory_id`. |
| `event` | **Event memory.** The CURRENT STATE of one long-running subject, keyed by `(category, event_key)` — for example `(development, AllMemoryMCP)`, `(finance, long-term allocation)`, `(health, allergy treatment)`. A living document, not a log entry. | One active row per `(tenant_id, agent_id, category, event_key)` | Versioned upsert; revisions are kept; pass `expected_version` when writers may race. |
| `legacy` | Older free-form records created through `memory_write`. | Many rows per Agent | Normal versioned updates; kept for compatibility. |

The summary is deliberately allowed to be long: it is the digest an Agent maintains so that *other* Agents can understand its work in one read. **Do not create a second summary to add information — replace the existing one.** Other Agents can read it but never replace or delete it.

`GET /v1/memory-guide` (or the `memory_guide` MCP tool) returns this contract in machine-readable form — layer meanings (English and Chinese), cardinality, length limits, read/write tools, reading order and rules — so an Agent never has to guess what `summary`, `timeline` or `event` mean. `GET /v1/whoami`, `memory_whoami`, `memory_context` and `memory_search` also carry a compact layer reminder in their responses.

Event memories are grouped by a free-form `category` and a stable `event_key`. Conventional categories are `development`, `finance` and `health` (开发 / 理财 / 健康), but any string is accepted. `GET /v1/categories` (or `memory_categories`) lists the categories in use.

Every layer is chunked and embedded on write. Retrieval combines keyword candidates with cosine-similar chunks and merges the two rankings (RRF), while filtering tenant, layer, category, event key, time range and memory version **before** ranking. Summaries are placed first when listing without a query and receive a small relevance boost in hybrid search. Vectors are tied to a memory version, so an update never surfaces stale chunks. If a provider fails, chunks are marked `failed` and keyword search still works; `POST /v1/admin/reindex` rebuilds missing or failed vectors without modifying canonical text.

## Agent reading protocol

The layers are a **progressive disclosure ladder**, not three equal buckets. A newly connected Agent should:

1. **`memory_whoami`** — confirm which identity this connection uses. The response also carries a compact reminder of the three layers.
2. **`memory_agents`** / `GET /v1/agents` — read **every** Agent's single summary plus its timeline/event counts in one call. This answers "who is doing what?" without pulling thousands of detail rows. Use `summary_mode=preview` for large tenants.
3. **`memory_summary_get(agent_id=...)`** / `GET /v1/memory-summaries/{agent_id}` — read the full, potentially long overview of the Agent you care about. **This is the highest-priority source.**
4. **Only then fetch details** — `memory_search(memory_layer="event", category=..., owner_agent_id=...)` for a subject's current state, `memory_search(memory_layer="timeline", occurred_from=..., occurred_to=...)` for dated history, or `memory_revisions(memory_id=...)` for how an overview/subject changed.

`memory_context(detail_level="summary")` / `GET /v1/memory-context?detail_level=summary` bundles step 3 with layer descriptions, counts, `reading_order` and concrete `next_calls`, so a client can copy the next request instead of inventing it. `detail_level="full"` (the default, backward compatible) also returns recent timeline and matching event rows.

Writing is the reverse order: append dated facts with `memory_timeline_write`, keep subject state current with `memory_event_write`, then refresh the **one** summary with `memory_summary_write` so the global overview stays accurate.

| What you need | Read first | Then call |
|---|---|---|
| Understand another Agent / the team | `memory_agents` | `memory_summary_get(agent_id=...)` |
| The current state of one project or topic | that Agent's summary | `memory_search(memory_layer="event", category=..., event_key=...)` |
| What happened during a period | that Agent's summary | `memory_search(memory_layer="timeline", occurred_from=..., occurred_to=...)` |
| How an overview changed | that Agent's summary | `memory_revisions(memory_id=...)` |
| Unsure which layer to use | `memory_guide` | follow `reading_order` / `writing_order` |

## How it works

```mermaid
flowchart LR
  A[AI Agents] -->|REST / Bearer| R[FastAPI REST + console :8099]
  A -->|MCP / Bearer| M[Streamable HTTP MCP :8001]
  A -->|stdio| S[Local MCP bridge]
  U[Human browser] -->|admin token -> HttpOnly session cookie| R
  R --> C[Shared service layer<br/>tenant + scope + ownership]
  M --> C
  S --> C
  C --> D[(SQLite or PostgreSQL)]
```

REST, MCP, and the browser console call the same service layer and database — there is no second source of truth.

| Concept | Meaning |
|---|---|
| Tenant | An isolated memory space, identified by `tenant_id` carried on every row. |
| Principal | An authenticated identity: `(tenant_id, agent_id, scopes)`. Derived from the Bearer token, never supplied by the caller. |
| Memory | A text record with `memory_type`, `tags`, `version`, timestamps and an `owner_agent_id`. Deletion is soft (`deleted_at`). |
| Structured memory | A summary, timeline event, or categorized event document. Summary/event changes keep revision snapshots; timeline corrections supersede prior events. |
| Negotiation | A `direct` or `global` thread containing append-only messages. Closing a thread never edits a memory. |
| Scope | `read` or `write` for Agents. Static tokens grant both; managed credentials declare their scopes explicitly. The console session additionally holds `admin`, which is never issued to an Agent credential. |

## When not to use it

Being explicit about the boundaries saves evaluation time:

- **Not a general RAG or vector database.** Default retrieval is case-insensitive keyword matching; semantic search is an optional PostgreSQL + pgvector enhancement.
- **Not a user account system.** The browser has one account type: a static `ADMIN_TOKENS` credential, exchanged at `/viewer/login` for an HttpOnly cookie session. There are no passwords, per-user roles, self-service signup, or OAuth/OIDC. Agent credentials are for MCP/REST clients, not for browser sign-in.
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
# Terminal 1: REST API and browser console
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
| Browser console | <http://127.0.0.1:8099/> |
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

| Proxy | Console | MCP | REST |
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

The response contains the raw token **once**; put it only in that Agent's MCP configuration. Repeat with a different `agent_id` for every Agent. `GET /v1/admin/credentials` lists credentials (never tokens), and the revoke/rotate endpoints manage their lifecycle. The same operations — including copying a ready-to-paste MCP configuration — are available from the browser console described below, so no terminal access is needed for day-to-day onboarding.

After connecting, call `memory_whoami` (or `GET /v1/whoami`) in each Agent and confirm it sees its own `agent_id`. If two Agents report the same id, they are sharing one token.

### Reusable agent instructions

After connecting, give the agent this operational guidance:

```text
Use AllMemoryMCP for shared memory. Call memory_guide if you are unsure about a layer.

MEMORY MODEL (three layers):
- summary = EXACTLY ONE long Markdown document per Agent. It is the overview of
  ALL of that Agent's memories (what it is, what it is working on, decisions,
  conventions, pointers to details), NOT one memory entry. It may be long.
  Read it FIRST to understand another Agent. Writing REPLACES the whole document
  and never creates a second summary; only the owning Agent may write it.
- event = the CURRENT STATE of one long-running subject, keyed by
  (category, event_key), e.g. (development, my-project), (finance, allocation),
  (health, treatment). A living, versioned document for detailed context.
- timeline = an append-only dated log entry with occurred_at, answering what
  happened and when. Immutable; corrections append a row that supersedes the old one.

READ in this order (progressive disclosure):
1. memory_whoami                       - confirm your own identity.
2. memory_agents                       - see EVERY Agent's one summary at once.
3. memory_summary_get(agent_id=...)    - the full overview of the Agent you need.
4. ONLY THEN memory_search(memory_layer="event"|"timeline", owner_agent_id=...)
   for the details. memory_context(detail_level="summary") bundles step 3 with
   layer descriptions and ready-to-use next_calls.

WRITE in this order:
1. memory_timeline_write   - append dated facts as they happen.
2. memory_event_write      - keep each subject's current state up to date.
3. memory_summary_write    - refresh your ONE overview of everything.
   expected_version is optional for a single writer.

- Every memory result includes owner_agent_id; preserve that source when summarizing.
- You may update or delete only memories you uploaded. memory_write creates a
  legacy free-form record; prefer the three layers above.
- Use negotiation_start / negotiation_post / negotiation_resolve for discussions;
  messages never change memories automatically. Update a memory separately.
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
| `GET`, `PUT` | `/v1/memory-summary` | Read or REPLACE the caller's ONE long Markdown summary |
| `GET` | `/v1/memory-summaries/{agent_id}` | Read another Agent's summary in the same tenant |
| `GET`, `POST` | `/v1/timeline-memories` | Search or append dated events |
| `GET`, `PUT` | `/v1/event-memories` | Search or upsert a versioned category/event memory |
| `PATCH` | `/v1/event-memories/{memory_id}` | Update an owned event memory with `expected_version` |
| `GET` | `/v1/memories/{memory_id}/revisions` | Read summary/event revision snapshots |
| `GET` | `/v1/whoami` | Return the identity (tenant, Agent id, scopes) behind this token, with a layer reminder |
| `GET` | `/v1/memory-guide` | The machine-readable three-layer contract (meanings, cardinality, reading/writing order) |
| `GET` | `/v1/agents` | Directory of all Agents in this tenant WITH each one's single summary |
| `GET` | `/v1/categories` | List categories in use, with event/timeline counts |
| `GET` | `/v1/memory-context` | Load an Agent's summary first; `detail_level=summary` omits detail rows and returns `next_calls` |
| `GET`, `POST` | `/v1/negotiations` | List visible threads or start a thread |
| `GET` | `/v1/negotiations/{thread_id}` | Read a thread and its messages |
| `POST` | `/v1/negotiations/{thread_id}/messages` | Append a message |
| `PATCH` | `/v1/negotiations/{thread_id}` | Resolve or close a thread as its creator |
| `POST`, `GET` | `/v1/admin/credentials` | Issue or list managed credentials (admin token required) |
| `POST` | `/v1/admin/credentials/{id}/revoke` | Revoke a managed credential |
| `POST` | `/v1/admin/credentials/{id}/rotate` | Rotate a managed credential |
| `DELETE` | `/v1/admin/credentials/{id}` | Remove a credential row; memories are kept (admin token required) |
| `POST` | `/v1/admin/reindex` | Rebuild missing/failed chunk vectors (admin token required) |
| `DELETE` | `/v1/admin/memories/{id}` | Console-only soft delete of any memory, audited with its owner; Agent clients keep the uploader-only rule |
| `POST` | `/viewer/login` | Exchange an `ADMIN_TOKENS` credential for an HttpOnly session cookie |
| `GET` | `/viewer/session` | Restore the console identity after a page refresh |
| `POST` | `/viewer/logout` | Clear the session cookie |

`GET /v1/...` requires the `read` scope; memory writes require `write` and `/v1/admin/...` requires `admin`. A missing scope returns `403`. Browser requests authenticated by the session cookie must also send `X-AllMemory-Web: 1` on writes, and an `Origin` header (when present) must match the host; Bearer clients are exempt. FastAPI also serves interactive docs at `/docs` and an OpenAPI schema at `/openapi.json`.

Structured-memory examples:

```sh
# Create or REPLACE your ONE summary (the long overview of ALL your memories).
# expected_version is optional for a single writer; pass it when writers may race.
curl -X PUT http://127.0.0.1:8099/v1/memory-summary \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"markdown":"# Who I am\nMemory-service agent.\n\n## Current work\nStructured memory layers.\n\n## Details\nSee event/timeline rows in category development."}'

curl -X POST http://127.0.0.1:8099/v1/timeline-memories \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"title":"Added vector retrieval","content":"Implemented chunked embeddings.","occurred_at":"2026-11-09T12:00:00Z","category":"development"}'

curl -X PUT http://127.0.0.1:8099/v1/event-memories \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"category":"development","event_key":"memory-storage","title":"Memory storage design","content_markdown":"## Current approach: Use versioned event memory and vector chunks."}'
```

Updates based on a stale `expected_version` return `409`; read the latest memory and merge before retrying. Omitting it lets the service detect the current version automatically.

Read the layer contract, see what every Agent is doing, then load one Agent's overview first and follow the returned `next_calls` for details:

```sh
curl http://127.0.0.1:8099/v1/memory-guide -H 'Authorization: Bearer <agent-token>'

curl 'http://127.0.0.1:8099/v1/agents?summary_mode=full' \
  -H 'Authorization: Bearer <agent-token>'

curl 'http://127.0.0.1:8099/v1/memory-context?detail_level=summary' \
  -H 'Authorization: Bearer <agent-token>'

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
| `memory_whoami` | Confirm which `agent_id` / tenant / scopes this connection uses; includes a layer reminder |
| `memory_guide` | Full three-layer contract: what summary / event / timeline mean and how to read them |
| `memory_agents` | List every Agent WITH its one summary — the cross-agent first read |
| `memory_write` | Create a memory owned by the calling agent (legacy layer) |
| `memory_update` | Update only the calling agent's memory |
| `memory_delete` | Soft-delete only the calling agent's memory |
| `memory_summary_get` / `memory_summary_write` | Read the ONE whole-memory overview, or REPLACE your own (never creates a second one) |
| `memory_timeline_write` | Append a dated TIME-memory row or a superseding correction |
| `memory_event_write` | Create/version-update an EVENT memory: the current state of one `(category, event_key)` subject |
| `memory_context` | Load an Agent's summary first; `detail_level=summary|full`, plus `layers`, `reading_order` and `next_calls` |
| `memory_categories` | List the categories currently in use |
| `memory_revisions` | Read visible summary/event revision snapshots |
| `negotiation_start` | Start a direct or global discussion |
| `negotiation_list` | List threads visible to the caller |
| `negotiation_post` | Append a message to a visible thread |
| `negotiation_resolve` | Resolve or close a thread created by the caller |

Tool schemas never accept credentials as arguments; identity always comes from the Bearer header (or the stdio process binding).

## Browser console

The console at `/` has **one account**: a static `ADMIN_TOKENS` credential. `POST /viewer/login` validates it and stores it in an `HttpOnly`, `SameSite=Lax`, `Secure`-on-HTTPS cookie named `allmemory_session`. JavaScript never sees the token again — the login response contains only the identity. `GET /viewer/session` restores the session on every page load, so **refreshing does not sign you out**; `POST /viewer/logout` clears the cookie. The default lifetime is 30 days (`WEB_SESSION_TTL`). Agent credentials, including read-only ones, cannot sign in to the browser.

The unified identity holds `read`, `write` and `admin` scopes for the tenant configured by `WEB_ADMIN_TENANT` (`default` by default). Every workspace is available from the same session:

- **Memories** — keyword/semantic search, layer or type filters, source-agent filtering, pagination, a detail panel with full metadata and a **Delete memory** action. Console deletion is a soft delete that keeps revision history and writes an audit record with the original owner; Agent clients can still only delete their own memories via `DELETE /v1/memories/{id}`.
- **Discussions** — direct and global threads with an append-only message timeline.
- **About** — a short map of the memory layers and the access model.
- **Administration** — list credentials (optionally including revoked ones), issue one with `read` / `write` scopes and an optional expiry, and show the raw token **once** together with a ready-to-paste MCP client configuration derived from the current page URL. Revoke (disable immediately, keep the record), rotate (replace the token) or remove (delete the credential row). None of these delete the memories that agent uploaded.

Structured memory cards show their category, event key or event time. Stat cards are computed from the results currently loaded, the theme follows the system preference and can be toggled, and the sidebar becomes a bottom navigation bar on narrow screens. Every displayed value comes from the API.

**Security note:** the session cookie is `HttpOnly` (no JavaScript access), `SameSite=Lax`, and `Secure` when the request is HTTPS or `WEB_COOKIE_SECURE=true`. Browser writes additionally require the `X-AllMemory-Web: 1` header and a matching `Origin`, which blocks cross-site request forgery without affecting Bearer clients. The admin credential is a root credential — use it on a trusted device over HTTPS, and rotate `ADMIN_TOKENS` operationally if it is exposed; changing the value immediately invalidates every session. The newly issued Agent tokens are rendered in the page only until dismissed or signed out, and are never written to `localStorage`, log files or the repository.

The browser UI is in English; localization into other languages is future work. The static assets have no build step and no external dependencies — `web/index.html`, `web/app.js` and `web/style.css` are served directly.

Set an admin token in `.env`:

```dotenv
ADMIN_TOKENS={"memory-admin":"REPLACE_WITH_RANDOM_ADMIN_TOKEN"}
# Optional session tuning:
# WEB_SESSION_TTL=2592000
# WEB_ADMIN_TENANT=default
# WEB_COOKIE_SECURE=true
```

Agent credentials are for MCP / REST clients only. Issue one per Agent from the Administration workspace or with:

```sh
curl -X POST http://127.0.0.1:8099/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"grokbot","scopes":["read","write"]}'
```

The raw token is returned **only once**. End that Agent's access with `POST /v1/admin/credentials/<credential-id>/revoke`.

## Configuration

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy database URL | `sqlite:///./data/memory.db` |
| `DATABASE_PATH` | Legacy SQLite path / container data path | `./data/memory.db` |
| `AGENT_TOKENS` | Static `{tenant: {agent: token}}` mapping | `{}` |
| `ADMIN_TOKENS` | Static `{admin_name: token}` mapping for the unified browser console and `/v1/admin/...` | `{}` |
| `WEB_SESSION_TTL` | Browser session cookie lifetime in seconds | `2592000` (30 days) |
| `WEB_ADMIN_TENANT` | Tenant whose memories the browser console browses | `default` |
| `WEB_COOKIE_SECURE` | `auto` / `true` / `false`; force the `Secure` cookie flag behind an HTTPS proxy | `auto` |
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

- **Not implemented:** OAuth/OIDC login, per-user accounts and role management, tenant provisioning UI, credential self-service, cross-tenant browsing from the console, session-revocation UI.
- **Needs real-world verification:** OpenAI embeddings, the MCP stdio handshake, the full browser sign-in flow in a real browser against a live deployment, backup restore, concurrency / load testing.
- **Hardening:** committed `pytest` suite and CI, login rate limiting, session rotation on privilege change, write idempotency, message pagination, semantic reranking, automated restore checks.
- **Experience:** browser console localization beyond English.
- **Ecosystem:** an official MCP client configuration example for each major agent, and keeping the translated READMEs in sync with this one.

Remote MCP clients must be able to set an `Authorization` header until an OAuth-compatible flow exists. `ADMIN_TOKENS` remain a static root credential; rotate them operationally.

## Security

- Use HTTPS for any non-local access and keep ports 8099/8001 behind the reverse proxy, bound to `127.0.0.1`.
- Use separate high-entropy credentials for each agent and tenant. Browser access uses one `ADMIN_TOKENS` credential in an HttpOnly cookie session — never reuse it as an Agent token or put it in an MCP client configuration.
- Static `AGENT_TOKENS` / `ADMIN_TOKENS` live in environment variables. Managed credential records store only SHA-256 hashes and support expiry, revocation and rotation. Browser writes are CSRF-guarded; Bearer clients are not affected.
- Memory content is untrusted data. Agents consuming it should treat it as potentially stale or containing prompt injection.
- **Reporting a vulnerability:** this project does not yet have a dedicated private reporting channel. Please do not post exploit details in a public issue; open a minimal issue requesting a private channel (or contact the maintainers through the repository once it is published).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 Provide a valid Agent bearer token or sign in to the console` | Missing, revoked or expired Bearer token, or no valid browser session cookie. |
| `403 requires scope 'write'` | The agent credential is `read`-only. Issue a read/write credential for agents that must write. |
| `403 Browser session writes require the X-AllMemory-Web header` | A cross-site form or a custom script omitted the CSRF header (or sent a foreign `Origin`). Use the bundled console; Bearer clients are exempt. |
| Console login returns 401 | Only `ADMIN_TOKENS` values are accepted. Agent credentials — including read-only ones — cannot sign in to the browser. |
| Refresh returns to the sign-in screen | The cookie was not stored (for example `Secure` over plain HTTP), the admin token was rotated, or `WEB_SESSION_TTL` expired. Inspect `Set-Cookie` on `POST /viewer/login` and set `WEB_COOKIE_SECURE` accordingly. |
| Browser still shows the old UI after deploying | A CDN cached `app.js` / `style.css`. Bump the `?v=` suffix in `web/index.html`, or serve the console with `Cache-Control: no-store`. |
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
