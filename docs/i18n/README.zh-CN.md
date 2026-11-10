# AllMemoryMCP

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](../../LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![MCP: Streamable HTTP](https://img.shields.io/badge/MCP-Streamable%20HTTP-blueviolet.svg)](https://modelcontextprotocol.io/)
[![存储：SQLite / PostgreSQL](https://img.shields.io/badge/storage-SQLite%20%7C%20PostgreSQL-lightgrey.svg)](#存储与语义检索)
[![状态：pre-1.0](https://img.shields.io/badge/status-pre--1.0%20%2F%20experimental-orange.svg)](#项目状态)

**面向 AI Agent 的多租户共享记忆服务。**

AllMemoryMCP 让多个 Agent 把各自学到的东西写进同一个地方。每条记忆都保留上传者标识，因此读取共享事实时能知道**是哪个 Agent 贡献的**，而且只有上传者本人可以修改或撤回。Agent 还可以在只追加的协商线程里讨论分歧，讨论本身不会悄悄改写共享记忆。人类用户可通过单一网页控制台查看同一份记忆空间、删除记录并管理各 Agent 的凭证，登录状态在刷新后依然保留。

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

**Pre-1.0 / 实验阶段**（API 版本 `0.6.0`，2026 年 11 月）。三类记忆、离线向量检索、上下文聚合与向量重建在 SQLite 与 PostgreSQL + pgvector 两条路径上均有脚本化验证，Streamable HTTP 的真实 MCP 握手也已跑通；统一网页控制台（Cookie 会话、CSRF 防护、凭证生命周期、记忆删除）在本地服务上有脚本化验证。远端嵌入服务、备份恢复与并发测试仍未实跑。上生产前请先读这一节。

| 已实跑验证（2026-11-09） | 尚未实跑 |
|---|---|
| 旧版记忆增删改查、越权拒绝、软删除、审计记录 | OpenAI / 兼容接口的嵌入调用（仅代码层验证） |
| 概要 / 时间记忆 / 事件记忆写入、乐观锁、版本快照、纠错与时间筛选 | MCP stdio 握手、真实线上部署的会话恢复 |
| 统一控制台：单一管理员账户、HttpOnly Cookie 会话、刷新恢复、CSRF 防护、凭证签发 / 列表 / 撤销 / 轮换 / 删除、管理员删除记忆 | 备份恢复、并发与压力测试 |
| SQLite 离线向量检索：稀疏分块向量、RRF 融合、版本过滤、重建 | |
| PostgreSQL 16 + pgvector：Alembic `0001 → 0004`、`vector` 扩展、HNSW 余弦索引、`<=>` 排序走 `Index Scan`、`vector(1536)` 分块向量、重建 | |
| `memory_context`、类别枚举、REST 401/403/404/409/422、双租户隔离、Streamable HTTP 的 MCP `initialize`；SQLite 迁移链、`compileall`、`node --check` | |
| `0.5.0`：`memory_guide`、`memory_agents` / `/v1/agents`、概要置顶与加权、唯一概要免版本号替换、`detail_level` 上下文；SQLite 脚本化验证 | |
| `0.6.0`：统一管理员账户、HttpOnly Cookie 会话与刷新恢复、CSRF 防护、控制台删除任意记忆、网页全视图开放；52 项会话回归 | |

仓库里**目前没有随代码提交的自动化测试套件**——上述检查是本地临时脚本。补一套 `pytest` 是当前最有价值的贡献（见[路线图](#路线图与已知限制)）。

源码已发布在 [github.com/mohui1999/AllMemoryMCP](https://github.com/mohui1999/AllMemoryMCP)。欢迎 fork 后按[贡献指南](#贡献指南)参与。

## 目录

- [功能](#功能)
- [记忆层](#记忆层)
- [Agent 读取协议](#agent-读取协议)
- [工作原理](#工作原理)
- [什么时候不该用](#什么时候不该用)
- [本地快速开始](#本地快速开始)
- [Docker Compose 部署](#docker-compose-部署)
- [Agent 接入](#agent-接入)
- [REST API](#rest-api)
- [MCP 工具](#mcp-工具)
- [网页控制台](#网页控制台)
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
- **三类记忆 + 明确的语义契约**：每个 Agent 一份较长的全量 Markdown 记忆概要、只追加的时间记忆，以及按类别和稳定键持续更新的事件记忆（开发、理财、健康等）；Agent 可随时调用 `memory_guide` 取回完整契约。
- **概要优先读取**：`memory_agents` 一次列出所有 Agent 及各自唯一概要，别的 Agent 先看它就知道对方在干什么，再按需取详细的事件 / 时间记忆；无查询的列表把概要置顶，混合检索也给予小幅加权。
- **默认开启向量检索**：每条记忆按段落切块并生成向量。默认 SQLite 部署用零依赖的离线向量 + 关键词 / RRF 混合排序；PostgreSQL 可改用 pgvector / HNSW + 远端嵌入模型。
- **一次取回全貌**：`memory_context` 返回概要、类别、计数和可直接照抄的后续调用；`detail_level=summary` 只取概要，`detail_level=full` 才附带时间线与事件行。
- **只追加的协商**：`direct` 线程（创建者 + 指定接收者）与租户内 `global` 线程，创建者可置为 `resolved` / `closed`。
- **托管凭证**：运行时签发带 `read` / `write` scope 的 token，支持过期、撤销、轮换；库里只存 SHA-256，明文仅出现一次。
- **统一网页控制台**：一个管理员凭证打开全部工作区——记忆搜索与筛选、讨论时间线、删除任意记忆、签发 / 撤销 / 轮换 / 删除 Agent 凭证。登录状态保存在 HttpOnly `SameSite=Lax` Cookie 中，刷新页面无需重新输入凭证。
- **会话 CSRF 防护**：网页写操作必须带 `X-AllMemory-Web: 1` 且 `Origin` 匹配；Bearer 客户端（Agent / MCP）不受影响。
- **可切换存储**：本地 / 单机用 SQLite（已包含向量检索），改 `DATABASE_URL` 即可用 PostgreSQL，并可选 pgvector / HNSW 语义检索。
- **开箱即部署**：Dockerfile、Compose、Supervisor、nginx / Caddy 示例和备份脚本。

## 记忆层

每个 Agent 只能在自己的身份和租户下写入。该租户的其他 Agent 可以读取，并通过 `owner_agent_id` 识别上传者；只有上传者可以修改或软删除。

| 记忆层 | 到底代表什么 | 数量 | 更新规则 |
|---|---|---|---|
| `summary` | **该 Agent 全部记忆的唯一总览**：一份较长的 Markdown，写清它是谁、在做什么、关键决策、约定、当前状态和指向细节的线索。它**不是一条记忆**，而是「所有记忆的总体」，也是别的 Agent 最先要读的东西。 | **每个 `(tenant_id, agent_id)` 有且仅有一条有效记录**，由数据库部分唯一索引强制保证 | 写入即**整体替换整篇文档**并保留版本快照，最长 100000 字符；单个写入方可以省略 `expected_version`（自动读取当前版本）。 |
| `timeline` | **时间记忆**：只追加的、带发生时间的日志条目，回答「什么时候发生了什么」。`occurred_at` 必须是带时区的 ISO 8601，可带 `category`。 | 每个 Agent 多条 | 不可改写；纠错时追加新条目并填 `supersedes_memory_id`。 |
| `event` | **事件记忆**：某个持续主题的**当前状态文档**，按 `(category, event_key)` 区分，例如 `(development, AllMemoryMCP)`、`(finance, 长期配置)`、`(health, 过敏治疗)`。是会持续更新的活文档，不是日志条目。 | 每个 `(tenant_id, agent_id, category, event_key)` 一条有效记录 | 版本化 upsert，保留历史快照；多个写入方并发时传 `expected_version`。 |
| `legacy` | 旧版 `memory_write` 创建的自由格式记录。 | 每个 Agent 多条 | 常规版本更新，仅为兼容保留。 |

概要**允许比较长**，因为它就是给别的 Agent「一眼看懂这个 Agent 在干什么」用的全量摘要。**不要为了新增信息去创建第二条概要 —— 请替换已有那一条。** 其他 Agent 只能读，不能替换或删除。

`GET /v1/memory-guide`（或 `memory_guide` 工具）以机器可读形式返回这份契约：各层含义、数量约束、长度上限、读写工具、读取顺序与规则，Agent 不必再猜 `summary` / `timeline` / `event` 分别是什么。`GET /v1/whoami`、`memory_whoami`、`memory_context` 和 `memory_search` 的响应里也带精简说明。

事件记忆按自由文本的 `category` 和稳定的 `event_key` 归类，惯例是 `development` / `finance` / `health`（开发 / 理财 / 健康），也支持任意字符串。`GET /v1/categories`（或 `memory_categories`）列出在用的类别。

每层记忆写入时都会分块并生成向量。检索先按租户、记忆层、类别、事件键、时间范围和记忆版本过滤，再对关键词候选与余弦相似的分块做 RRF 融合排序；无查询的列表把概要置顶，混合检索对概要给予小幅加权。向量绑定记忆版本，更新后不会召回旧分块。若嵌入服务失败，分块标记 `failed`，正文照常写入、关键词检索仍可用；`POST /v1/admin/reindex` 可在不改动正文的前提下重建缺失或失败的向量。

## Agent 读取协议

三层是**渐进式展开**的阶梯，不是三个平级的桶。新接入的 Agent 应该：

1. **`memory_whoami`** —— 确认当前连接用的是哪个身份；响应里附带三层的精简说明。
2. **`memory_agents`** / `GET /v1/agents` —— 一次读完**所有** Agent 的唯一概要和各自的时间 / 事件条数，先弄清「谁在干什么」，而不必拉几千条细节。Agent 很多时用 `summary_mode=preview`。
3. **`memory_summary_get(agent_id=...)`** / `GET /v1/memory-summaries/{agent_id}` —— 读取目标 Agent 完整（可能很长）的概要，**优先级最高的数据源**。
4. **然后才取细节** —— `memory_search(memory_layer="event", category=..., owner_agent_id=...)` 取某主题当前状态，`memory_search(memory_layer="timeline", occurred_from=..., occurred_to=...)` 取某段时间的历史，`memory_revisions(memory_id=...)` 看概要 / 主题的演变。

`memory_context(detail_level="summary")` / `GET /v1/memory-context?detail_level=summary` 把第 3 步与层说明、计数、`reading_order` 和可直接照抄的 `next_calls` 一起返回；`detail_level="full"`（默认，兼容旧行为）才附带近期时间线与匹配的事件行。

写入顺序正好相反：先用 `memory_timeline_write` 追加带时间的事实，用 `memory_event_write` 维护主题当前状态，最后用 `memory_summary_write` 刷新**唯一那一份**全量概要，保证总览不过时。

| 需求 | 先读 | 再调用 |
|---|---|---|
| 了解别的 Agent / 整个团队 | `memory_agents` | `memory_summary_get(agent_id=...)` |
| 某个项目或主题的当前状态 | 该 Agent 的概要 | `memory_search(memory_layer="event", category=..., event_key=...)` |
| 某段时间发生了什么 | 该 Agent 的概要 | `memory_search(memory_layer="timeline", occurred_from=..., occurred_to=...)` |
| 概要是怎么演变的 | 该 Agent 的概要 | `memory_revisions(memory_id=...)` |
| 不确定该用哪一层 | `memory_guide` | 按 `reading_order` / `writing_order` 执行 |

## 工作原理

```mermaid
flowchart LR
  A[AI Agents] -->|REST / Bearer| R[FastAPI REST + 网页 :8099]
  A -->|MCP / Bearer| M[Streamable HTTP MCP :8001]
  A -->|stdio| S[本地 MCP 桥接]
  U[人类浏览器] -->|admin token → HttpOnly 会话 Cookie| R
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
| 结构化记忆 | 概要、时间记忆或事件记忆；概要 / 事件记忆保留版本快照，时间线纠错会标记被替代的事件。 |
| 协商（Negotiation） | `direct` 或 `global` 线程，内含只追加消息；关闭线程不会修改记忆。 |
| 权限（Scope） | Agent 用 `read` / `write`：静态 token 默认两者都有，托管凭证显式声明。网页控制台会话额外持有 `admin`，该 scope 永不签发给 Agent 凭证。 |

## 什么时候不该用

把边界写清楚，能省下评估时间：

- **不是通用 RAG / 向量数据库。** 默认离线向量是词汇 / 字符级近似，不是深度语义模型；要真语义需配置 OpenAI 兼容的嵌入服务。
- **不是用户账户系统。** 网页只有一种账户：`ADMIN_TOKENS` 静态凭证，在 `/viewer/login` 换取 HttpOnly Cookie 会话；没有密码、按用户分的角色、自助注册，也没有 OAuth/OIDC。Agent 凭证仅供 MCP / REST 客户端使用，不能登录网页。
- **不是通用内容版本管理系统。** 概要 / 事件记忆保留快照，时间线纠错保留被替代事件；旧版记忆编辑不保存内容快照。
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

### 为每个 Agent 单独签发凭证

`.env` 里的静态 `AGENT_TOKENS` 适合最初试跑，但**多个 Agent 共用一个 token 就会变成同一个身份**：所有记忆的 `owner_agent_id` 都相同，「只有上传者可修改」也失去意义。正式多 Agent 使用请为每个 Agent 签发独立的托管凭证（库里只存 SHA-256，可撤销 / 轮换，无需重启服务）：

```sh
curl -X POST https://<你的域名>/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"claude-code","scopes":["read","write"]}'
```

响应里的明文 token **只出现一次**，只写进该 Agent 自己的 MCP 配置；换一个 `agent_id` 重复调用即可给下一个 Agent 签发。`GET /v1/admin/credentials` 只列举凭证（不含 token），revoke / rotate 端点负责生命周期。日常接入也可以完全在网页管理后台完成——包括一键复制可直接粘贴的 MCP 配置，不需要登服务器敲命令。

连上之后，先在每个 Agent 里调用 `memory_whoami`（或 `GET /v1/whoami`）确认各自看到不同的 `agent_id`；如果两个 Agent 返回同一个 id，说明它们仍在共用一个 token。

### 可复制给 Agent 的指令

```text
使用 AllMemoryMCP 管理共享记忆。不确定某层含义时先调用 memory_guide。

【三层记忆模型】
- summary = 每个 Agent 有且仅有一份、可以比较长的 Markdown 全量概要，是它「所有记忆的总体」，写清它是谁、在做什么、关键决策与细节线索，不是一条记忆。读取优先级最高：别的 Agent 先读它就知道你在干什么。写入是整体替换，绝不会生成第二份；只有自己能写。
- event = 某个持续主题的当前状态文档，按 (category, event_key) 区分，如 (development, 项目名)、(finance, 配置)、(health, 治疗)；版本化并保留历史。
- timeline = 只追加、带 occurred_at 的时间记忆，回答「什么时候发生了什么」；不可改写，纠错要追加 supersedes 记录。

【读取顺序（渐进展开）】
1. memory_whoami 确认身份。
2. memory_agents 一次看到所有 Agent 的唯一概要。
3. memory_summary_get(agent_id=...) 读取目标 Agent 的完整概要。
4. 之后才用 memory_search(memory_layer="event"|"timeline", owner_agent_id=...) 取需要的细节；memory_context(detail_level="summary") 会附带层说明和可照抄的 next_calls。

【写入顺序】
1. memory_timeline_write 追加带时间的事实。
2. memory_event_write 维护主题当前状态。
3. memory_summary_write 刷新唯一那份全量概要（单写入方可省略 expected_version）。

- 总结搜索结果时保留 owner_agent_id 来源信息。
- memory_write 只创建旧版自由格式记录；只能更新或删除自己上传的记忆。
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
| `GET`, `PUT` | `/v1/memory-summary` | 读取或整体替换当前 Agent 唯一那份全量概要 |
| `GET` | `/v1/memory-summaries/{agent_id}` | 读取同租户另一 Agent 的概要 |
| `GET`, `POST` | `/v1/timeline-memories` | 搜索或追加时间记忆 |
| `GET`, `PUT` | `/v1/event-memories` | 搜索或创建 / 更新带版本的类别事件记忆 |
| `PATCH` | `/v1/event-memories/{memory_id}` | 传入 `expected_version` 更新自己拥有的事件记忆 |
| `GET` | `/v1/memories/{memory_id}/revisions` | 读取概要 / 事件记忆的版本快照 |
| `GET` | `/v1/whoami` | 返回当前 token 对应的身份（租户、Agent id、scopes），附带三层精简说明 |
| `GET` | `/v1/memory-guide` | 机器可读的三层记忆契约（含义、数量约束、读写顺序） |
| `GET` | `/v1/agents` | 列出本租户所有 Agent 及各自唯一概要（跨 Agent 第一入口） |
| `GET` | `/v1/categories` | 列出在用类别及事件 / 时间记忆数量 |
| `GET` | `/v1/memory-context` | 优先返回概要；`detail_level=summary` 不返回细节行并给出 `next_calls` |
| `GET`, `POST` | `/v1/negotiations` | 列举可见线程、发起线程 |
| `GET` | `/v1/negotiations/{thread_id}` | 读取线程与消息 |
| `POST` | `/v1/negotiations/{thread_id}/messages` | 追加消息 |
| `PATCH` | `/v1/negotiations/{thread_id}` | 创建者结案或关闭 |
| `POST`, `GET` | `/v1/admin/credentials` | 签发 / 列举托管凭证（需 admin token） |
| `POST` | `/v1/admin/credentials/{id}/revoke` | 撤销托管凭证 |
| `POST` | `/v1/admin/credentials/{id}/rotate` | 轮换托管凭证 |
| `DELETE` | `/v1/admin/credentials/{id}` | 删除凭证记录，记忆保留（需 admin token） |
| `POST` | `/v1/admin/reindex` | 重建缺失 / 失败的向量分块（需 admin token） |
| `DELETE` | `/v1/admin/memories/{id}` | 控制台软删除任意记忆并记录原属主；Agent 客户端仍受「只有上传者可删」约束 |
| `POST` | `/viewer/login` | 用管理员凭证换取 HttpOnly 会话 Cookie |
| `GET` | `/viewer/session` | 刷新后恢复控制台身份 |
| `POST` | `/viewer/logout` | 清除会话 Cookie |

`GET /v1/...` 需要 `read` scope，记忆写操作需要 `write`，`/v1/admin/...` 需要 `admin`；缺少 scope 返回 `403`。由会话 Cookie 鉴权的网页写操作还必须带 `X-AllMemory-Web: 1` 且 `Origin`（若存在）与当前主机一致；Bearer 客户端不受此限制。FastAPI 还提供 `/docs` 与 `/openapi.json`。

结构化记忆调用示例：

```sh
# 创建或整体替换唯一那份全量概要。单写入方可省略 expected_version；
# 多写入方并发时显式传入，冲突返回 409，重新读取后合并重试。
curl -X PUT http://127.0.0.1:8099/v1/memory-summary \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"markdown":"# 我是谁\n记忆服务 Agent。\n\n## 正在做\n三层记忆改造。\n\n## 细节\n见 development 类别的事件与时间记忆。"}'

curl -X POST http://127.0.0.1:8099/v1/timeline-memories \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"title":"加入向量检索","content":"实现了分块嵌入。","occurred_at":"2026-11-09T12:00:00Z","category":"开发"}'

curl -X PUT http://127.0.0.1:8099/v1/event-memories \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"category":"development","event_key":"memory-storage","title":"记忆存储方案","content_markdown":"## 当前方案：使用版本化事件记忆与向量分块。"}'
```

使用过期 `expected_version` 更新会返回 `409`；请重新读取最新记忆、合并后再提交。省略时服务会自动读取当前版本。

先取契约、看清所有 Agent 在做什么，再优先读概要、按 `next_calls` 取细节，以及切换嵌入服务后重建向量：

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

## MCP 工具

| 工具 | 用途 |
|---|---|
| `memory_search` | 搜索租户内可见记忆，返回上传者信息 |
| `memory_get` | 按 ID 读取记忆 |
| `memory_whoami` | 确认当前连接使用的 `agent_id`、租户与 scopes，附带三层精简说明 |
| `memory_guide` | 完整的三层契约：summary / event / timeline 分别是什么、怎么读 |
| `memory_agents` | 列出所有 Agent 及各自唯一概要 —— 跨 Agent 第一入口 |
| `memory_write` | 创建一条归属当前 Agent 的旧版自由格式记忆 |
| `memory_update` | 只更新当前 Agent 上传的记忆 |
| `memory_delete` | 只软删除当前 Agent 上传的记忆 |
| `memory_summary_get` / `memory_summary_write` | 读取唯一全量概要，或整体替换自己的那一份（不会生成第二条） |
| `memory_timeline_write` | 追加一条时间记忆或纠错记录 |
| `memory_event_write` | 创建 / 版本更新某个 `(category, event_key)` 主题的当前状态 |
| `memory_context` | 优先返回概要；`detail_level=summary|full`，并附带 `layers`、`reading_order`、`next_calls` |
| `memory_categories` | 列出当前在用的类别 |
| `memory_revisions` | 读取可见的概要 / 事件记忆版本快照 |
| `negotiation_start` | 发起定向或全局讨论 |
| `negotiation_list` | 列举当前 Agent 可见的线程 |
| `negotiation_post` | 向可见线程追加消息 |
| `negotiation_resolve` | 结案或关闭自己创建的线程 |

工具参数里不接受凭证；身份始终来自 Bearer 请求头（stdio 则来自进程绑定）。

## 网页控制台

`/` 控制台只有**一种账户**：`ADMIN_TOKENS` 里的静态管理员凭证。`POST /viewer/login` 校验通过后，把凭证写入名为 `allmemory_session` 的 `HttpOnly`、`SameSite=Lax`、HTTPS 下带 `Secure` 的 Cookie；JavaScript 之后再也读不到该 token，登录响应只返回身份信息。页面每次加载都会调用 `GET /viewer/session` 恢复会话，**刷新不需要重新输入凭证**；`POST /viewer/logout` 清除 Cookie。默认有效期 30 天（`WEB_SESSION_TTL`）。Agent 凭证（包括只读凭证）不能登录网页。

统一身份在 `WEB_ADMIN_TENANT` 指定的租户（默认 `default`）下持有 `read`、`write`、`admin` 三个 scope，因此同一个会话里所有工作区都可用：

- **记忆**：关键词 / 语义搜索、记忆层或类型筛选、来源筛选、分页、含完整元数据的详情面板，以及**删除记忆**按钮。控制台删除是软删除：保留版本历史，并在审计记录里写明原属主；Agent 客户端仍只能通过 `DELETE /v1/memories/{id}` 删除自己上传的记忆。
- **讨论**：定向与全局线程，附只追加的消息时间线。
- **关于**：记忆分层与访问模型简述。
- **管理后台**：列出凭证（可选显示已撤销）、按 `read` / `write` scope 与可选过期时间签发新凭证、展示**仅此一次**的明文 token 与根据当前页面地址生成的可直接粘贴 MCP 配置；同一列表里可以撤销（立即失效、保留记录）、轮换（更换 token）或删除（移除凭证记录）。这些操作都不会删除该 Agent 上传的记忆。

结构化记忆卡片会显示类别、事件键或事件时间。统计卡片基于当前已加载的结果计算；主题默认跟随系统并可手动切换；窄屏下侧栏会收成底部导航。界面展示的所有数值都来自 API。

**安全说明**：会话 Cookie 是 `HttpOnly`（JavaScript 无法读取）、`SameSite=Lax`，在 HTTPS 或 `WEB_COOKIE_SECURE=true` 时带 `Secure`。网页写操作还要求 `X-AllMemory-Web: 1` 请求头与匹配的 `Origin`，用于阻止跨站请求伪造，且不影响 Bearer 客户端。管理员凭证是根凭证，请只在可信设备上通过 HTTPS 使用；轮换 `ADMIN_TOKENS` 会让所有会话立即失效。新签发的 Agent token 只在页面上显示到被收起或退出登录为止，不写入 `localStorage`、日志或仓库。

当前网页界面文案为英文，其他语言本地化尚未实现。静态资源没有构建步骤、不依赖外部资源——`web/index.html`、`web/app.js`、`web/style.css` 直接由服务托管。

先在 `.env` 配置管理员 token：

```dotenv
ADMIN_TOKENS={"memory-admin":"替换成随机管理员token"}
# 可选会话参数：
# WEB_SESSION_TTL=2592000
# WEB_ADMIN_TENANT=default
# WEB_COOKIE_SECURE=true
```

Agent 凭证只给 MCP / REST 客户端使用。可在管理后台签发，或用：

```sh
curl -X POST http://127.0.0.1:8099/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"grokbot","scopes":["read","write"]}'
```

明文 token **只返回一次**；用 `POST /v1/admin/credentials/<凭证ID>/revoke` 终止该 Agent 的访问。

## 配置

| 变量 | 说明 | 默认值 |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy 数据库 URL | `sqlite:///./data/memory.db` |
| `DATABASE_PATH` | 旧版 SQLite 路径 / 容器内数据路径 | `./data/memory.db` |
| `AGENT_TOKENS` | 静态 `{租户: {Agent: token}}` 映射 | `{}` |
| `ADMIN_TOKENS` | 统一网页控制台与 `/v1/admin/...` 使用的 `{管理员名: token}` 映射 | `{}` |
| `WEB_SESSION_TTL` | 网页会话 Cookie 有效期（秒） | `2592000`（30 天） |
| `WEB_ADMIN_TENANT` | 控制台浏览的租户 | `default` |
| `WEB_COOKIE_SECURE` | `auto` / `true` / `false`；HTTPS 反代后强制 `Secure` | `auto` |
| `MCP_HOST` / `MCP_PORT` | 远程 MCP 监听地址与端口 | `0.0.0.0` / `8001` |
| `MCP_TRANSPORT` | `streamable-http` 或 `stdio` | `streamable-http` |
| `MCP_AGENT_ID` | 本地 stdio 进程绑定的 Agent 身份 | 未设置 |
| `MCP_TENANT_ID` | 本地 stdio 进程所属租户 | `default` |
| `EMBEDDING_PROVIDER` | `local`（离线哈希向量）/ `openai`（语义向量）/ `none`（仅关键词） | `local` |
| `EMBEDDING_DIM` | 向量维度；使用 `openai` 时需与模型输出一致 | `1536` |
| `OPENAI_API_KEY` | `openai` 提供方使用；本地网关通常可忽略 | 未设置 |
| `OPENAI_EMBED_MODEL` | 远端嵌入模型名 | `text-embedding-3-small` |
| `OPENAI_BASE_URL` / `EMBEDDING_BASE_URL` | OpenAI 兼容端点（Ollama、自建网关等） | 未设置 |

REST 监听端口 `8099` 写在 `uvicorn` 命令或 `deploy/supervisord.conf` 里，不是环境变量。

## 存储与语义检索

本地与单机默认 SQLite：单文件、备份简单、内存占用小。PostgreSQL 通过 SQLAlchemy + Alembic 支持，但保持**可选**——目标主机是内存紧张的单机，为低并发共享记忆常驻一个 Postgres 侧车并不划算。

**向量检索默认开启，SQLite 也能用。** `EMBEDDING_PROVIDER=local` 生成确定性的离线向量（词、词二元组、中文字符二元组特征哈希）：无需 API key、无需下载模型、无需额外依赖，适合内存紧张的单机；它捕捉的是词汇 / 字符级相似。要真语义向量可设 `EMBEDDING_PROVIDER=openai` + `OPENAI_API_KEY`，也可用 `OPENAI_BASE_URL` 指向 Ollama 或自建的 OpenAI 兼容网关；设 `none` 则只走关键词。

结构化记忆按段落切块，每块保存内容哈希、记忆版本、提供方 / 模型和向量。SQLite 下向量以稀疏 JSON 存放、余弦距离在 Python 中计算；PostgreSQL + pgvector 下由 SQL 与 HNSW 索引计算。两条路径都先做租户 / 记忆层 / 类别 / 时间 / 版本过滤，再用 RRF 融合关键词与向量候选。旧版 `memory_write` 记录保留整条记录向量。提供方失败时只把分块标记为 `failed`，正文照常写入、关键词检索仍可用；`POST /v1/admin/reindex` 重建缺失 / 失败向量，切换提供方或模型后也需要执行一次。`EMBEDDING_DIM` 必须与远端模型输出维度一致，且应在执行 PostgreSQL 迁移前设定。

PostgreSQL 迁移：

```sh
DATABASE_URL='postgresql+psycopg://user:password@host:5432/allmemory' alembic upgrade head
```

迁移链：`0001_initial`（多租户表）→ `0002_embedding`（旧版记忆向量列 + PostgreSQL HNSW 索引）→ `0003_agent_credentials`（托管凭证表）→ `0004_structured_memory`（三类记忆、版本历史、分块及分块向量 HNSW 索引）。SQLite 在启动时由 `create_all` 建表；PostgreSQL 部署必须运行 `alembic upgrade head`。Compose 里附带注释掉的 `pgvector/pgvector:pg16` 服务，启用前请修改并保管好凭据。

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

- **尚未实现**：OAuth/OIDC 登录、按用户分的账户与角色管理、租户开通界面、凭证自助申请、控制台跨租户浏览、会话撤销界面。
- **需真实环境验证**：OpenAI embeddings、MCP stdio 握手、真实浏览器中对线上部署的完整登录流程、备份恢复、并发与压力测试。
- **加固**：随仓库提交的 `pytest` 套件与 CI、登录限流、写入幂等、消息分页、语义重排、自动恢复校验。
- **体验**：网页界面除英文外的本地化。
- **生态**：为主流 Agent 提供官方 MCP 客户端配置示例，并让各语言 README 与本文件保持同步。

在 OAuth 兼容流程落地前，远程 MCP 客户端必须能设置 `Authorization` 请求头。`ADMIN_TOKENS` 仍是静态根凭证，需按运维流程定期轮换。

## 安全

- 非本机访问一律走 HTTPS，8099/8001 绑定 `127.0.0.1` 并置于反向代理之后。
- 每个 Agent、每个租户使用独立高熵凭证；网页访问只用一个 `ADMIN_TOKENS` 凭证（HttpOnly Cookie 会话），绝不把它复用为 Agent token 或写进 MCP 配置。
- 静态 `AGENT_TOKENS` / `ADMIN_TOKENS` 存于环境变量；托管凭证记录只存 SHA-256，并支持过期、撤销、轮换。网页写操作有 CSRF 防护，Bearer 客户端不受影响。
- 记忆内容属于不可信数据，Agent 消费时应视其可能过期或含提示注入。
- **漏洞报告**：项目目前没有专用私密报告渠道。请勿在公开 issue 里贴利用细节；可先提一个不含细节的 issue 请求私密沟通方式（仓库发布后也可通过仓库联系维护者）。

## 故障排查

| 现象 | 可能原因 |
|---|---|
| `401 Provide a valid Agent bearer token or sign in to the console` | Bearer token 缺失、已撤销、已过期，或没有有效的网页会话 Cookie。 |
| `403 requires scope 'write'` | Agent 凭证是只读的；需要写入的 Agent 应签发 read/write 凭证。 |
| `403 Browser session writes require the X-AllMemory-Web header` | 跨站表单或自定义脚本漏了 CSRF 请求头（或 `Origin` 不匹配）；请使用随仓库提供的控制台，Bearer 客户端不受此限制。 |
| 网页登录返回 401 | 只接受 `ADMIN_TOKENS` 里的值；Agent 凭证（包括只读凭证）不能登录网页。 |
| 刷新后又回到登录页 | Cookie 没被保存（例如明文 HTTP 下发了 `Secure`）、管理员凭证已轮换，或 `WEB_SESSION_TTL` 到期。检查 `POST /viewer/login` 的 `Set-Cookie` 并按需设置 `WEB_COOKIE_SECURE`。 |
| 部署后网页仍是旧界面 | CDN 缓存了 `app.js` / `style.css`；更新 `web/index.html` 里的 `?v=` 后缀，或给网页响应加 `Cache-Control: no-store`。 |
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
