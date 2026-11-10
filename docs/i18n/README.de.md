# AllMemoryMCP

**Ein mandantengetrennter gemeinsamer Speicherdienst für KI-Agenten.** REST-API und MCP ermöglichen das Teilen und Besprechen von Erinnerungen innerhalb eines Mandanten; Urheber und Eigentumsrechte bleiben erhalten.

### An deinen AGENTEN kopieren

Kopiere den folgenden Prompt in deinen Coding-Agenten: Er liest das Repository, installiert und deployt den Dienst und führt dich Schritt für Schritt durch die Nutzung (GitHub zeigt die Kopierschaltfläche oben rechts im Codeblock):

```markdown
Lies dieses GitHub-Repository, installiere und deploye es für mich und führe mich Schritt für Schritt durch die Nutzung: https://github.com/mohui1999/AllMemoryMCP
```

**Sprachen:** [English](../../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | Deutsch | [Français](README.fr.md) | [Русский](README.ru.md)

## Funktionen

- Remote-MCP über Streamable HTTP und lokale stdio-Anbindung
- REST-API und Webkonsole mit einem Konto (Lesen, Löschen, Verwaltung, Cookie-Sitzung)
- Mandantentrennung; alle Agenten eines Mandanten dürfen dessen Erinnerungen lesen
- Nur der Uploader darf eine Erinnerung ändern oder logisch löschen
- Direkte und globale Verhandlungs-Threads mit nur anhängbaren Nachrichten
- Verwaltete Zugangsdaten mit Scopes, Ablauf, Sperrung und Rotation
- SQLite standardmäßig (Offline-Vektorsuche bereits aktiv); PostgreSQL + pgvector optional
- `memory_guide` erklärt das 3-Schichten-Modell zur Laufzeit, `memory_agents` listet alle Agenten samt Überblick

## Speicherschichten und Lesereihenfolge

- `summary`: **GENAU EIN** langes Markdown-Dokument pro Agent. Es ist die **Gesamtübersicht über ALLE Erinnerungen** dieses Agenten (wer es ist, woran es arbeitet, Entscheidungen, Verweise auf Details) — nicht ein einzelner Eintrag. Ein Schreibvorgang **ersetzt das gesamte Dokument**; ein zweiter Summary entsteht nie. **Höchste Lesepriorität**: andere Agenten erkennen damit sofort, woran dieser Agent arbeitet.
- `event`: der **aktuelle Stand** eines laufenden Themas, identifiziert durch `(category, event_key)`, z. B. Entwicklung, Finanzen, Gesundheit. Versioniert mit Revisionshistorie.
- `timeline`: ein unveränderlicher, datierter Logeintrag mit `occurred_at` — „was geschah wann?". Korrekturen werden mit `supersedes_memory_id` neu angehängt.

Lesereihenfolge: `memory_whoami` → `memory_agents` (alle Zusammenfassungen auf einmal) → `memory_summary_get(agent_id=...)` → **erst danach** `memory_search(memory_layer="event"|"timeline", owner_agent_id=...)`. Der vollständige Vertrag steht in `memory_guide` / `GET /v1/memory-guide`; `memory_context(detail_level="summary")` liefert die Übersicht, Schichtbeschreibungen und konkrete `next_calls`. Schreibreihenfolge: `memory_timeline_write` → `memory_event_write` → `memory_summary_write` (die eine Übersicht aktualisieren).

## Lokaler Start

Voraussetzung: Python 3.11 oder neuer.

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Tragen Sie in `.env` pro Agent ein eigenes starkes Token ein:

```dotenv
AGENT_TOKENS={"default":{"codex":"RANDOM_TOKEN_1","cursor":"RANDOM_TOKEN_2"}}
DATABASE_URL=sqlite:///./data/memory.db
DATABASE_PATH=./data/memory.db
MCP_HOST=0.0.0.0
MCP_PORT=8001
MCP_TRANSPORT=streamable-http
```

Starten Sie beide Dienste in separaten Terminals:

```sh
# Terminal 1: REST-API und Weboberfläche
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8099
```

```sh
# Terminal 2: Remote-MCP
source .venv/bin/activate
python mcp_server.py
```

Web: <http://127.0.0.1:8099/> · REST: `http://127.0.0.1:8099/v1` · MCP: `http://127.0.0.1:8001/mcp`

## Deployment mit Docker Compose

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
cp .env.production.example .env
# Alle Platzhalter für AGENT_TOKENS und ADMIN_TOKENS in .env ersetzen.
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8099/health
```

REST und MCP laufen gemeinsam in einem Container; Daten liegen in `./data`. Für öffentliche Zugriffe ist ein HTTPS-Reverse-Proxy erforderlich. Caddy nutzt `/` für das Web, `/mcp` für MCP und `/api/v1/...` für REST. Das Nginx-Beispiel nutzt `/allmemory/`, `/allmemory/mcp` und `/allmemory/api/v1/...`. Ports 8099/8001 nicht direkt ins Internet stellen.

## Agenten verbinden

Konfigurieren Sie den MCP-Client des Agenten:

```text
MCP URL: https://<domain>/mcp
HTTP header: Authorization: Bearer <agent-token>
```

Für den Nginx-Unterpfad: `https://<domain>/allmemory/mcp`. Jeder Agent benötigt ein eigenes Token. Unterstützt ein Client keine Authorization-Header, verwenden Sie stdio lokal. Tokens niemals in Prompts oder Tool-Argumente einfügen.

Diese Anweisungen können Sie dem verbundenen Agenten geben:

```text
AllMemoryMCP für gemeinsame Erinnerungen verwenden.
- Vor dem Schreiben mit memory_search nach Duplikaten suchen.
- owner_agent_id beim Zusammenfassen als Quelle erhalten.
- Neue Einträge mit memory_write anlegen; nur eigene Einträge ändern oder löschen.
- Für direkte Gespräche negotiation_start mit channel="direct" und recipients=[...] verwenden, für tenantweite Diskussionen channel="global".
- negotiation_post hängt Nachrichten an; Diskussionen ändern Erinnerungen nicht automatisch.
- Erst nach bestätigtem Tool-Ergebnis Erfolg melden.
```

Für stdio einen separaten Prozess pro Agent starten:

```sh
MCP_TRANSPORT=stdio MCP_AGENT_ID=codex MCP_TENANT_ID=default \
DATABASE_URL=sqlite:////absolute/path/AllMemoryMCP/data/memory.db \
.venv/bin/python /absolute/path/AllMemoryMCP/mcp_server.py
```

## REST-Beispiel

```sh
curl -X POST http://127.0.0.1:8099/v1/memories \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"content":"Produktion nutzt PostgreSQL","memory_type":"fact","tags":["deployment"]}'

curl 'http://127.0.0.1:8099/v1/memories?q=PostgreSQL&owner_agent_id=codex&limit=20&offset=0' \
  -H 'Authorization: Bearer <agent-token>'
```

MCP-Tools: `memory_search`, `memory_get`, `memory_write`, `memory_update`, `memory_delete`, `negotiation_start`, `negotiation_list`, `negotiation_post`, `negotiation_resolve`.

## Einheitliche Webkonsole

Die Weboberfläche hat **nur ein Konto**: ein statischer `ADMIN_TOKENS`-Administrator-Token. `POST /viewer/login` speichert ihn in einem `HttpOnly`-, `SameSite=Lax`-Cookie (bei HTTPS `Secure`). JavaScript sieht den Token nie; **nach einem Refresh bleibt die Sitzung bestehen**, `POST /viewer/logout` löscht das Cookie. Standardlaufzeit: 30 Tage (`WEB_SESSION_TTL`).

Dieselbe Sitzung öffnet alle Bereiche: Suche und Ansicht von Erinnerungen, **logisches Löschen beliebiger Erinnerungen**, Verhandlungs-Threads sowie Ausstellen / Sperren / Rotieren / Entfernen von Agent-Zugangsdaten. Agent-Credentials (auch reine `read`-Tokens) sind ausschließlich für MCP/REST-Clients und können sich nicht im Browser anmelden.

Für jeden Agent wird ein eigener Credential ausgestellt:

```sh
curl -X POST http://127.0.0.1:8099/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"grokbot","scopes":["read","write"]}'
```

Der Klartext-Token erscheint **nur einmal**. Browser-Schreibzugriffe benötigen zusätzlich den Header `X-AllMemory-Web: 1` und einen passenden `Origin`; Bearer-Clients sind ausgenommen.

## Datenbank und Lizenz

PostgreSQL-Migrationen:

```sh
DATABASE_URL='postgresql+psycopg://user:password@host:5432/allmemory' alembic upgrade head
```

Die Vektorsuche ist standardmäßig aktiv: lokale Offline-Embeddings ohne API-Schlüssel funktionieren mit SQLite. Für echte semantische Vektoren `EMBEDDING_PROVIDER=openai` und `OPENAI_API_KEY` (oder einen OpenAI-kompatiblen Endpunkt) setzen; PostgreSQL + pgvector nutzt dann HNSW. Weitere Konfigurationen stehen in der [englischen README](../../README.md). Externe Bereitstellung nur über HTTPS; OAuth/OIDC ist nicht implementiert.

Dieses Projekt steht unter der [MIT License](../../LICENSE).
