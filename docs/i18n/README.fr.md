# AllMemoryMCP

**Un service de mémoire partagé et isolé par tenant pour les agents IA.** Il fournit une API REST et MCP afin que les agents d’un même tenant partagent et discutent des souvenirs tout en conservant leur provenance et leurs droits de propriété.

### Copier dans votre AGENT

Copiez ce prompt dans votre agent de code : il lira le dépôt, installera et déploiera le service, puis vous guidera pas à pas (le bouton de copie de GitHub se trouve en haut à droite du bloc de code) :

```markdown
Lis ce dépôt GitHub, installe-le et déploie-le pour moi, puis guide-moi pas à pas dans son utilisation : https://github.com/mohui1999/AllMemoryMCP
```

**Langues :** [English](../../README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Deutsch](README.de.md) | Français | [Русский](README.ru.md)

## Fonctionnalités

- MCP distant via Streamable HTTP et pont local stdio
- API REST et console Web à compte unique (lecture, suppression, administration, session par cookie)
- Isolation par tenant et attribution de chaque souvenir à son agent source
- Seul l’agent propriétaire peut modifier ou supprimer logiquement son souvenir
- Discussions directes ou globales avec messages ajoutés sans écrasement
- Identifiants gérés avec scopes, expiration, révocation et rotation
- SQLite par défaut (recherche vectorielle hors ligne activée) ; PostgreSQL + pgvector en option
- `memory_guide` décrit les 3 couches à l'exécution, `memory_agents` liste tous les agents avec leur synthèse

## Couches de mémoire et ordre de lecture

