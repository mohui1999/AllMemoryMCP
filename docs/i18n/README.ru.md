# AllMemoryMCP

**Общий сервис памяти для ИИ-агентов с изоляцией арендаторов.** REST API и MCP позволяют агентам в одном арендаторе совместно хранить и обсуждать воспоминания, сохраняя сведения об источнике и владельце.

### Скопировать своему АГЕНТУ

Скопируйте этот промпт в своего ИИ-агента: он прочитает репозиторий, установит и развернёт сервис и пошагово проведёт вас по работе с ним (кнопка копирования GitHub — в правом верхнем углу блока кода):

```markdown
Прочитай этот GitHub-репозиторий, установи и разверни его для меня и пошагово объясни, как им пользоваться: https://github.com/mohui1999/AllMemoryMCP
```

**Языки:** [English](../../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Deutsch](README.de.md) | [Français](README.fr.md) | Русский

## Возможности

- Удалённый MCP через Streamable HTTP и локальный мост stdio
- REST API и веб-консоль с одной учётной записью (просмотр, удаление, администрирование, сессия в cookie)
- Изоляция арендаторов; источник каждой записи виден
- Изменять или логически удалять запись может только загрузивший её агент
- Прямые и общие ветки обсуждений; сообщения только добавляются и не меняют память автоматически
- Управляемые учётные данные: scopes, срок действия, отзыв и ротация
- SQLite по умолчанию (офлайн-векторный поиск включён); PostgreSQL + pgvector опционально
- `memory_guide` описывает три слоя во время работы, `memory_agents` показывает сводки всех агентов сразу

## Слои памяти и порядок чтения

- `summary`: **РОВНО ОДИН** длинный Markdown-документ на агента. Это **обзор ВСЕЙ памяти агента** (кто он, над чем работает, решения, ссылки на детали), а не одна запись. Запись **полностью заменяет документ**, вторая сводка никогда не создаётся. **Наивысший приоритет чтения**: другой агент сразу понимает, чем тот занимается.
- `event`: **текущее состояние** долгой темы, задаваемой парой `(category, event_key)` — разработка, финансы, здоровье и т. д. Версионируется, история редакций сохраняется.
- `timeline`: неизменяемая запись с датой `occurred_at` — «что и когда произошло». Исправление добавляет новую запись с `supersedes_memory_id`.

Порядок чтения: `memory_whoami` → `memory_agents` (все сводки сразу) → `memory_summary_get(agent_id=...)` → **и только затем** `memory_search(memory_layer="event"|"timeline", owner_agent_id=...)`. Полный контракт — `memory_guide` / `GET /v1/memory-guide`; `memory_context(detail_level="summary")` возвращает сводку, описания слоёв и готовые `next_calls`. Порядок записи: `memory_timeline_write` → `memory_event_write` → `memory_summary_write` (обновление единственной сводки).

## Локальный запуск

Требуется Python 3.11 или новее.

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

В `.env` задайте отдельный случайный токен для каждого агента:

```dotenv
AGENT_TOKENS={"default":{"codex":"RANDOM_TOKEN_1","cursor":"RANDOM_TOKEN_2"}}
DATABASE_URL=sqlite:///./data/memory.db
DATABASE_PATH=./data/memory.db
MCP_HOST=0.0.0.0
MCP_PORT=8001
MCP_TRANSPORT=streamable-http
```

Запустите службы в двух терминалах:

```sh
# Терминал 1: REST API и веб-интерфейс
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8099
```

```sh
# Терминал 2: удалённый MCP
source .venv/bin/activate
python mcp_server.py
```

Веб-интерфейс: <http://127.0.0.1:8099/> · REST: `http://127.0.0.1:8099/v1` · MCP: `http://127.0.0.1:8001/mcp`

