# Deploying the Signalor backend to a Hetzner VPS

Single-box Docker Compose deployment, fully self-hosted:
**Caddy → Django/Gunicorn web → Celery worker → Postgres → Redis**, with the two
scheduled management commands and nightly DB backups run from the host crontab.

- **Server:** Ubuntu, `178.105.176.220` (8 GB)
- **Data stores:** Postgres + Redis run as containers on this box (not managed).
  Postgres starts empty — this is a fresh DB (migrate + create a superuser below).
- **Volume:** mounted at `/mnt/HC_Volume_105845076` — Postgres data, backups,
  Caddy TLS certs.
- **App image:** built in CI, pulled from GHCR (Chromium baked in for screenshots)

All paths below assume the repo lives at `/opt/signalor` and you run commands
from `/opt/signalor/be/deploy`.

---

## 0. Prerequisites (one-time)

**DNS** — point your API hostname at the server *before* starting Caddy, or
Let's Encrypt can't validate:

```
api2.signalor.ai.   A   178.105.176.220     # temp hostname to validate before cutover
```

> Keep `api.signalor.ai` pointed at Render until the VPS is verified, then
> repoint it here. Whatever hostname you use must match `Caddyfile` + `ALLOWED_HOSTS`.

**Firewall** — open SSH + HTTP + HTTPS. In the **Hetzner Cloud Console**
firewall (if attached) allow inbound `22, 80, 443`. And on the host:

```bash
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
```

---

## 1. Install Docker + Compose plugin

```bash
curl -fsSL https://get.docker.com | sh
docker compose version   # confirm the v2 plugin is present
```

---

## 2. Create the durable directories on the volume

```bash
mkdir -p /mnt/HC_Volume_105845076/{postgres,backups,caddy/data,caddy/config}
```

> Postgres (5432) and Redis (6379) are **not** published to the host — only
> reachable on the internal Docker network. Keep it that way.

---

## 3. Get the code

```bash
mkdir -p /opt && cd /opt
git clone <your-repo-url> signalor
cd /opt/signalor/be/deploy
```

---

## 4. Configure secrets

```bash
cp stack.env.example stack.env
```

Fill in the values. Key ones:

- **`DB_PASSWORD`** → pick a strong one (`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`).
  Leave `DATABASE_URL` **unset** — the stack uses the `DB_*` vars so the local
  Postgres (no TLS) connects cleanly. `DB_HOST=db`, `DB_NAME=signalor`, `DB_USER=signalor`.
- **`REDIS_URL`** / **`CELERY_BROKER_URL`** → `redis://redis:6379/0` (the local container).
- **`SECRET_KEY`** / **`ENCRYPTION_KEY`** → reuse your existing values so stored
  OAuth tokens keep working. (Fresh SECRET_KEY: `python3 -c "import secrets; print(secrets.token_urlsafe(64))"`.)
- **Email** → set `EMAIL_HOST_USER=apikey` + `EMAIL_HOST_PASSWORD=<SendGrid key>`
  (production reads these names, *not* `SMTP_USER`/`SMTP_PASS`).
- The rest: `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, OpenRouter/Serper keys,
  OAuth, payments, OTEL header.

Also set your hostname in **`Caddyfile`** (currently `api2.signalor.ai`) so it
matches `ALLOWED_HOSTS`.

---

## 5. Build and start

```bash
docker compose build          # first build pulls Chromium + deps — several minutes
docker compose up -d
docker compose ps             # all services should be "running"/"healthy"
docker compose logs -f web    # watch migrations run, then gunicorn boot
```

The `web` container runs `reconcile_migrations.py` + `migrate` automatically on
every start before gunicorn takes over.

---

## 6. Create an admin user

```bash
docker compose exec web python manage.py createsuperuser
```

---

## 7. Verify

```bash
# From the server — should return JSON (not a 400/502):
curl -sS https://api2.signalor.ai/api/auth/get-session

# TLS cert issued?
docker compose logs caddy | grep -i certificate
```

Then hit `https://api2.signalor.ai/<ADMIN_URL>` in a browser and log in.

---

## 8. Scheduled jobs + backups (host crontab)

These replace Render's two cron services and add DB backups. `exec` reuses the
running `web` container (cheap; Chromium image already loaded).

