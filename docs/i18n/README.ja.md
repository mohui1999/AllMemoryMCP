# AllMemoryMCP

**AI エージェント向けのテナント分離型共有メモリサービスです。** REST API と MCP を提供し、アップロード元と所有権を保ちながら、同じテナント内のエージェントが記憶を共有・検索・協議できます。

### エージェントにコピー

下のプロンプトをコーディングエージェントに貼り付けると、リポジトリを読み、インストールとデプロイを行い、使い方を順を追って案内してくれます（GitHub のコードブロック右上にコピーボタンがあります）：

```markdown
この GitHub リポジトリを読み、インストールとデプロイを行い、使い方を順を追って案内してください: https://github.com/mohui1999/AllMemoryMCP
```

**言語:** [English](../../README.md) | [简体中文](README.zh-CN.md) | 日本語 | [한국어](README.ko.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | [Русский](README.ru.md)

## 主な機能

- リモート MCP Streamable HTTP とローカル stdio
- REST API と読み取り専用の記憶ブラウザー
- テナント分離。同一テナント内のエージェントは相互に記憶を閲覧可能
- 記憶の更新・論理削除はアップロードしたエージェントのみ可能
- 直接/全体協議スレッド。メッセージは追記型で記憶を自動変更しない
- scope、有効期限、失効、ローテーションに対応した管理資格情報
- SQLite 標準。PostgreSQL と pgvector による任意の意味検索

## ローカルで始める

Python 3.11 以降が必要です。

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

`.env` の `AGENT_TOKENS` にエージェントごとの強力な個別トークンを設定します。

```dotenv
AGENT_TOKENS={"default":{"codex":"RANDOM_TOKEN_1","cursor":"RANDOM_TOKEN_2"}}
DATABASE_URL=sqlite:///./data/memory.db
DATABASE_PATH=./data/memory.db
MCP_HOST=0.0.0.0
MCP_PORT=8001
MCP_TRANSPORT=streamable-http
```

別々のターミナルで起動します。

```sh
# Terminal 1: REST API とブラウザー画面
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8099
```

```sh
# Terminal 2: リモート MCP
source .venv/bin/activate
python mcp_server.py
```

画面は <http://127.0.0.1:8099/>、REST は `http://127.0.0.1:8099/v1`、MCP は `http://127.0.0.1:8001/mcp` です。

## Docker Compose デプロイ

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
cp .env.production.example .env
# .env 内の AGENT_TOKENS と ADMIN_TOKENS のプレースホルダーをすべて置換
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8099/health
```

Compose は REST と MCP を同じコンテナーで起動し、データを `./data` に保存します。公開時は Caddy または Nginx で HTTPS を設定してください。Caddy は画面 `/`、MCP `/mcp`、REST `/api/v1/...`、Nginx の例は `/allmemory/`、`/allmemory/mcp`、`/allmemory/api/v1/...` を使用します。8099/8001 を直接公開しないでください。

## エージェント接続

MCP クライアントに次を設定します。

```text
MCP URL: https://<your-domain>/mcp
HTTP header: Authorization: Bearer <agent-token>
```

Nginx のサブパス構成では `https://<your-domain>/allmemory/mcp` を使用します。エージェントごとに別のトークンを発行してください。HTTP ヘッダーを設定できないクライアントではローカル stdio を使用します。トークンをプロンプトやツール引数へ入れないでください。

接続後、次の運用指示をエージェントに渡せます。

```text
AllMemoryMCP を共有メモリに使用する。
- 書き込む前に memory_search で重複を確認する。
- 結果を要約するとき owner_agent_id を情報源として保持する。
- memory_write で追加し、自分がアップロードした記憶だけを更新・削除する。
- negotiation_start の channel="direct" と recipients=[...] で個別協議、channel="global" で全体協議を開始する。
- negotiation_post はメッセージを追記する。協議メッセージは記憶を自動変更しない。
- ツールが成功を返す前に書き込み完了を報告しない。
```

stdio はエージェントごとに独立プロセスで起動します。

```sh
MCP_TRANSPORT=stdio MCP_AGENT_ID=codex MCP_TENANT_ID=default \
DATABASE_URL=sqlite:////absolute/path/AllMemoryMCP/data/memory.db \
.venv/bin/python /absolute/path/AllMemoryMCP/mcp_server.py
```

## REST 呼び出し

```sh
curl -X POST http://127.0.0.1:8099/v1/memories \
  -H 'Authorization: Bearer <agent-token>' \
  -H 'Content-Type: application/json' \
  -d '{"content":"本番環境は PostgreSQL を使用","memory_type":"fact","tags":["deployment"]}'

curl 'http://127.0.0.1:8099/v1/memories?q=PostgreSQL&owner_agent_id=codex&limit=20&offset=0' \
  -H 'Authorization: Bearer <agent-token>'
```

MCP ツール: `memory_search`、`memory_get`、`memory_write`、`memory_update`、`memory_delete`、`negotiation_start`、`negotiation_list`、`negotiation_post`、`negotiation_resolve`。

## 読み取り専用ブラウザー

管理者は `read` scope の資格情報を発行します。`agent_id` がユーザー名となり、発行時に一度だけ返される token がログイン資格情報です。

```sh
curl -X POST http://127.0.0.1:8099/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"viewer-alice","scopes":["read"]}'
```

ユーザーに `viewer-alice` と token を渡します。画面は読み取り専用資格情報のみを受け付け、token はページのメモリー内だけに保持します。

## データベースとライセンス

PostgreSQL のマイグレーション:

```sh
DATABASE_URL='postgresql+psycopg://user:password@host:5432/allmemory' alembic upgrade head
```

意味検索には PostgreSQL + pgvector と `EMBEDDING_PROVIDER=openai`、`OPENAI_API_KEY` が必要です。追加設定や API の詳細は[英語版 README](../../README.md)を参照してください。インターネット公開時は HTTPS を使用してください。OAuth/OIDC は未実装です。

本プロジェクトは [MIT License](../../LICENSE) の下で公開されています。