## Развёртывание Docker Compose

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
cp .env.production.example .env
# Замените все шаблонные значения AGENT_TOKENS и ADMIN_TOKENS.
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8099/health
```

REST и MCP работают в одном контейнере; данные хранятся в `./data`. Для публичного доступа настройте HTTPS reverse proxy. Caddy использует `/` для веб-интерфейса, `/mcp` для MCP и `/api/v1/...` для REST. Пример Nginx использует `/allmemory/`, `/allmemory/mcp`, `/allmemory/api/v1/...`. Не публикуйте порты 8099/8001 напрямую.

## Подключение агента

Настройте MCP-клиент агента:

```text
MCP URL: https://<домен>/mcp
HTTP header: Authorization: Bearer <agent-token>
```

Для Nginx-подпути используйте `https://<домен>/allmemory/mcp`. Каждому агенту нужен отдельный токен. Если клиент не поддерживает заголовок Authorization, используйте локальный stdio. Не помещайте токен в prompt или аргументы инструмента.

Передайте подключённому агенту следующие инструкции:

```text
Используй AllMemoryMCP для общей памяти.
- Перед записью вызывай memory_search, чтобы избежать дубликатов.
- При пересказе сохраняй owner_agent_id как источник.
- Создавай записи через memory_write; изменяй/удаляй только свои записи.
- Для личного обсуждения вызывай negotiation_start с channel="direct" и recipients=[...], для общего — channel="global".
- negotiation_post только добавляет сообщение; обсуждение не изменяет память автоматически.
- Подтверждай запись только после успешного ответа инструмента.
```

Для stdio запускайте отдельный процесс для каждого агента:

```sh
MCP_TRANSPORT=stdio MCP_AGENT_ID=codex MCP_TENANT_ID=default \
DATABASE_URL=sqlite:////absolute/path/AllMemoryMCP/data/memory.db \
.venv/bin/python /absolute/path/AllMemoryMCP/mcp_server.py
```

## Пример REST

```sh
curl -X POST http://127.0.0.1:8099/v1/memories \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"content":"В production используется PostgreSQL","memory_type":"fact","tags":["deployment"]}'

curl 'http://127.0.0.1:8099/v1/memories?q=PostgreSQL&owner_agent_id=codex&limit=20&offset=0' \
  -H 'Authorization: Bearer <agent-token>'
```

Инструменты MCP: `memory_search`, `memory_get`, `memory_write`, `memory_update`, `memory_delete`, `negotiation_start`, `negotiation_list`, `negotiation_post`, `negotiation_resolve`.

## Единая веб-консоль

У веб-консоли **один тип учётной записи**: статический административный токен из `ADMIN_TOKENS`. `POST /viewer/login` сохраняет его в cookie `HttpOnly`, `SameSite=Lax` (`Secure` при HTTPS). JavaScript никогда не видит токен; **после обновления страницы вход сохраняется**, а `POST /viewer/logout` удаляет cookie. Срок по умолчанию — 30 дней (`WEB_SESSION_TTL`).

Одна сессия открывает все разделы: поиск и просмотр записей, **логическое удаление любой записи**, нити обсуждений, выпуск / отзыв / ротацию / удаление учётных данных агентов. Учётные данные агентов (включая `read`-only) предназначены только для клиентов MCP/REST и не подходят для входа в браузер.

Для каждого агента выпускайте отдельные учётные данные:

```sh
curl -X POST http://127.0.0.1:8099/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"grokbot","scopes":["read","write"]}'
```

Открытый token показывается **только один раз**. Записи из браузера требуют заголовка `X-AllMemory-Web: 1` и совпадающего `Origin`; на Bearer-клиентов это не влияет.

## База данных и лицензия

Миграции PostgreSQL:

```sh
DATABASE_URL='postgresql+psycopg://user:password@host:5432/allmemory' alembic upgrade head
```

Векторный поиск включён по умолчанию: локальные офлайн-эмбеддинги без API-ключа работают с SQLite. Для настоящих семантических векторов задайте `EMBEDDING_PROVIDER=openai` и `OPENAI_API_KEY` (или совместимый с OpenAI endpoint); PostgreSQL + pgvector использует HNSW. Остальные настройки описаны в [README на английском](../../README.md). Для удалённого доступа используйте HTTPS. OAuth/OIDC пока не реализован.

Проект распространяется по [лицензии MIT](../../LICENSE).
