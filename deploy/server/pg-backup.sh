#!/bin/sh
# Nightly logical backups of every running Postgres container on this host.
# Output: /var/backups/postgres/<container>/<UTC timestamp>.dump (pg_dump custom format)
# Restore: pg_restore -U <user> -d <db> --clean <file>
#
# Retention is by count, not age, so a large database cannot fill the disk:
#   KEEP_DEFAULT dumps per database, KEEP_LARGE for databases whose last dump exceeds LARGE_BYTES.
# A dump is skipped (and logged as FAILED) when free space is below MIN_FREE_FACTOR x its last dump size.
set -u
BACKUP_ROOT=/var/backups/postgres
KEEP_DEFAULT=7
KEEP_LARGE=2
LARGE_BYTES=1073741824
MIN_FREE_FACTOR=2
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
status=0

umask 077
mkdir -p "$BACKUP_ROOT"

latest_dump() {
  ls -1t "$1"/*.dump 2>/dev/null | head -n 1
}

file_bytes() {
  [ -n "$1" ] && [ -f "$1" ] && stat -c %s "$1" || echo 0
}

free_bytes() {
  df -P -B1 "$BACKUP_ROOT" | awk 'NR==2 {print $4}'
}

# prune <dir> <slots_to_reserve>: keep the newest (limit - slots_to_reserve) dumps.
prune() {
  dir=$1
  keep=$KEEP_DEFAULT
  [ "$(file_bytes "$(latest_dump "$dir")")" -gt "$LARGE_BYTES" ] && keep=$KEEP_LARGE
  keep=$((keep - $2))
  [ "$keep" -lt 1 ] && keep=1
  ls -1t "$dir"/*.dump 2>/dev/null | tail -n +"$((keep + 1))" | while read -r old; do rm -f "$old"; done
}

for c in $(docker ps --format '{{.Names}} {{.Image}}' | awk '$2 ~ /postgres/ {print $1}'); do
  dir="$BACKUP_ROOT/$c"
  mkdir -p "$dir"
  rm -f "$dir"/*.tmp

  # Make room first: prune down to keep-1 so tonight's dump brings it back to keep.
  prune "$dir" 1

  needed=$(( $(file_bytes "$(latest_dump "$dir")") * MIN_FREE_FACTOR ))
  if [ "$(free_bytes)" -lt "$needed" ]; then
    logger -t pg-backup "FAILED $c skipped: not enough free disk"
    status=1
    continue
  fi

  out="$dir/$STAMP.dump"
  if docker exec "$c" sh -c 'pg_dump -U "${POSTGRES_USER:-postgres}" -Fc "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"' > "$out.tmp" 2>"$dir/last-error.log" && [ -s "$out.tmp" ]; then
    mv "$out.tmp" "$out"
    rm -f "$dir/last-error.log"
    logger -t pg-backup "ok $c $(du -h "$out" | cut -f1)"
  else
    rm -f "$out.tmp"
    logger -t pg-backup "FAILED $c (see $dir/last-error.log)"
    status=1
  fi
  prune "$dir" 0
done

exit $status
