#!/usr/bin/env bash
# Nightly Postgres backup → Hetzner volume, with 7-day retention.
# Wire into the host crontab (see DEPLOY.md). Dumps run inside the db container
# using its own POSTGRES_* env, so no credentials are needed here.
set -euo pipefail

COMPOSE_DIR=/opt/signalor/deploy
BACKUP_DIR=/mnt/HC_Volume_105845076/backups
STAMP=$(date +%F_%H%M%S)
RETAIN_DAYS=7

mkdir -p "$BACKUP_DIR"
cd "$COMPOSE_DIR"

# -T disables TTY allocation (required from cron). gzip on the host side.
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  | gzip > "$BACKUP_DIR/signalor_${STAMP}.sql.gz"

# Prune old dumps.
find "$BACKUP_DIR" -name 'signalor_*.sql.gz' -mtime +"$RETAIN_DAYS" -delete

echo "[backup] wrote $BACKUP_DIR/signalor_${STAMP}.sql.gz"

# Optional offsite copy to Backblaze B2 (you already use B2 for invoices).
# Install rclone + configure a remote named "b2", then uncomment:
# rclone copy "$BACKUP_DIR/signalor_${STAMP}.sql.gz" b2:your-bucket/db-backups/
