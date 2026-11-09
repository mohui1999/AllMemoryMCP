#!/usr/bin/env bash
# AllMemoryMCP backup — run from the project root (where ./data lives) or via cron.
# SQLite: atomic .backup + WAL capture.
# PostgreSQL: pg_dump (set USE_POSTGRES=1 and DATABASE_URL).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
DATA_DIR="${DATA_DIR:-./data}"
KEEP="${KEEP:-30}"

mkdir -p "$BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"

if [ "${USE_POSTGRES:-0}" = "1" ]; then
    : "${DATABASE_URL:?DATABASE_URL required for PostgreSQL backup}"
    OUT="$BACKUP_DIR/memory-$TS.sql"
    pg_dump "$DATABASE_URL" -f "$OUT"
    echo "PostgreSQL backup: $OUT"
else
    SQLITE="${DATA_DIR}/memory.db"
    if [ ! -f "$SQLITE" ]; then
        echo "No SQLite database at $SQLITE; nothing to back up." >&2
        exit 0
    fi
    OUT="$BACKUP_DIR/memory-$TS.db"
    sqlite3 "$SQLITE" ".backup '$OUT'"
    # Capture any pending WAL so a restore is fully consistent.
    [ -f "$SQLITE-wal" ] && cp "$SQLITE-wal" "$OUT-wal"
    # Checkpoint so the main file stays authoritative between backups.
    sqlite3 "$SQLITE" "PRAGMA wal_checkpoint(TRUNCATE);"
    echo "SQLite backup: $OUT"
fi

# Prune to keep the last K backups.
if [ "${USE_POSTGRES:-0}" = "1" ]; then
    ls -1t "$BACKUP_DIR"/*.sql 2>/dev/null | tail -n +"$((KEEP+1))" | xargs -r rm -f
else
    ls -1t "$BACKUP_DIR"/*.db 2>/dev/null | tail -n +"$((KEEP+1))" | xargs -r rm -f
fi
echo "Pruned to last $KEEP backups in $BACKUP_DIR"
