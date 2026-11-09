FROM python:3.12-slim

WORKDIR /app

# Runtime deps: supervisor to run REST+MCP as one container (shares the SQLite file),
# sqlite3 for backups, curl for healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    sqlite3 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data /var/log/supervisor

EXPOSE 8099 8001

# supervisord inherits the container environment (set by docker-compose) into both
# child processes, so DATABASE_PATH / AGENT_TOKENS / DATABASE_URL flow through.
CMD ["supervisord", "-c", "/app/deploy/supervisord.conf"]
