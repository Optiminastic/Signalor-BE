# Scaling to 50 brands and 99.95% uptime

Status: plan for approval.
Nothing in this document has been deployed or changed in any environment.

Decided on 2026-11-18:
- The Growth Agent is sized for **50 active brands** (`GROWTH_MAX_ACTIVE_PROGRAMS=50`).
- The uptime target is **99.95%**: about 4.4 hours of downtime a year, or 22 minutes a month.
- 99.999% was considered and not chosen: it needs multi-region active-active and providers (Gemini, OpenAI, Gmail) that don't promise it.

## 1. What the code now does (U7, built and tested)

| Piece | What it does |
|---|---|
| `growth.interactive` queue + `growth-interactive-worker` | Chat replies and previews; never wait behind a site analysis |
| `growth.background` queue + `growth-worker` | One tick per brand per round: up to 3 drafts and 5 approved sends, reply checks, follow-ups, daily and weekly checks |
| `growth-scheduler` service | One round every 15 minutes; queues the ticks; checks health after each round |
| Redis | Round lock, per-brand tick lock, scheduler heartbeat, alert rate limit |
| Postgres advisory lock | The 50-brand cap, so simultaneous starts can't exceed it |
| Time limits | Chat 2 min, send 2 min, draft 5 min, tick 20 min, other checks 10 min; analyses keep 40 min |
| Dead-letter queue `growth.dlq` | A job that fails 3 deliveries is parked, not retried forever |
| `growth_health` | Alerts (Sentry + email to `GROWTH_OPS_ALERT_EMAILS`) when the scheduler, a brand or an approved send stalls |

Tested:
- 50 brands all get a tick in one round.
- A brand with a backlog gets only its fair share.
- One crashing brand doesn't stop the others.
- Overlapping ticks and rounds skip.
- Routing and time limits.
- The cap (Postgres race test: 9 of 5 places taken without the lock, exactly 5 with it).
- Heartbeat alerts, rate-limited.

## 2. Capacity for 50 brands

From the limits in the code (24 planned tasks and at most 40 actions per brand per month, 10 emails a day per brand):

| Work, for 50 brands | Volume | Worker time |
|---|---|---|
| Drafts (write + fact check) | about 50 to 150 a working day | 1 to 2 min each, so 1 to 5 hours a day |
| Approved sends | about 100 a working day at most | seconds each |
| Ticks with nothing to do | 50 every 15 min | a few seconds each |
| Weekly checks | 50 a week, spread over the week | several minutes each |
| Chat replies | bursty | 10 to 60 seconds each |

- `growth-worker` with 2 slots gives 48 slot-hours a day, against 1 to 5 hours of work: ample headroom.
- `growth-interactive-worker` with 2 slots handles 2 chats at the same moment; a 3rd waits seconds.
- Both start at 2 slots because of memory. Measured on the production server on 2026-11-18:
  - 4 CPUs, 7.6 GiB RAM, 4.8 GiB available, 4 GiB swap, 121 GB free disk;
  - existing workers use about 0.6 to 0.7 GiB each;
  - the 3 new services should add about 1.6 to 2 GiB.
- The real limits at 50 brands are provider rate limits (Gemini, OpenAI, Serper, Gmail), not workers.
- Raising the cap above 50 needs the load test re-run at the new size first.

## 3. What 99.95% needs, and what we have

Today production is one server running Postgres, Redis, RabbitMQ, web, all workers and Caddy.
Any reboot, disk failure or bad deploy is a full outage.
Two such incidents a year already use the whole 99.95% budget.

| # | Change | Why | Owner |
|---|---|---|---|
| H1 | **Managed Postgres with automatic failover** (for example Neon with a production branch and point-in-time restore, or a managed Postgres with a standby) | The database is the hardest single point of failure; failover in under a minute | Operator |
| H2 | **Backups tested by restoring**, with a documented restore time | Phase 1 gates B1 to B3 already require it | Operator |
| H3 | **Managed Redis** (or Redis with a replica and Sentinel) | Locks, heartbeats and the cache; loss means skipped rounds, not lost data | Operator |
| H4 | **RabbitMQ on its own host as a 3-node quorum cluster** (or a managed broker) | Queues are quorum queues already; a cluster keeps them through one node's loss | Operator |
| H5 | **Two or more web servers behind a load balancer**, with health checks on `/api/health/` | One web server restarting doesn't take the site down | Operator |
| H6 | **Workers on their own machine(s)**, separate from web | A memory-hungry analysis can't starve the web server | Operator |
| H7 | **Zero-downtime deploys**: migrations as a separate one-shot step (with `MIGRATION_DATABASE_URL`, never given to running containers), then rolling restart of web | Today web migrates on start, so every deploy restarts the only web server | Code + operator |
| H8 | **Uptime monitoring and on-call**: an external probe, Sentry alerts, `growth_health` alerts to a named person, and a response runbook | 99.95% is impossible without someone being told within minutes | Operator |
| H9 | **One scheduler instance**, restarted automatically | A second copy is harmless (Redis lock), but none means no rounds; `growth_health` alerts within 45 minutes | Operator |
| H10 | **Graceful worker shutdown** on deploy (`acks_late` already set, so an interrupted job is redelivered) | No lost jobs during restarts | Done in code |

What stays outside our control: the AI providers, Gmail and the customers' own sites.
The agent already degrades rather than fails when they do (sends wait, unknown outcomes go to reconciliation).

## 4. Suggested order

1. H2 (backups proven) and H8 (monitoring and on-call), since they are the cheapest and needed anyway.
2. H1 (managed Postgres) and H3 (managed Redis).
3. H6 (workers on their own machine) and H7 (separate migration step, rolling deploys).
4. H5 (second web server and load balancer) and H4 (broker cluster).

## 5. Open facts needed from an operator

- The current server's memory and CPU, to size the new worker services (they add about 4 Celery processes).
- The hosting provider and budget for managed Postgres and Redis.
- Who is on call, and the alert email addresses for `GROWTH_OPS_ALERT_EMAILS`.
- Whether a host cron currently runs `run_growth_tasks` in production; it must be removed when `growth-scheduler` starts, or the two run rounds together (safe, but wasteful).
