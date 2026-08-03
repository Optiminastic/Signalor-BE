# App boundaries: capability, not vendor

Status: proposal. Supersedes the app list in `docs/modularization-plan.md` §3.3.

---

## 1. The test

> **If I add a second provider of this capability, do I need a new app?**
> If yes, the app is named after an implementation, not a capability.

`github_agent` fails it. A WordPress agent doing the same job — read a finding,
edit the site, report back — would need `wordpress_agent`, then `shopify_agent`,
and so on. The app name encodes *who does the work* instead of *what the
business does*.

Three questions decide where any code goes:

| Question | Home |
|---|---|
| What does the business do? | `apps/<capability>/` |
| How does the system work? | `core/` |
| What can everyone reuse? | `shared/` |
| **Who provides it?** | **`apps/integrations/<provider>/`** |
| **How is it delivered?** | **a channel inside the capability** |

The last two rows are the ones missing today, and they are exactly where the
current layout leaks.

---

## 2. The failure is already in the database

The "apply a fix to the customer's site" capability exists **twice**, split by
provider rather than modelled once:

| | `analyzer.AutoFixJob` | `github_agent.GithubFixJob` |
|---|---|---|
| run | `analysis_run` | `analysis_run` |
| what to fix | `recommendation` FK | `finding_codes` JSON |
| provider handle | `integration` → WordPress/Shopify | `installation` → GitHub |
| outcome | `status`, `response_data` | `pr_url`, `branch_name` |

Two tables, one concept. `apps/analyzer/auto_fix.py` is 870 lines and
`apps/github_agent/` is 3,535 — both implementing "take a finding, change the
site, record what happened" against different providers.

Adding a WordPress agent today means a third model. That is the cost of naming
an app after a vendor, and it is already paid.

---

## 3. What each misnamed app actually is

| Today | What it really is | Belongs |
|---|---|---|
| `github_agent` | a **capability** (auto-remediation) + a **provider** (GitHub API, App auth, sandbox, repo matching) | split: `apps/remediation/` + `apps/integrations/github/` |
| `drip` | one **campaign** on one **channel** (pricing emails) | `apps/notifications/` (email channel) |
| `webhooks` | one **channel** (outbound HTTP) | `apps/notifications/` (webhook channel) |
| `referrals` | actor → attribution → reward | `apps/growth/` |
| `partners` | actor → attribution → commission → payout | `apps/growth/` |

`referrals` and `partners` are the same three-step shape twice:

```
ReferralCode → Referral        → ReferralReward
Partner      → PartnerAttribution → PartnerCommission → PartnerPayout
```

Attribute a signup to a source, compute what is owed, pay it. One capability,
two programs.

`drip` and `webhooks` are likewise one capability: **tell someone something
happened**. Email, webhook and (future) Slack are channels of it, not apps.

---

## 4. Target layout

```
apps/                          "what does the business do?"
├── accounts/                  identity, sessions, agency membership
├── billing/                   subscriptions, invoices, plan limits
├── organizations/             brands, corpus, brand profile
│
├── analyzer/                  the GEO analysis engine
│   ├── crawling/              fetch + technical audit
│   ├── prompts/               tracked prompts, engine answers
│   ├── competitors/
│   └── citations/
├── recommendations/           findings -> prioritised tasks
├── remediation/               ← apply a fix. PROVIDER-AGNOSTIC.
│   ├── planning/                what to change (was github_agent/agent, fixers)
│   ├── sandbox/                 safe execution
│   └── providers.py             port: whoever can apply it
├── content/                   blog, backlinks, satellite network
├── visibility/                standalone brand probes
├── growth/                    ← referrals + partners
│   ├── referrals/
│   └── partners/
├── notifications/             ← drip + webhooks, by channel
│   ├── email/                   campaigns incl. the pricing drip
│   ├── webhook/                 outbound HTTP + delivery log
│   └── slack/
│
├── integrations/              ← EVERY provider adapter
│   ├── github/                  ← client, auth, webhook, repo_match (~595 lines)
│   ├── wordpress/  shopify/  woocommerce/
│   ├── ga4/  gsc/
│   └── slack/  webflow/  framer/
└── public_api/                external REST surface
```

