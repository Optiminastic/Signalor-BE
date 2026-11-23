# Production host scripts

Copies of the scripts installed on the production server, kept here so changes are reviewed and drift is visible.
The server is the source that runs; after changing one here, copy it to its install path.

| Script | Installed at | Runs | Does |
|---|---|---|---|
| `pg-backup.sh` | `/usr/local/sbin/pg-backup.sh` | `/etc/cron.d/pg-backup`, 02:30 UTC daily | `pg_dump -Fc` of every running Postgres container to `/var/backups/postgres/<container>/`, 7 kept |
| `pg-s3-backup.sh` | `/usr/local/bin/pg-s3-backup.sh` | root crontab, 08:00 Asia/Kolkata daily | Dumps the signalor database, uploads to S3 (off-site), notifies Slack and email on success or failure |
| `heartbeat-ping.sh` | `/opt/signalor/deploy/heartbeat-ping.sh` | root crontab, every 2 minutes | Pings a Better Stack heartbeat per worker while its container runs |

Secrets are not in these files:
- `pg-s3-backup.sh` reads `/root/.pg-backup-notify.env` (`SLACK_WEBHOOK_URL`, `SES_REGION`, `SES_FROM`, `SES_TO`).
- `heartbeat-ping.sh` gets `SITEMAP_HEARTBEAT_URL` and `ANALYSIS_HEARTBEAT_URL` from the crontab line.

## Finding containers

Find containers by the Coolify project and the compose service name:

```
docker ps --filter label=coolify.projectName=signalor --filter label=com.docker.compose.service=<service>
```

Never use `coolify.resourceName`: it changes when the service is re-created in a new Coolify panel.
On 2026-11-18 that silently stopped the S3 backup and the heartbeat until 2026-11-23.