```bash
chmod +x /opt/signalor/be/deploy/backup.sh
crontab -e
```

Add:

```cron
# Scheduled analyses — every 30 min (matches Render's */30 schedule)
*/30 * * * * cd /opt/signalor/be/deploy && /usr/bin/docker compose exec -T web python manage.py run_scheduled_analyses >> /var/log/signalor-cron.log 2>&1

# Cleanup stale rewards — daily 04:00
0 4 * * * cd /opt/signalor/be/deploy && /usr/bin/docker compose exec -T web python manage.py cleanup_stale_rewards >> /var/log/signalor-cron.log 2>&1

# Postgres backup — daily 03:00 → /mnt/HC_Volume_105845076/backups (7-day retention)
0 3 * * * /opt/signalor/be/deploy/backup.sh >> /var/log/signalor-backup.log 2>&1
```

> The Celery **worker** (sitemap audits) runs as a long-lived container — it is
> *not* a cron job.

---

## 9. CI/CD — auto-deploy on `main` (GitHub Actions)

Workflow: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml). On every
push to `main` it **builds** the image on GitHub's runners, **pushes** it to
GHCR (`ghcr.io/rishi-optiminastic/signalor-be`), then the **deploy** job SSHes
into this VPS and runs `docker compose pull && up -d` — no build on the prod
box. The deploy job is gated behind a manual approval.

**One-time setup:**

1. **Repo secrets** (Settings → Secrets and variables → Actions → *New repository secret*):
   - `SSH_HOST` = `178.105.176.220`
   - `SSH_USER` = `root` (or a dedicated deploy user)
   - `SSH_KEY` = the **private** key whose public half is in the server's
     `~/.ssh/authorized_keys`. Generate a dedicated pair:
     ```bash
     ssh-keygen -t ed25519 -f deploy_key -C "gh-actions-deploy" -N ""
     ssh-copy-id -i deploy_key.pub root@178.105.176.220   # or append manually
     # paste the contents of `deploy_key` (private) into the SSH_KEY secret
     ```
   - `SSH_PORT` = `22` (optional; omit to default to 22)

2. **Manual approval gate** (Settings → Environments → *New environment* →
   `production`): add yourself under **Required reviewers**. The deploy job then
   pauses for a one-click approval after each successful build.

3. **GHCR pull access** — handled automatically: the workflow logs the VPS into
   GHCR with its own ephemeral token each run, so no long-lived PAT is needed.
   (The package inherits the repo's private visibility.)

That's it — `git push origin main` → build → approve in the Actions tab → live.
The image is tagged `sha-<commit>`; the deploy writes `APP_TAG` into
`deploy/.env` so compose runs that exact commit and rollbacks are deterministic.

**Rollback:** re-run the workflow for an earlier green commit and approve, or on
the box set `APP_TAG=sha-<older>` in `deploy/.env` and `docker compose up -d`.

## 9b. Updating manually (fallback if CI is unavailable)

```bash
cd /opt/signalor && git pull
cd be/deploy
docker compose build web worker      # builds locally instead of pulling from GHCR
docker compose up -d                 # recreates changed services; migrations run on web start
docker compose logs -f web
```

---

## Restoring a backup

```bash
gunzip -c /mnt/HC_Volume_105845076/backups/signalor_<STAMP>.sql.gz \
  | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

---

## Operational notes

- **Fresh empty DB:** Postgres starts empty. If you later want your Render/Neon
  data here, `pg_dump` from Neon and pipe into the `db` container (see Restoring).
- **Volume is only 10 GB.** Watch it: `df -h /mnt/HC_Volume_105845076`. Resize in
  the Hetzner console (then `resize2fs /dev/sdb`) before it passes ~70%.
- **Memory (8 GB):** web (gunicorn + Chromium), worker, Postgres, Redis share the
  box — Chromium screenshots are the heaviest. If you see OOM kills
  (`dmesg | grep -i oom`), drop gunicorn `--workers`/`--threads` or worker
  `--concurrency`, or add swap.
- **Logs** stream to stdout → `docker compose logs`, and (if OTEL is configured)
  to Better Stack.
- **Webhook/OAuth callback URLs** (Shopify, Dodo, Stripe, Google) must point at
  the hostname you cut over to — update them in each provider's dashboard.