### The test, re-run

**"I have a new WordPress agent."**

- Adapter: `apps/integrations/wordpress/` — **already exists** (537-line service).
- Register it as a remediation provider via `remediation/providers.py`.
- **No new app. No new model. No migration.**

That is the whole point of the split.

---

## 5. Why this is not just renaming

`github_agent` divides cleanly along the seam:

| Provider-specific -> `integrations/github/` | Lines | Capability -> `remediation/` | Lines |
|---|---:|---|---:|
| `client.py` | 107 | `agent.py` | 483 |
| `auth.py` | 78 | `orchestrator.py` | 274 |
| `webhook.py` | 135 | `fixers.py` | 246 |
| `repo_match.py` | 204 | `sandbox.py` | 174 |
| `repo_profile.py` | 71 | `fixability.py` + `fixable.py` | 197 |
| **~595** | | **~1,374** | |

The 1,374 lines never mention a vendor in their purpose — they decide *what* to
change and verify it worked. Only the 595 know what a pull request is.

`core/ports/code_fix.py` (already built) is the seam this slots into: the
analyzer asks "can this be fixed?" without knowing who answers.

---

## 6. Cost, honestly

| Move | Models | Migration |
|---|---:|---|
| `github_agent` -> `remediation` + `integrations/github` | 2 | `SeparateDatabaseAndState`, `db_table` pinned |
| merge `AutoFixJob` + `GithubFixJob` | 2 -> 1 | **real data migration** — the only genuinely risky step |
| `drip` -> `notifications/email` | 2 | state-only |
| `webhooks` -> `notifications/webhook` | 2 | state-only (already moved once) |
| `referrals` + `partners` -> `growth` | 7 | state-only |

Every row except the merge is a code move with pinned tables, proven on
`webhooks` already: `sqlmigrate` emits `-- (no-op)`.

**The merge is different.** Collapsing two job tables into one needs a real data
migration and is the only step that can lose rows. Recommended sequencing:

1. Move the boundaries first (state-only, reversible).
2. Introduce a provider port so both job types are created through one service.
3. Merge the tables last, once one code path writes both.

Do not start at step 3.

---

## 7. What stays as-is

- `analyzer` keeps its name. It is a capability ("analyse a site for GEO"), not
  a vendor, and it has 75 migrations.
- `visibility` is a real capability (quick standalone brand probe), not a
  channel or provider.
- `public_api` is a delivery surface, correctly its own app.
- `organizations`, `accounts` are capabilities.

---

## 8. Should there be an `agents/` folder?

**No.** It repeats the `github_agent` mistake on a different axis.

| Axis | Example | Why it fails |
|---|---|---|
| by **vendor** | `github_agent` | a new provider needs a new app |
| by **technique** | `agents/{github,wordpress,framer}` | "agent" is *how* the work is done, not what the business does |
| **by capability** | `remediation/` | a new provider needs a new *adapter* |

Two further reasons specific to this codebase:

1. **The name is taken.** `analyzer/agent_plan.py` is the *Growth Agent* - a read
   model over `UserAction` producing a customer's daily task list, with its own
   `AgentLogEntry` table. Nothing to do with fixing code. An `agents/` package
   would collide with an existing, unrelated concept.
2. **Everything is already an agent.** `auto_fix.py` calls `ask_llm` and
   `ask_structured` just as `github_agent` does. Both fixers are LLM-driven, so
   "agent" partitions nothing - it describes how most of the product works.

A folder name has to answer "what is this?" for someone who has never seen the
code. `agents/` answers "it uses an LLM", which is true of nearly every module
here.

---

## 9. Where a WordPress / Framer / Webflow agent goes

