#!/usr/bin/env bash
# Pings Better Stack heartbeats only while the worker containers are actually
# running, so a red heartbeat means a genuinely dead worker (not a dead cron).
# Post-Coolify: containers are resolved by Coolify labels, not compose ps.
set -uo pipefail
ping_if_up() {
  local svc="$1" url="$2"
  [ -z "$url" ] && return 0
  local cid
  cid="$(docker ps -q --filter "label=coolify.projectName=signalor" --filter "label=com.docker.compose.service=${svc}" | head -1)"
  if [ -n "$cid" ] && [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null)" = "true" ]; then
    curl -fsS -m 10 "$url" >/dev/null 2>&1 || true
  fi
}
ping_if_up worker          "${SITEMAP_HEARTBEAT_URL:-}"
ping_if_up analysis-worker "${ANALYSIS_HEARTBEAT_URL:-}"
