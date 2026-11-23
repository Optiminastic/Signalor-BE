#!/usr/bin/env bash
#
# pg-s3-backup.sh
# Dump the PostgreSQL database running in the signalor-db-1 Docker container,
# compress it, upload to AWS S3, and notify Slack + email (AWS SES) on
# success or failure.
#
# Installed to: /usr/local/bin/pg-s3-backup.sh  (chmod 700)
# Secrets in:   /root/.pg-backup-notify.env     (chmod 600)
#
# The database is only ever READ (pg_dump). Nothing is dropped or modified
# on the live server. The --clean flag only writes drop statements *inside*
# the dump file, used solely if you later restore into a different target.
#
set -euo pipefail

# ---- config ----
# Stable labels: Coolify's resourceName changes when the service is re-created in a
# new panel (it did on 2026-11-18 and backups silently stopped), projectName doesn't.
CONTAINER="$(docker ps --filter label=coolify.projectName=signalor --filter label=com.docker.compose.service=db --format '{{.Names}}' | head -1)"
DB_NAME="signalor"
DB_USER="signalor"
S3_PREFIX="s3://optiminastic-storage-049882582087-ap-south-1-an/Backup/signalor"
BACKUP_DIR="/var/backups/postgres"
LOG="/var/log/pg-s3-backup.log"
NOTIFY_ENV="/root/.pg-backup-notify.env"
HOST="$(hostname)"
# ----------------

# Load SLACK_WEBHOOK_URL, SES_REGION, SES_FROM, SES_TO (created at install time)
# shellcheck source=/dev/null
source "$NOTIFY_ENV"

TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"
FILE="${DB_NAME}_${TIMESTAMP}.sql.gz"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

notify_slack() {  # $1=status emoji/text, $2=message
  [ -n "${SLACK_WEBHOOK_URL:-}" ] || return 0
  local payload
  payload=$(printf '{"text":"%s *PG backup* on `%s`\n%s"}' "$1" "$HOST" "$2")
  curl -fsS -X POST -H 'Content-type: application/json' \
    --data "$payload" "$SLACK_WEBHOOK_URL" >/dev/null 2>&1 || \
    log "WARN: Slack notification failed"
}

notify_email() { # $1=subject, $2=body
  [ -n "${SES_FROM:-}" ] || return 0
  aws ses send-email --region "${SES_REGION:-ap-south-1}" --from "$SES_FROM" \
    --destination "ToAddresses=$SES_TO" \
    --message "Subject={Data=$1},Body={Text={Data=$2}}" >/dev/null 2>&1 || \
    log "WARN: SES notification failed"
}

# On any error, notify failure (with the last log lines) and exit non-zero.
on_error() {
  local line="$1"
  # Clean up this run's temp dump if it exists (e.g. upload failed mid-way).
  rm -f "${BACKUP_DIR}/${FILE}" 2>/dev/null || true
  local tail_lines; tail_lines="$(tail -n 15 "$LOG" 2>/dev/null)"
  local msg="FAILED at line ${line}. Recent log:\n${tail_lines}"
  log "ERROR: backup failed at line ${line}"
  notify_slack ":red_circle:" "$msg"
  notify_email "[FAILED] PG backup ${DB_NAME} on ${HOST}" "$msg"
  exit 1
}
trap 'on_error $LINENO' ERR

mkdir -p "$BACKUP_DIR"
if [ -z "$CONTAINER" ]; then log "ERROR: no running signalor db container found (labels coolify.projectName=signalor, com.docker.compose.service=db)"; false; fi
log "Starting dump of ${DB_NAME} from container ${CONTAINER}"

# Dump inside the container (local socket, trust auth), compress on the host.
docker exec -t "$CONTAINER" \
  pg_dump -U "$DB_USER" -d "$DB_NAME" --no-owner --clean --if-exists \
  | gzip > "${BACKUP_DIR}/${FILE}"

SIZE="$(du -h "${BACKUP_DIR}/${FILE}" | cut -f1)"
log "Dump complete: ${FILE} (${SIZE}). Uploading to ${S3_PREFIX}/"

aws s3 cp "${BACKUP_DIR}/${FILE}" "${S3_PREFIX}/${FILE}"

# Remove only the temp file this run created; never touches anything else.
rm -f "${BACKUP_DIR}/${FILE}"
log "Uploaded and cleaned local temp copy. Done."

# Success notification
SUCCESS_MSG="SUCCESS ${FILE} (${SIZE}) -> ${S3_PREFIX}/"
notify_slack ":large_green_circle:" "$SUCCESS_MSG"
notify_email "[OK] PG backup ${DB_NAME} on ${HOST}" "$SUCCESS_MSG"