```
apps/remediation/                 the capability. One app, permanently.
├── planning.py                   what to change      (provider-agnostic)
├── verification.py               did it work         (re-crawl)
├── providers.py                  the PORT: who can apply this?
└── models.py                     FixJob - one table

apps/integrations/github/         the provider. Owns auth + client + PRs.
└── remediation.py                implements the port
apps/integrations/wordpress/      already exists (537 lines)
└── remediation.py                ← NEW. ~100 lines.
apps/integrations/framer/
└── remediation.py                ← one file. No new app, no migration.
```

The adapter lives beside the provider's other code rather than under
`remediation/providers/`: when WordPress changes its API you touch
`integrations/wordpress/`, where its auth and publishing already are. One folder
per provider, not two.

This is what `CLAUDE.md` §11.1 already requires - "external systems are accessed
only through adapter modules; the core never imports a vendor SDK directly".

---

## 10. The model collapse: TWO merges, in order

### 10.1 `GithubInstallation` -> `Integration`

`integrations.Integration` is already the generic provider table:

| Integration | GithubInstallation |
|---|---|
| `organization` FK | `organization` FK |
| `provider` (enum) | *implied: github* |
| `is_active` | - |
| encrypted credentials | - |
| `metadata` JSON | `installation_id`, `account_login`, `repo_full_name`, `repositories`, `default_branch`, `repo_locked`, `connect_slug` |

Every GitHub-specific column is what `metadata` exists for; its own comment reads
*"Provider-specific metadata (e.g. GA4 property_id, Shopify domain, WP site_url)"*.
The enum already contains `WEBFLOW` and `NEXTJS`, so adding `GITHUB` and `FRAMER`
is a one-line change.

`GithubInstallation` is the same anti-pattern one level down: a per-vendor copy of
a generic table.

**One caveat, not free:** `installation_id` is `unique=True`. Moved into
`metadata` it needs a unique partial index on `(provider, (metadata->>'installation_id'))`
or an explicit uniqueness check. Postgres supports this; it is a real migration,
not a rename.

### 10.2 `AutoFixJob` + `GithubFixJob` -> `FixJob`

Only possible *after* 10.1, because the two FKs are what forced two models:

- `AutoFixJob.integration` -> `integrations.Integration`
- `GithubFixJob.installation` -> `GithubInstallation`

Once GitHub is an `Integration`, one FK serves every provider:

```python
class FixJob(models.Model):
    analysis_run   = FK(AnalysisRun)
    recommendation = FK(Recommendation, null=True)   # from AutoFixJob
    finding_codes  = JSONField(default=list)         # from GithubFixJob
    integration    = FK(Integration)                 # ONE provider FK
    status         = CharField(choices=Status)
    plan           = JSONField(default=dict)         # intended change
    result         = JSONField(default=dict)         # pr_url / branch / response
    error_message  = TextField(blank=True)
```

`result` as JSON is the load-bearing choice. `AutoFixJob` already did this
(`payload_sent`, `response_data`); `GithubFixJob` instead hardcoded GitHub's
vocabulary into columns (`pr_url`, `branch_name`, `pr_number`). Provider-shaped
output is **data, not schema** - that is what lets one table serve all providers.

Telling detail: `GithubFixJob.__str__` already returns `f"FixJob #{self.pk}..."`.
The author was thinking "FixJob"; the `Github` prefix was incidental.

**Trade-off to accept knowingly:** `pr_number` currently has its own index. Moving
it into `result` JSON means a GIN index or an expression index if PRs are queried
by number. Check the access pattern before assuming it is needed.

---

## 11. Sequence

1. Add `GITHUB`, `FRAMER` to `Integration.Provider`. *(one line, no migration risk)*
2. Extract `apps/remediation/` from `github_agent`, provider code to
   `integrations/github/`. *(state-only, `db_table` pinned)*
3. Introduce `providers.py` and route both existing fixers through it.
   *(no schema change - both job tables still exist)*
4. Migrate `GithubInstallation` rows into `Integration`. *(real data migration)*
5. Merge the job tables into `FixJob`. *(real data migration)*

Steps 1-3 are reversible and carry no data risk. 4 and 5 are the only ones that
can lose rows, and by then a single code path writes both - which is what makes
them safe to attempt.

Do not start at 5.
