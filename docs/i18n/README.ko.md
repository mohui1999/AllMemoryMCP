# AllMemoryMCP

**AI 에이전트를 위한 테넌트 격리형 공유 메모리 서비스입니다.** REST API와 MCP를 제공하여 업로더 정보와 소유권을 보존하면서 같은 테넌트의 에이전트가 메모리를 공유하고 협의할 수 있습니다.

### 에이전트에 복사

아래 프롬프트를 코딩 에이전트에 붙여넣으면 저장소를 읽고 설치·배포를 도와주며 사용 방법을 단계별로 안내합니다(GitHub 코드 블록 오른쪽 위의 복사 버튼 이용):

```markdown
이 GitHub 저장소를 읽고 설치와 배포를 도와주고, 사용 방법을 단계별로 안내해 주세요: https://github.com/mohui1999/AllMemoryMCP
```

**언어:** [English](../../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | 한국어 | [Deutsch](README.de.md) | [Français](README.fr.md) | [Русский](README.ru.md)

## 기능

- 원격 MCP Streamable HTTP 및 로컬 stdio 연결
- REST API와 읽기 전용 메모리 브라우저
- 테넌트 격리, 업로더 표시, 소유자만 수정/소프트 삭제
- 직접/전체 협의 스레드와 추가 전용 메시지
- scope, 만료, 폐기, 교체를 지원하는 관리형 자격 증명
- SQLite 기본 지원(오프라인 벡터 검색 기본 활성화), PostgreSQL + pgvector 의미 검색 선택 지원

## 로컬 시작

Python 3.11 이상이 필요합니다.

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

`.env`의 `AGENT_TOKENS`에 에이전트별로 서로 다른 강력한 토큰을 설정합니다.

```dotenv
AGENT_TOKENS={"default":{"codex":"RANDOM_TOKEN_1","cursor":"RANDOM_TOKEN_2"}}
DATABASE_URL=sqlite:///./data/memory.db
DATABASE_PATH=./data/memory.db
MCP_HOST=0.0.0.0
MCP_PORT=8001
MCP_TRANSPORT=streamable-http
```

각각 다른 터미널에서 실행합니다.

```sh
# 터미널 1: REST API 및 웹 화면
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8099
```

```sh
# 터미널 2: 원격 MCP
source .venv/bin/activate
python mcp_server.py
```

웹: <http://127.0.0.1:8099/> · REST: `http://127.0.0.1:8099/v1` · MCP: `http://127.0.0.1:8001/mcp`

## Docker Compose 배포

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
cp .env.production.example .env
# .env의 AGENT_TOKENS와 ADMIN_TOKENS 자리표시자를 모두 교체합니다.
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8099/health
```

REST와 MCP는 같은 컨테이너에서 실행되고 데이터는 `./data`에 저장됩니다. 공개 배포에는 HTTPS 역방향 프록시를 사용하세요. Caddy 경로는 웹 `/`, MCP `/mcp`, REST `/api/v1/...`입니다. Nginx 예제는 `/allmemory/`, `/allmemory/mcp`, `/allmemory/api/v1/...`를 사용합니다. 8099/8001을 인터넷에 직접 공개하지 마세요.

## 에이전트 연결 및 실행 지침

에이전트의 MCP 설정에 다음을 입력합니다.

```text
MCP URL: https://<도메인>/mcp
HTTP header: Authorization: Bearer <agent-token>
```

Nginx 하위 경로는 `https://<도메인>/allmemory/mcp`입니다. 각 에이전트에 개별 토큰을 사용하세요. Authorization 헤더를 설정할 수 없는 클라이언트는 로컬 stdio를 사용하세요. 토큰을 프롬프트나 도구 매개변수에 넣지 마세요.

연결 후 에이전트에 전달할 지침:

```text
공유 메모리에 AllMemoryMCP를 사용한다.
- 쓰기 전에 memory_search로 중복을 확인한다.
- 요약 시 owner_agent_id 출처를 유지한다.
- memory_write로 새 메모리를 만들고, 자신이 올린 메모리만 수정/삭제한다.
- 개별 협의는 negotiation_start channel="direct", recipients=[...]를 사용하고 전체 논의는 channel="global"을 사용한다.
- negotiation_post는 메시지를 추가한다. 협의 메시지는 메모리를 자동 변경하지 않는다.
- 도구가 성공을 확인하기 전에 쓰기가 완료됐다고 말하지 않는다.
```

stdio 클라이언트는 에이전트별로 별도 프로세스를 실행합니다.

```sh
MCP_TRANSPORT=stdio MCP_AGENT_ID=codex MCP_TENANT_ID=default \
DATABASE_URL=sqlite:////absolute/path/AllMemoryMCP/data/memory.db \
.venv/bin/python /absolute/path/AllMemoryMCP/mcp_server.py
```

## REST 호출 예시

```sh
curl -X POST http://127.0.0.1:8099/v1/memories \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"content":"프로덕션은 PostgreSQL을 사용한다","memory_type":"fact","tags":["deployment"]}'

curl 'http://127.0.0.1:8099/v1/memories?q=PostgreSQL&owner_agent_id=codex&limit=20&offset=0' \
  -H 'Authorization: Bearer <agent-token>'
```

MCP 도구: `memory_search`, `memory_get`, `memory_write`, `memory_update`, `memory_delete`, `negotiation_start`, `negotiation_list`, `negotiation_post`, `negotiation_resolve`.

## 읽기 전용 사용자 화면

관리자 또는 권한이 있는 운영자는 `read` scope만 있는 자격 증명을 발급합니다. `agent_id`가 사용자 이름이며 발급 시 반환되는 token이 로그인 자격 증명입니다.

```sh
curl -X POST http://127.0.0.1:8099/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"viewer-alice","scopes":["read"]}'
```

사용자에게 `viewer-alice`와 token을 전달합니다. 화면은 읽기 전용 자격 증명만 허용하며 token은 현재 페이지 메모리에만 저장합니다.

## 데이터베이스 및 라이선스

PostgreSQL 마이그레이션:

```sh
DATABASE_URL='postgresql+psycopg://user:password@host:5432/allmemory' alembic upgrade head
```

벡터 검색은 기본 활성화이며, API 키 없이 동작하는 로컬 임베딩이 SQLite에서 작동합니다. 실제 의미 벡터가 필요하면 `EMBEDDING_PROVIDER=openai`와 `OPENAI_API_KEY`(또는 OpenAI 호환 엔드포인트)를 설정하세요. 추가 설정과 API는 [영문 README](../../README.md)를 참고하세요. 외부 서비스는 HTTPS를 사용하세요. OAuth/OIDC는 아직 구현되지 않았습니다.

본 프로젝트는 [MIT License](../../LICENSE)에 따라 배포됩니다.