- `summary` : **UN SEUL** long document Markdown par agent. C'est la **vue d'ensemble de TOUS ses souvenirs** (qui il est, ce qu'il fait, décisions, renvois vers les détails) — pas un souvenir parmi d'autres. Écrire **remplace l'intégralité du document** et ne crée jamais une deuxième synthèse. **Priorité de lecture maximale** : un autre agent comprend immédiatement à quoi il travaille.
- `event` : l'**état actuel** d'un sujet durable, identifié par `(category, event_key)` — développement, finances, santé, etc. Versionné, avec historique des révisions.
- `timeline` : entrée de journal datée, en ajout seul, avec `occurred_at` — « que s'est-il passé et quand ? ». Une correction ajoute une entrée avec `supersedes_memory_id`.

Ordre de lecture : `memory_whoami` → `memory_agents` (toutes les synthèses d'un coup) → `memory_summary_get(agent_id=...)` → **ensuite seulement** `memory_search(memory_layer="event"|"timeline", owner_agent_id=...)`. Le contrat complet est fourni par `memory_guide` / `GET /v1/memory-guide` ; `memory_context(detail_level="summary")` renvoie la synthèse, les descriptions de couches et des `next_calls` prêts à l'emploi. Ordre d'écriture : `memory_timeline_write` → `memory_event_write` → `memory_summary_write` (mettre à jour l'unique synthèse).

## Démarrage local

Python 3.11 ou ultérieur est requis.

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Dans `.env`, définissez un jeton aléatoire distinct pour chaque agent :

```dotenv
AGENT_TOKENS={"default":{"codex":"RANDOM_TOKEN_1","cursor":"RANDOM_TOKEN_2"}}
DATABASE_URL=sqlite:///./data/memory.db
DATABASE_PATH=./data/memory.db
MCP_HOST=0.0.0.0
MCP_PORT=8001
MCP_TRANSPORT=streamable-http
```

Lancez les deux services dans des terminaux séparés :

```sh
# Terminal 1 : API REST et interface Web
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8099
```

```sh
# Terminal 2 : MCP distant
source .venv/bin/activate
python mcp_server.py
```

Interface : <http://127.0.0.1:8099/> · REST : `http://127.0.0.1:8099/v1` · MCP : `http://127.0.0.1:8001/mcp`

## Déploiement Docker Compose

```sh
git clone https://github.com/mohui1999/AllMemoryMCP.git
cd AllMemoryMCP
cp .env.production.example .env
# Remplacez tous les exemples AGENT_TOKENS et ADMIN_TOKENS dans .env.
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8099/health
```

REST et MCP tournent dans un conteneur ; les données sont conservées dans `./data`. Pour une exposition publique, configurez un proxy HTTPS. Caddy utilise `/` pour l’interface, `/mcp` pour MCP et `/api/v1/...` pour REST. L’exemple Nginx utilise `/allmemory/`, `/allmemory/mcp` et `/allmemory/api/v1/...`. N’exposez pas directement les ports 8099/8001.

## Connexion d’un agent

Configurez le client MCP de l’agent :

```text
MCP URL: https://<domaine>/mcp
HTTP header: Authorization: Bearer <agent-token>
```

Avec le sous-chemin Nginx : `https://<domaine>/allmemory/mcp`. Utilisez un jeton distinct par agent. Si le client ne permet pas de configurer l’en-tête Authorization, utilisez stdio local. Ne placez jamais le jeton dans une invite ou un argument d’outil.

Instructions à transmettre à l’agent connecté :

```text
Utiliser AllMemoryMCP pour la mémoire partagée.
- Appeler memory_search avant d’ajouter un souvenir afin d’éviter les doublons.
- Conserver owner_agent_id comme source lors des résumés.
- Ajouter avec memory_write ; ne modifier ou supprimer que ses propres souvenirs.
- Pour une discussion privée, negotiation_start avec channel="direct" et recipients=[...] ; pour une discussion du tenant, channel="global".
- negotiation_post ajoute un message ; une discussion ne modifie jamais automatiquement la mémoire.
- Ne confirmer une écriture qu’après la confirmation de l’outil.
```

Pour stdio, lancer un processus distinct par agent :

```sh
MCP_TRANSPORT=stdio MCP_AGENT_ID=codex MCP_TENANT_ID=default \
DATABASE_URL=sqlite:////absolute/path/AllMemoryMCP/data/memory.db \
.venv/bin/python /absolute/path/AllMemoryMCP/mcp_server.py
```

## Exemple REST

```sh
curl -X POST http://127.0.0.1:8099/v1/memories \
  -H 'Authorization: Bearer <agent-token>' -H 'Content-Type: application/json' \
  -d '{"content":"La production utilise PostgreSQL","memory_type":"fact","tags":["deployment"]}'

curl 'http://127.0.0.1:8099/v1/memories?q=PostgreSQL&owner_agent_id=codex&limit=20&offset=0' \
  -H 'Authorization: Bearer <agent-token>'
```

Outils MCP : `memory_search`, `memory_get`, `memory_write`, `memory_update`, `memory_delete`, `negotiation_start`, `negotiation_list`, `negotiation_post`, `negotiation_resolve`.

## Console Web unifiée

La console Web n'a **qu'un seul compte** : un jeton administrateur statique `ADMIN_TOKENS`. `POST /viewer/login` le stocke dans un cookie `HttpOnly`, `SameSite=Lax` (`Secure` en HTTPS). JavaScript ne voit jamais le jeton ; **un rechargement de page ne demande pas de nouvelle saisie**, et `POST /viewer/logout` efface le cookie. Durée par défaut : 30 jours (`WEB_SESSION_TTL`).

La même session donne accès à tous les espaces : recherche et lecture des mémoires, **suppression logique de n'importe quelle mémoire**, fils de négociation, et émission / révocation / rotation / suppression des identifiants d'agent. Les identifiants d'agent (y compris `read` seul) sont réservés aux clients MCP/REST et ne permettent pas de se connecter au navigateur.

Émettez un identifiant distinct par agent :

```sh
curl -X POST http://127.0.0.1:8099/v1/admin/credentials \
  -H 'Authorization: Bearer <admin-token>' -H 'Content-Type: application/json' \
  -d '{"tenant_id":"default","agent_id":"grokbot","scopes":["read","write"]}'
```

Le jeton en clair n'apparaît **qu'une fois**. Les écritures du navigateur exigent l'en-tête `X-AllMemory-Web: 1` et un `Origin` concordant ; les clients Bearer ne sont pas concernés.

## Base de données et licence

Migrations PostgreSQL :

```sh
DATABASE_URL='postgresql+psycopg://user:password@host:5432/allmemory' alembic upgrade head
```

La recherche vectorielle est activée par défaut : des embeddings locaux hors ligne, sans clé API, fonctionnent avec SQLite. Pour de vrais vecteurs sémantiques, définissez `EMBEDDING_PROVIDER=openai` et `OPENAI_API_KEY` (ou un endpoint compatible OpenAI) ; PostgreSQL + pgvector utilise alors HNSW. Les détails figurent dans le [README anglais](../../README.md). Utilisez HTTPS pour tout accès distant. OAuth/OIDC n’est pas encore implémenté.

Ce projet est distribué sous [licence MIT](../../LICENSE).
