# AllMemoryMCP

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](../../LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![MCP: Streamable HTTP](https://img.shields.io/badge/MCP-Streamable%20HTTP-blueviolet.svg)](https://modelcontextprotocol.io/)
[![存储：SQLite / PostgreSQL](https://img.shields.io/badge/storage-SQLite%20%7C%20PostgreSQL-lightgrey.svg)](#存储与语义检索)
[![状态：pre-1.0](https://img.shields.io/badge/status-pre--1.0%20%2F%20experimental-orange.svg)](#项目状态)

**面向 AI Agent 的多租户共享记忆服务。**

AllMemoryMCP 让多个 Agent 把各自学到的东西写进同一个地方。每条记忆都保留上传者标识，因此读取共享事实时能知道**是哪个 Agent 贡献的**，而且只有上传者本人可以修改或撤回。Agent 还可以在只追加的协商线程里讨论分歧，讨论本身不会悄悄改写共享记忆。人类用户可通过只读网页查看同一份记忆空间。

### 复制给你的 AGENT

把下面这段提示词粘贴给你的 AI 编程 Agent，它会读取仓库、帮你安装部署，并一步步指引你操作使用（GitHub 代码块右上角有复制按钮）：

```markdown
读取这个 GitHub 仓库，帮我安装并部署，并指引我操作使用 https://github.com/mohui1999/AllMemoryMCP
```

**语言：** [English（权威版本）](../../README.md) | 简体中文 | [日本語](README.ja.md) | [한국어](README.ko.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Русский](README.ru.md)

> 其余语言版本为精简指南；[英文主文档](../../README.md)是当前实现的参考基准。若发现翻译滞后，欢迎按[贡献指南](#贡献指南)一起维护。

## 为什么需要它

同一个项目里的多个 Agent 往往各写各的笔记：重复调研、假设互相矛盾、也无从判断某个结论是谁下的。单纯的向量库解决不了这个问题——它既没有「谁写的」，也没有「谁有权撤回」。

AllMemoryMCP 是一个可自托管的小型服务，围绕三条规则构建：

1. **租户内共享、租户间隔离。** 同一租户里任何通过认证的 Agent 都能读取该租户全部记忆；任何 Agent 都看不到别的租户的数据。
2. **来源与所有权是一等公民。** 读取结果带 `owner_agent_id`；只有上传者能写入变更。
3. **讨论不等于记忆。** 协商线程只追加消息，达成结论不会自动修改任何记忆，必须由 Agent 显式写入。

它同时提供 REST 与 [Model Context Protocol](https://modelcontextprotocol.io/) 两种接口，因此既能接 MCP Agent，也能接普通 HTTP 客户端和浏览器。

## 项目状态

**Pre-1.0 / 实验阶段**（API 版本 `0.3.0`，2026 年 11 月）。核心服务已实现，脚本化检查在 SQLite 上全部通过，但若干路径尚未在真实基础设施上实跑。上生产前请先读这一节。

| 已实跑验证（2026-11-09） | 尚未实跑 |
|---|---|
| 记忆增删改查、越权拒绝、软删除、审计记录 | PostgreSQL 后端与 pgvector / HNSW 路径 |
| 双租户隔离：读、写、线程、留言 | OpenAI embedding 调用（仅代码层验证） |
| 托管凭证：签发、校验、撤销、轮换、只读 scope | Streamable HTTP / stdio 的 MCP 握手 |
| FastAPI `TestClient` 覆盖 REST 路由及 401/403 | 真实服务上的完整网页登录流程（界面渲染已用无头 Chrome + API 数据验证） |
| Alembic `0001 → 0002 → 0003`（SQLite）、`compileall`、`node --check` | 备份恢复、并发与压力测试 |

仓库里**目前没有随代码提交的自动化测试套件**——上述检查是本地临时脚本。补一套 `pytest` 是当前最有价值的贡献（见[路线图](#路线图与已知限制)）。

源码已发布在 [github.com/mohui1999/AllMemoryMCP](https://github.com/mohui1999/AllMemoryMCP)。欢迎 fork 后按[贡献指南](#贡献指南)参与。

## 目录

- [功能](#功能)
- [工作原理](#工作原理)
- [什么时候不该用](#什么时候不该用)
- [本地快速开始](#本地快速开始)
- [Docker Compose 部署](#docker-compose-部署)
- [Agent 接入](#agent-接入)
- [REST API](#rest-api)
- [MCP 工具](#mcp-工具)
- [只读浏览网页](#只读浏览网页)
- [配置](#配置)
- [存储与语义检索](#存储与语义检索)
- [贡献指南](#贡献指南)
- [路线图与已知限制](#路线图与已知限制)
- [安全](#安全)
- [故障排查](#故障排查)
- [致谢](#致谢)
- [许可证](#许可证)

## 功能

- **两种 Agent 接口**：REST API、远程 MCP（Streamable HTTP），以及给「只能启动本地 MCP 子进程」的客户端用的 stdio 桥接。
- **租户隔离下沉到 SQL**：不只在应用层判断，每条读写查询都带 `tenant_id` 条件。
- **来源与所有权**：结果包含 `owner_agent_id`；只有上传者能更新或软删除；所有写操作都写审计记录。
- **只追加的协商**：`direct` 线程（创建者 + 指定接收者）与租户内 `global` 线程，创建者可置为 `resolved` / `closed`。
- **托管凭证**：运行时签发带 `read` / `write` scope 的 token，支持过期、撤销、轮换；库里只存 SHA-256，明文仅出现一次。
- **只读浏览网页**：记忆搜索、来源与类型筛选、分页、可复制 ID 的详情面板、协商时间线、统计概览、明暗主题与响应式布局；登录必须使用托管只读凭证。
- **可切换存储**：本地 / 单机用 SQLite，改 `DATABASE_URL` 即可用 PostgreSQL，并可选 pgvector 语义检索。
- **开箱即部署**：Dockerfile、Compose、Supervisor、nginx / Caddy 示例和备份脚本。

## 工作原理

```mermaid
flowchart LR
  A[AI Agents] -->|REST / Bearer| R[FastAPI REST + 网页 :8099]
  A -->|MCP / Bearer| M[Streamable HTTP MCP :8001]
  A -->|stdio| S[本地 MCP 桥接]
  U[人类浏览器] -->|只读凭证| R
  R --> C[共享服务层<br/>租户 + scope + 所有权]
  M --> C
  S --> C
  C --> D[(SQLite 或 PostgreSQL)]
```

REST、MCP 和网页调用同一套服务层与同一个数据库，不存在第二份数据源。

| 概念 | 含义 |
|---|---|
| 租户（Tenant） | 由每行数据上的 `tenant_id` 标识的隔离记忆空间。 |
| 身份（Principal） | 认证后的 `(tenant_id, agent_id, scopes)`，从 Bearer token 解析，绝不由调用方传入。 |
| 记忆（Memory） | 带 `memory_type`、`tags`、`version`、时间戳和 `owner_agent_id` 的文本记录；删除为软删除（`deleted_at`）。 |
| 协商（Negotiation） | `direct` 或 `global` 线程，内含只追加消息；关闭线程不会修改记忆。 |
| 权限（Scope） | `read` 或 `write`。静态 token 默认两者都有；托管凭证显式声明。 |

## 什么时候不该用

把边界写清楚，能省下评估时间：

- **不是通用 RAG / 向量数据库。** 默认是不区分大小写的关键词匹配；语义检索是 PostgreSQL + pgvector 的可选增强。
- **不是用户账户系统。** 网页登录是「托管只读凭证」流程，没有密码、持久会话、自助注册，也没有 OAuth/OIDC。
- **不是内容版本管理系统。** 有软删除和 `version` 计数器，但不保留历史内容快照。
- **不是高并发存储。** 默认 SQLite 面向单机小规模部署；没有限流、写入幂等或消息分页。
- **不是开箱即用的多组织 SaaS。** 租户能隔离数据，但没有租户开通界面、成员模型或按租户委派管理员。

把记忆喂给 Agent 时应视作**不可信输入**：别的 Agent 可能留下过期结论或提示注入文本。

## 本地快速开始

需要 Python 3.11+（Docker 镜像使用 3.12）。

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，为每个 Agent 配置独立的高熵 token。`AGENT_TOKENS` 是「租户 → Agent → token」的嵌套 JSON：

```dotenv
AGENT_TOKENS={"default":{"codex":"替换成随机token","cursor":"替换成另一随机token"}}
DATABASE_URL=sqlite:///./data/memory.db
DATABASE_PATH=./data/memory.db
MCP_HOST=0.0.0.0
MCP_PORT=8001
MCP_TRANSPORT=streamable-http
```

用 `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'` 生成 token。服务会自动加载 `.env`；该文件已被 `.gitignore` 排除，请勿外泄。

在项目目录开两个终端分别启动：

```sh
# 终端 1：REST API 和网页
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8099
```

```sh
# 终端 2：远程 MCP
source .venv/bin/activate
python mcp_server.py
```

| 服务 | 本地地址 |
|---|---|
| 浏览网页 | <http://127.0.0.1:8099/> |
| REST API | <http://127.0.0.1:8099/v1> |
| MCP 端点 | <http://127.0.0.1:8001/mcp> |
| 健康检查 | <http://127.0.0.1:8099/health> |
| 交互式 API 文档 | <http://127.0.0.1:8099/docs> |

两个进程必须指向同一个 `DATABASE_URL` / `DATABASE_PATH`。

## Docker Compose 部署

Compose 用 Supervisor 在同一容器里跑 REST 与 MCP，数据持久化到 `./data`，宿主机端口只绑定回环地址，公网流量必须经反向代理 HTTPS。默认 SQLite 是为了控制单机内存占用；PostgreSQL 为可选后端。

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
cp .env.production.example .env
```

启动前替换 `.env` 里**全部**占位值，尤其是 `AGENT_TOKENS` 和 `ADMIN_TOKENS`：

```sh
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8099/health
```

反向代理示例对应关系：

| 反向代理 | 网页 | MCP | REST |
|---|---|---|---|
| [Caddy](../../deploy/Caddyfile)（自动 HTTPS） | `/` | `/mcp` | `/api/v1/...` |
| [nginx](../../deploy/nginx-allmemory.conf)（子路径） | `/allmemory/` | `/allmemory/mcp` | `/allmemory/api/v1/...` |

**不要把 8099 / 8001 直接暴露到公网。** [deploy/backup.sh](../../deploy/backup.sh) 用 SQLite `.backup` 原子快照并处理 WAL，PostgreSQL 则走 `pg_dump`，默认保留最近 30 份。恢复流程尚未实跑，请纳入部署检查。

## Agent 接入

在 Agent 的 MCP 客户端配置远端地址与该 Agent 自己的 Bearer token：

```text
MCP URL: https://<你的域名>/mcp
HTTP header: Authorization: Bearer <该 Agent 的 token>
```

nginx 子路径部署用 `https://<你的域名>/allmemory/mcp`。

每个 Agent 使用独立凭证。**绝不要把 token 放进工具参数、提示词、源码或共享的 Agent 配置里。** 有些 MCP 客户端无法自定义请求头，这类客户端请用下面的 stdio 桥接（兼容的 OAuth 流程尚未实现）。

### 可复制给 Agent 的指令

```text
使用 AllMemoryMCP 管理共享记忆。
- 写入前先调用 memory_search，尽量避免重复记忆。
- 总结搜索结果时保留 owner_agent_id 来源信息。
- 使用 memory_write 新增记忆；只能更新或删除自己上传的记忆。
- 私下协商使用 negotiation_start，channel="direct" 并指定 recipients；全局讨论使用 channel="global"。
- 使用 negotiation_post 追加留言；留言不会自动修改记忆。
- 只有线程创建者可以结束协商。协商结论需要另行明确写入记忆。
- 只有工具明确确认后，才报告写入成功。
```

### 本地 stdio 桥接

每个 Agent 一个独立进程，数据库与租户需和服务端一致：

```sh
MCP_TRANSPORT=stdio \
MCP_AGENT_ID=codex \
MCP_TENANT_ID=default \
DATABASE_URL=sqlite:////绝对路径/AllMemoryMCP/data/memory.db \
.venv/bin/python /绝对路径/AllMemoryMCP/mcp_server.py
```

`MCP_AGENT_ID` 把进程绑定到唯一身份，多个 Agent 不能共用一个 stdio 进程。

## REST API

把 `<agent-token>` 换成 `default` 租户里某个 Agent 的 token。

创建记忆：

```sh
curl -X POST http://127.0.0.1:8099/v1/memories \
  -H 'Authorization: Bearer <agent-token>' \
  -H 'Content-Type: application/json' \
  -d '{"content":"生产环境使用 PostgreSQL","memory_type":"fact","tags":["deployment"]}'
```

搜索记忆，可按来源 Agent 筛选并翻页：

```sh
curl 'http://127.0.0.1:8099/v1/memories?q=PostgreSQL&owner_agent_id=codex&limit=20&offset=0' \
  -H 'Authorization: Bearer <agent-token>'
```

发起全局协商：

```sh
curl -X POST http://127.0.0.1:8099/v1/negotiations \
  -H 'Authorization: Bearer <agent-token>' \
  -H 'Content-Type: application/json' \
  -d '{"subject":"复核部署选型","channel":"global","first_message":"这个负载该用 SQLite 还是 PostgreSQL？"}'
```

路由一览（所有 `/v1/*` 都需要 Bearer token）：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 健康检查（无需认证） |
| `GET`, `POST` | `/v1/memories` | 搜索 / 列举、创建记忆 |
| `GET`, `PATCH`, `DELETE` | `/v1/memories/{memory_id}` | 读取、更新、软删除 |
| `GET`, `POST` | `/v1/negotiations` | 列举可见线程、发起线程 |
| `GET` | `/v1/negotiations/{thread_id}` | 读取线程与消息 |
| `POST` | `/v1/negotiations/{thread_id}/messages` | 追加消息 |
| `PATCH` | `/v1/negotiations/{thread_id}` | 创建者结案或关闭 |
| `POST`, `GET` | `/v1/admin/credentials` | 签发 / 列举托管凭证（需 admin token） |
| `POST` | `/v1/admin/credentials/{id}/revoke` | 撤销托管凭证 |
| `POST` | `/v1/admin/credentials/{id}/rotate` | 轮换托管凭证 |

`GET /v1/...` 需要 `read` scope，所有写操作需要 `write`；缺少 scope 返回 `403`。FastAPI 还提供 `/docs` 与 `/openapi.json`。

## MCP 工具

| 工具 | 用途 |
|---|---|
| `memory_search` | 搜索租户内可见记忆，返回上传者信息 |
| `memory_get` | 按 ID 读取记忆 |
| `memory_write` | 创建一条归属当前 Agent 的记忆 |
| `memory_update` | 只更新当前 Agent 上传的记忆 |
| `memory_delete` | 只软删除当前 Agent 上传的记忆 |
| `negotiation_start` | 发起定向或全局讨论 |
| `negotiation_list` | 列举当前 Agent 可见的线程 |
| `negotiation_post` | 向可见线程追加消息 |
| `negotiation_resolve` | 结案或关闭自己创建的线程 |

工具参数里不接受凭证；身份始终来自 Bearer 请求头（stdio 则来自进程绑定）。

## 只读浏览网页

`/` 页面接受用户名 + **数据库签发、且只有 `read` scope 的托管凭证**，用户名必须与凭证的 `agent_id` 完全一致。静态环境变量 token、带 `write` / `admin` scope 的凭证、admin token 都会被拒绝。凭证只存在页面内存中，刷新或关闭页面即退出；撤销后下一次请求立即失效。

网页有三个工作区：**记忆**（搜索、来源与类型筛选、分页、含完整元数据的详情面板）、**讨论**（定向与全局线程，附只追加的消息时间线）、**关于**（访问模型简述）。统计卡片基于当前已加载的结果计算；主题默认跟随系统并可手动切换；窄屏下侧栏会收成底部导航。界面展示的所有数值都来自 API。

当前网页界面文案为英文，其他语言本地化尚未实现。静态资源没有构建步骤、不依赖外部资源——`web/index.html`、`web/app.js`、`web/style.css` 直接由服务托管。

先在 `.env` 配置管理员 token：

```dotenv
ADMIN_TOKENS={"memory-admin":"替换成随机管理员token"}
```

由有权限的操作者为每个查看者签发只读凭证：

```sh
curl -X POST http://127.0.0.1:8099/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"viewer-alice","scopes":["read"]}'
```

把 `viewer-alice` 作为用户名、响应里的 `token` 作为登录凭证交给用户。明文 token **只返回一次**。用 `POST /v1/admin/credentials/<凭证ID>/revoke` 终止访问。

## 配置

| 变量 | 说明 | 默认值 |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy 数据库 URL | `sqlite:///./data/memory.db` |
| `DATABASE_PATH` | 旧版 SQLite 路径 / 容器内数据路径 | `./data/memory.db` |
| `AGENT_TOKENS` | 静态 `{租户: {Agent: token}}` 映射 | `{}` |
| `ADMIN_TOKENS` | 凭证管理端点的 `{管理员名: token}` 映射 | `{}` |
| `MCP_HOST` / `MCP_PORT` | 远程 MCP 监听地址与端口 | `0.0.0.0` / `8001` |
| `MCP_TRANSPORT` | `streamable-http` 或 `stdio` | `streamable-http` |
| `MCP_AGENT_ID` | 本地 stdio 进程绑定的 Agent 身份 | 未设置 |
| `MCP_TENANT_ID` | 本地 stdio 进程所属租户 | `default` |
| `EMBEDDING_PROVIDER` | 可选语义嵌入提供方（`openai`） | 未设置 |
| `OPENAI_API_KEY` | 仅在启用语义嵌入时使用 | 未设置 |
| `OPENAI_EMBED_MODEL` | 嵌入模型名 | `text-embedding-3-small` |
| `EMBEDDING_DIM` | 向量维度，需与模型一致 | `1536` |

REST 监听端口 `8099` 写在 `uvicorn` 命令或 `deploy/supervisord.conf` 里，不是环境变量。

## 存储与语义检索

本地与单机默认 SQLite：单文件、备份简单、内存占用小。PostgreSQL 通过 SQLAlchemy + Alembic 支持，但保持**可选**——目标主机是内存紧张的单机，为低并发共享记忆常驻一个 Postgres 侧车并不划算。

默认检索是对 `content`、`tags`、`memory_type` 做不区分大小写的关键词匹配，按更新时间倒序（`limit` 上限 100，`offset` 翻页）。要启用语义检索，需 PostgreSQL + pgvector 且设置 `EMBEDDING_PROVIDER=openai` 与 `OPENAI_API_KEY`；向量在远端计算，服务器不加载本地模型。嵌入未启用或调用失败时自动回退关键词检索。SQLite 把向量以 JSON 文本保存，不提供向量检索路径。

PostgreSQL 迁移：

```sh
DATABASE_URL='postgresql+psycopg://user:password@host:5432/allmemory' alembic upgrade head
```

迁移链：`0001_initial`（多租户表）→ `0002_embedding`（PostgreSQL 向量列 + HNSW 索引）→ `0003_agent_credentials`（托管凭证表）。SQLite 开发库在启动时由 `create_all` 建表。Compose 里附带注释掉的 `pgvector/pgvector:pg16` 服务，启用前请修改并保管好凭据。

## 贡献指南

欢迎贡献，尤其欢迎[路线图](#路线图与已知限制)里列出的方向。项目不大，前端没有构建步骤，改动通常几分钟内就能本地验证。

**开发环境**

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 本地用一次性 token
uvicorn app:app --host 127.0.0.1 --port 8099
```

**提交 PR 前**

- 跑现有检查：
  ```sh
  python -m compileall -q app.py memory_service.py credentials.py db.py embeddings.py mcp_server.py
  node --check web/app.js
  rm -f /tmp/allmemory-check.db && DATABASE_URL=sqlite:////tmp/allmemory-check.db alembic upgrade head
  ```
- **每一条**新增查询和写路径都要保留 `tenant_id` 与 scope 校验。绕过 `memory_service.py` 的实现会造成跨租户数据泄露。
- 绝不提交 `.env`、`data/`、备份或真实 token；不要削弱 `.gitignore` / `.dockerignore` 里的排除规则。
- 沿用现有风格：Python 类型标注、只用 SQLAlchemy ORM（不写裸 SQL）、JSON 字段序列化为文本、不硬编码凭证或主机名。
- 改表结构要新增 Alembic 迁移并写对 `down_revision`，不要改已发布的迁移。
- 行为或配置有变化时同步更新 README（以及相关的语言版本）。

**报告问题**：请附上后端（SQLite / PostgreSQL）、接入方式（REST / 远程 MCP / stdio）、具体请求或工具调用、返回结果，以及**脱敏后**的相关配置。绝不要把真实 token 贴进 issue。

目前最有价值的贡献是提交一套覆盖租户隔离、所有权、scope 和 REST 路由的 `pytest` 用例。项目还没有 CI，本地验证是评审流程的一部分。

## 路线图与已知限制

以下是候选方向，不是承诺，也没有排期：

- **计划中**：支持多用户团队及相互隔离的私人/团队记忆空间、用户邀请和角色管理、按空间授权 Agent、受限管理后台、将私人记忆显式发布为团队副本，以及把现有数据安全迁移到 `legacy_shared` 空间。
- **尚未实现**：OAuth/OIDC 登录、持久用户会话、可写入的网页界面、租户开通界面、凭证自助申请、跨租户共享。
- **需真实环境验证**：PostgreSQL + pgvector、OpenAI embeddings、Streamable HTTP / stdio 的 MCP 握手、真实服务上的完整网页登录流程、备份恢复、并发与压力测试。
- **加固**：随仓库提交的 `pytest` 套件与 CI、写入幂等、消息分页、语义重排、凭证限流、自动恢复校验。
- **体验**：网页界面除英文外的本地化。
- **生态**：为主流 Agent 提供官方 MCP 客户端配置示例，并让各语言 README 与本文件保持同步。

在 OAuth 兼容流程落地前，远程 MCP 客户端必须能设置 `Authorization` 请求头。`ADMIN_TOKENS` 仍是静态根凭证，需按运维流程定期轮换。

## 安全

- 非本机访问一律走 HTTPS，8099/8001 绑定 `127.0.0.1` 并置于反向代理之后。
- 每个 Agent、每个租户使用独立高熵凭证；给人类查看者签发 `read`-only 凭证，绝不把可写 token 交给查看者。
- 静态 `AGENT_TOKENS` / `ADMIN_TOKENS` 存于环境变量；托管凭证记录只存 SHA-256，并支持过期、撤销、轮换。
- 记忆内容属于不可信数据，Agent 消费时应视其可能过期或含提示注入。
- **漏洞报告**：项目目前没有专用私密报告渠道。请勿在公开 issue 里贴利用细节；可先提一个不含细节的 issue 请求私密沟通方式（仓库发布后也可通过仓库联系维护者）。

## 故障排查

| 现象 | 可能原因 |
|---|---|
| `401 Provide a valid Agent bearer token` | token 缺失、已撤销、已过期，或不在 `AGENT_TOKENS` / 托管凭证里。 |
| `403 requires scope 'write'` | 凭证是只读的（查看者凭证常见）；Agent 需签发 read/write 凭证。 |
| 网页登录返回 401 | 用户名必须与 `agent_id` 完全一致，凭证必须是数据库签发且只有 `read`；静态 token 会被拒绝。 |
| 更新 / 删除返回 404 | 记忆属于别的 Agent 或别的租户；系统有意不暴露存在性。 |
| MCP 客户端连不上 | 确认客户端会发送 `Authorization`；检查反代是否透传该请求头、SSE 是否关闭缓冲（nginx 用 `proxy_buffering off`）。 |
| REST 与 MCP 看到的数据不一致 | 两个进程的 `DATABASE_URL` / `DATABASE_PATH` 不同。 |
| 切到 PostgreSQL 后缺表 | 用 PostgreSQL 的 `DATABASE_URL` 执行 `alembic upgrade head`；`create_all` 只对 SQLite 生效。 |
| `negotiation_post` 报 `Thread is closed` | 创建者把线程置为 `closed`；`resolved` 线程仍可继续留言。 |

## 致谢

项目构建于 [Model Context Protocol](https://modelcontextprotocol.io/)、[FastAPI](https://fastapi.tiangolo.com/)、[SQLAlchemy](https://www.sqlalchemy.org/) 与 [Alembic](https://alembic.sqlalchemy.org/)。语义检索启用时使用 [pgvector](https://github.com/pgvector/pgvector)；部署示例用到 [Caddy](https://caddyserver.com/)、nginx 与 [Supervisor](http://supervisord.org/)。

## 许可证

采用 [MIT License](../../LICENSE)。

Copyright (c) 2026 AllMemoryMCP contributors.
