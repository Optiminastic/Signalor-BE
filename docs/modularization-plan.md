# Backend Modularization Plan

Status: Phase 0 shipped. Phase 1 shipped. Phase 2 in progress. Phases 3 to 4 proposed.
Scope: every app in `apps/`, plus `core/`.

---

## 0. Relationship to ARCHITECTURE.md

`ARCHITECTURE.md` is the source of truth for structure decisions and stays that way.
This document does not replace it.
It supplies the measured audit behind `ARCHITECTURE.md` §5, and adds the enforcement that was missing.

Three ways this updates it:

1. **§4 names one cycle** (`analyzer <-> organizations`).
   There are **six**, all involving analyzer. See §2.2 below.
2. **§5's numbers have grown since it was written.**
   `views.py` 7,700 -> **8,403**. `models.py` 42 -> **44** classes. `urls.py` 108 -> **118** paths.
   The roadmap was written and not executed, and the debt compounded in the meantime.
   That is the argument for gating in CI rather than writing another roadmap.
3. **§1 lists `recommendation` as an app that owns recommendation generation.**
   It is not routed in `config/urls.py` and nothing imports it. See §2.3.

Terminology follows `ARCHITECTURE.md` §2: the read/query layer is `repositories/`, not `selectors/`.

---

## 1. Context

`apps/analyzer` has become the place where everything lands, but it is not the only problem.
This document audits all 11 apps plus `core/`, then proposes a staged route out that does not risk the database.

One premise needs correcting first, because it changes the shape of the plan.

**`core/` is not unused.**
It is registered and load-bearing:

- `"core"` is in `INSTALLED_APPS` (`config/settings/base.py:59`)
- `core.middleware.GlobalIPRateLimitMiddleware` is in `MIDDLEWARE` (`base.py:69`)
- `core.exceptions.custom_exception_handler` is the DRF `EXCEPTION_HANDLER` (`base.py:221`)
- 12 import sites across 6 apps

The real problem is that `core/` is **475 lines** and holds only HTTP cross-cutting concerns.
It is under-scoped, not unused.
Shared infrastructure that belongs there is instead buried in `analyzer`, which is why other apps import `analyzer` to get it.

---

## 2. Audit

### 2.1 Whole-backend inventory

Python LOC excluding migrations and tests.
"Deferred" counts function-level imports via AST (`scripts/check_deferred_imports.py`), which is the clearest
available proxy for circular-dependency workarounds.

| App | Files | Lines | Models | Migrations | Deferred imports | Largest file |
|---|---:|---:|---:|---:|---:|---|
| **analyzer** | **117** | **43,548** | **44** | **75** | **842** | `views.py` (8,403) |
| **integrations** | 27 | 8,366 | 7 | 11 | 75 | `views.py` (2,723) |
| **accounts** | 24 | 4,187 | 5 | 19 | 54 | `views.py` (1,648) |
| github_agent | 23 | 3,535 | 2 | 4 | 45 | `views.py` (653) |
| organizations | 20 | 2,027 | 3 | 9 | 30 | `services/brand_profile.py` (265) |
| public_api | 21 | 1,819 | 5 | 4 | 4 | `models.py` (335) |
| visibility | 13 | 1,375 | 1 | 2 | 6 | `pipeline/reddit_check.py` (341) |
| partners | 7 | 1,239 | 4 | 3 | 0 | `views.py` (548) |
| drip | 18 | 920 | 2 | 3 | 2 | `email_sender.py` (174) |
| referrals | 11 | 727 | 3 | 2 | 2 | `services.py` (320) |
| ~~recommendation~~ | 7 | 409 | 0 | 0 | 2 | `services/recommendation_engine.py` (224) |
| **core** | 4 | **475** | 0 | 0 | 0 | `exceptions.py` (303) |

analyzer is 5.2x the next largest app and holds 61% of all backend code.
The bottom six apps are healthy and are not the problem.

### 2.2 Headline finding: six circular dependencies between apps

Every one involves `analyzer`.

| Cycle | Outbound | Inbound |
|---|---:|---:|
| analyzer <-> **organizations** | 37 | 17 |
| analyzer <-> **accounts** | 33 | 3 |
| analyzer <-> **integrations** | 38 | 2 |
| analyzer <-> **github_agent** | 1 | 10 |
| analyzer <-> **public_api** | 1 | 7 |
| analyzer <-> **visibility** | 4 | 4 |

This is the root cause, and it explains the 842 deferred imports directly.
Python cannot load these modules at import time in either order, so the codebase pushes imports into function bodies to break the cycle at runtime.

**Any modularization that does not break these cycles will fail.**
Splitting files inside a cycle just moves the problem.

Full dependency matrix:

```
accounts       -> referrals(5) partners(5) organizations(5) analyzer(3) visibility(1) github_agent(1)
analyzer       -> integrations(38) organizations(37) accounts(33) visibility(4) public_api(1) github_agent(1)
drip           -> accounts(1)
github_agent   -> analyzer(10) organizations(4)
integrations   -> organizations(2) analyzer(2) accounts(2)
organizations  -> analyzer(17) accounts(4)
partners       -> none
public_api     -> analyzer(7) integrations(3) accounts(3) organizations(1)
recommendation -> none
referrals      -> accounts(2)
visibility     -> analyzer(4)
```

`partners` and `recommendation` are the only apps with zero outbound app coupling.

### 2.3 Dead code: `apps/recommendation`

- Listed in `INSTALLED_APPS` (`base.py:55`)
- Has a `urls.py`, but is **never included in `config/urls.py`** so no route reaches it
- No app imports it
- 0 models, 0 migrations, 409 lines

It is also a naming hazard: it collides with the `analyzer.Recommendation` model and `analyzer/pipeline/recommendations.py`, which makes grep results actively misleading.

**Action: awaiting a decision, not deleted.** On inspection it is not cruft but a
complete unrouted feature: upload a discovery-report PDF -> extract text ->
generate recommendations -> render a PDF (`views.py`, `pdf_parser`, `pdf_renderer`,
`recommendation_engine`). Either wire it into `config/urls.py` or delete it
deliberately. Leaving it listed in `INSTALLED_APPS` but unreachable is the worst
of the three.

### 2.4 The other two offenders

**`integrations` (8,366 lines).**
`views.py` is 2,723 lines, and the app carries 75 deferred imports.
It holds seven provider integrations (WordPress, Shopify, GA4, GSC, WooCommerce, plus snapshots) in one flat app.
This is the clearest case in the codebase for a provider-adapter structure: one subpackage per provider behind a common port.

**`accounts` (4,187 lines).**
`views.py` is 1,648 lines and the app mixes four genuinely separate concerns: authentication/identity, subscriptions and billing, agency/team membership, and gamification-adjacent account state.
`identity.py` here is infrastructure and belongs in `core`.

### 2.5 Worst individual files, backend-wide

| File | Lines |
|---|---:|
| `analyzer/views.py` | 8,403 |
| `integrations/views.py` | 2,723 |
| `analyzer/models.py` | 2,051 (44 models) |
| `analyzer/pipeline/recommendations.py` | 1,778 |
| `accounts/views.py` | 1,648 |
| `analyzer/pipeline/competitors.py` | 1,650 |
| `analyzer/tasks.py` | 1,607 |
| `analyzer/recommendation_verify.py` | 1,276 |
| `analyzer/pipeline/llm.py` | 1,147 |
| `analyzer/serializers.py` | 1,028 |

`analyzer/views.py` alone serves 118 routes, 43 of them under `runs/`.

### 2.6 Shared infrastructure trapped inside analyzer

Other apps import these purely because there is nowhere else to get them:

| Target | External import sites |
|---|---:|
| `analyzer.models` | 17 |
| `analyzer._cache` | 5 |
| `analyzer.pipeline.llm` | 4 |
| `analyzer.tasks` | 3 |
| `analyzer.pipeline.embeddings` | 3 |
| `analyzer.prompts` | 2 |
| `analyzer.task_verify` | 2 |

`_cache`, `pipeline.llm`, `pipeline.embeddings` and `prompts` carry no analyzer domain knowledge.
They are infrastructure, and they are what `core/` should own.

### 2.7 The constraint that dictates sequencing

`analyzer` has **75 migrations** (132 backend-wide).

Moving a model to another Django app is not a code move.
It needs `SeparateDatabaseAndState` with `db_table` pinned, or the ORM will drop and recreate tables.
Get it wrong and you lose data.

`AnalysisRun` is referenced 42 times, `Recommendation` 13, `PromptTrack` 12.

**This is why phases 0 to 3 touch zero models and carry zero migration risk. Only phase 4 does, and it is optional per domain.**

---

## 3. Target architecture

### 3.1 One dependency rule

Enforced in CI as an import-linter `layers` contract (`pyproject.toml`).

```
public_api | github_agent | drip | referrals | partners   facades / consumers
      -> analyzer          the analysis engine
      -> visibility        standalone brand probe
      -> integrations      third-party connectors
      -> organizations     brands, corpus
      -> accounts          identity, billing, agency
      -> core              kernel; imports no app
```

Two corrections this layering forced on the earlier audit:

- **`integrations` and `organizations` are not siblings.** An `Integration`
  belongs to an `Organization`, so `organizations` sits below.
- **There are seven cycles, not six.** `accounts <-> organizations` (5 out / 4
  back) does not involve analyzer and was missed because §2.2 only looked at
  analyzer's edges.

It also retired a mistake of mine: the previous contract flagged
`public_api -> analyzer` (7 edges) as debt. `public_api` sits *above* analyzer,
so that direction was always correct.

**24 violations remain**, enumerated in the contract's `ignore_imports`.
8 of them are `accounts/views.py` (1,648 lines) reaching up into six apps.

- `core` imports nothing from `apps.*`
- Domain apps never import each other directly; they go through `core` or an explicit port
- Cycles are a CI failure, not a code review opinion

This is the rule that kills all six cycles in 2.2.

### 3.2 `core/` becomes a real shared kernel

```
core/
  exceptions.py        # exists
  middleware.py        # exists
  throttling.py        # exists
  identity.py          # MOVED from apps/accounts/identity.py
  access.py            # MOVED from apps/analyzer/access.py, generalized
  cache.py             # MOVED from apps/analyzer/_cache.py
  pagination.py        # MOVED from apps/analyzer/pagination.py
  llm/
    client.py          # MOVED from apps/analyzer/pipeline/llm.py
    structured.py      # MOVED from apps/analyzer/pipeline/structured.py
    embeddings.py      # MOVED from apps/analyzer/pipeline/embeddings.py
    spend.py           # MOVED from apps/analyzer/services/llm_spend.py
    prompts/           # MOVED from apps/analyzer/prompts/
```

This alone removes every non-model reason another app imports `analyzer`.

### 3.3 Per-app verdict

| App | Verdict | Action |
|---|---|---|
| **analyzer** | Split | Phases 2 to 4 below |
| **integrations** | Restructure | One subpackage per provider behind a common port; split `views.py` |
| **accounts** | Split concerns | `identity` to core; separate auth / billing / agency |
| **github_agent** | Decouple | 10 imports of analyzer; depend on a port, not models |
| **organizations** | Decouple | 17 imports of analyzer is the worst cycle; invert it |
| **public_api** | Keep | Correct shape already; only fix its analyzer imports |
| **visibility** | Keep | Small and clean |
| **partners** | Keep | Zero coupling, 0 deferred imports. The reference example. |
| **drip** | Keep | Clean |
| **referrals** | Keep | Clean |
| **recommendation** | **Delete** | Dead code, see 2.3 |
| **core** | Grow | Section 3.2 |

### 3.4 Analyzer domain seams

Derived from the 44 models and the URL map.

| Domain | Models | Representative code |
|---|---|---|
| **run** | AnalysisRun, PageScore, BrandVisibility, AIVisibilityProbe, ScheduledAnalysis, AgentLogEntry | `tasks.py`, `run_guard.py` |
| **prompts** | PromptTrack, PromptResult, PromptCitation, PromptEvalLog, PromptWikipediaDraft, PromptSchemaArtifact | `pipeline/prompt_tracker.py` |
| **competitors** | Competitor | `pipeline/competitors.py` (1,650) |
| **citations** | CitationOutreach | `services/citation_gaps.py` |
| **tasks** | Recommendation, UserAction, TaskSatisfaction, UserGamification, ContentSuggestion, AutoFixJob | `pipeline/recommendations.py`, `auto_fix.py` |
| **backlinks** | Backlink{Snapshot,Opportunity,Schedule,Provider,Product,Order}, BlogAutomation{Config,Job}, BlogPost | `services/backlink_engine.py`, `blog_store.py` |
| **crawl** | CrawlerHit, SitemapAudit(+Page), SchemaWatch(+Page), GeoImprovement | `pipeline/crawler.py` |
| **rank** | RankAudit, RankQuery, RankResult | `pipeline/rank_tracker.py` |
| **brand** | BrandKit, OverviewInsightReport, DomainAnalyticsSnapshot | `services/brand_kit.py` |
| **infra** | LLMResponseCache | to `core` |

### 3.5 Layering, applied per domain

What `CLAUDE.md` §4 already mandates and `views.py` does not follow.

```
apps/<domain>/
  api/            routes + serializers only, no business logic
  services/       business rules, no framework request objects
  selectors/      read queries; all scoping lives here
  models/         one module per aggregate, not one 2,051-line file
  tasks/          celery entrypoints
```

---

## 4. Phases

Each phase is independently shippable and revertible.

### Phase 0 — Guardrails  ✅ SHIPPED

No behavior change. Makes everything after it safe and measurable.

Delivered:

- `pyproject.toml [tool.importlinter]` — 3 contracts, verified to fail on a new violation.
- `scripts/check_deferred_imports.py` + `scripts/deferred_imports_baseline.json` — AST-based ratchet.
- `.github/workflows/ci.yml` — new `architecture` job running both gates.
- `requirements-dev.txt` — `import-linter==2.3`.

1. Add `import-linter` with the §3.1 contract. Fail CI on violation.
   Start with the six cycles in 2.2 as known exceptions, then burn them down one at a time.
2. CI ratchet on deferred-import count. Baseline 842 analyzer / 75 integrations / 54 accounts (`scripts/deferred_imports_baseline.json`). It may not increase.
3. Coverage baseline for analyzer, integrations and accounts so phases 2 and 3 are provably behavior-preserving.

**Risk: none. Migrations: none.**

### Phase 1 — Core kernel  ✅ SHIPPED

`core/` went from 475 lines to a real shared kernel. All moves used `git mv`.

```
core/authentication.py   <- apps/accounts/authentication.py
core/identity.py         <- apps/accounts/identity.py
core/cache.py            <- apps/analyzer/_cache.py
core/pagination.py       <- apps/analyzer/pagination.py
core/llm/client.py       <- apps/analyzer/pipeline/llm.py
core/llm/structured.py   <- apps/analyzer/pipeline/structured.py
core/llm/observability.py<- apps/analyzer/pipeline/observability.py
core/llm/embeddings.py   <- apps/analyzer/pipeline/embeddings.py
core/llm/serper.py       <- apps/analyzer/pipeline/serper.py
core/llm/config.py       <- EMBEDDING_DIMENSIONS, was organizations/models.py
core/llm/cache_port.py   <- new: the response-cache inversion (Phase 2 work, landed early)
```

**Burn-down: 40 -> 31 edges. analyzer inline imports 842 -> 823.**

Two scope corrections against the original plan, both caught by the Phase 0 contract:

- `pipeline/llm.py` could not move until the response cache was inverted — it
  reached `LLMResponseCache`. Solved with `core/llm/cache_port.py`: core declares
  the port, `AnalyzerConfig.ready()` registers the adapter. Unregistered is a valid
  state, so a worker without the analyzer app still boots and simply skips caching.
- `prompts/` is **not** moving to core, contrary to the original plan. The render
  machinery is infrastructure but `templates/` is analyzer domain content. Fix that
  edge by having `organizations` own its brand-profile prompt instead.
- `analyzer/access.py` stays put: it imports `organizations.models` and
  `accounts.agency_utils`, so it is app-aware authorization, not kernel material.

**Not done:** `apps/recommendation` was NOT deleted — see §2.3.

1. Delete `apps/recommendation` (2.3), remove from `INSTALLED_APPS`.
2. Move shared infrastructure into `core/` per 3.2, least-coupled first:
   `_cache` -> `pagination` -> `identity` -> `access` -> `llm/*`.
3. Leave a thin re-export shim at each old path for one release, then delete.

**Risk: low. Migrations: none.**
**Payoff: removes the non-model half of every analyzer cycle. `core` grows from 475 to roughly 3,000 meaningful lines.**

### Phase 2 — Break the remaining cycles, decompose the three big `views.py`

Cycles first, because file splitting inside a cycle achieves nothing.

- **organizations <-> analyzer (37/17):** the worst. Invert with a port in `core`, or move the shared model reference behind an id-based lookup.
- **github_agent -> analyzer (10)** and **public_api -> analyzer (7):** depend on a service interface, not models.
- **integrations, accounts, visibility:** should fall out once `core` owns the shared pieces.

Then split, by the URL groups that already exist:

```
analyzer/api/     runs.py (43 routes), actions.py (9), prompts.py,
                  competitors.py, backlinks.py, brand.py, health.py
integrations/     one subpackage per provider behind a common port
accounts/         auth.py, billing.py, agency.py
```

Rules applied during the split:

- Every view resolves identity through `core.identity` and scopes through `core.access`.
  The security work already merged establishes the pattern; this makes it uniform.
- Query logic moves to `repositories/`, so per-endpoint scoping cannot be forgotten.
- Deferred imports become real top-level imports wherever the cycle is gone.
  Whatever will not lift is the defect list for phase 3.

**Risk: medium (large but mechanical). Migrations: none.**
**Validation: suite green per extracted file, and the deferred-import count must fall.**

### Phase 3 — Domain packages inside each app

Reorganize `analyzer/` into the 3.4 domains, still as **one Django app**.
`models.py` becomes `models/` with one module per aggregate, no table changes, no migration.

Most of the readability and ownership benefit lands here at zero database risk.
It also proves the boundaries are right before phase 4 makes them expensive to change.

**Risk: medium. Migrations: none.**

### Phase 4 — Extract separate Django apps (optional, per domain)

Only for domains that earned it in phase 3.

1. **`backlinks`** — 9 models, near-zero inbound coupling, its own S3 storage. Best first candidate.
2. **`rank`** — 3 models, self-contained.
3. **`prompts`** — 6 models, but `PromptTrack` has 12 external references. Higher cost.

Do **not** extract `run` or `tasks`.
`AnalysisRun` (42 refs) and `Recommendation` (13) are the hub; splitting them buys churn, not modularity.

Mechanics per extraction:

- Create app, move model classes, pin `db_table` to the existing name.
- `SeparateDatabaseAndState`: state-only delete in `analyzer`, state-only create in the new app.
- Keep FK column names identical.
- Verify `sqlmigrate` output is empty before applying anywhere.
- Rehearse on a production database clone. Test settings use sqlite and will not surface Postgres constraint or index naming issues.

**Risk: high. Migrations: yes, per extraction.**
**Gate: do not start until 0 to 3 are shipped and stable.**

---

## 5. What I recommend against

- **Big-bang restructure.** 43k lines and 75 migrations at once is unreviewable and unrevertible.
- **Splitting files before breaking cycles.** A cycle spread across more files is a worse cycle.
- **Moving models before the code is split.** Phase 3 tells us if a boundary is real; phase 4 makes it permanent. That order is not negotiable.
- **Extracting `run` or `tasks`.** The coupling numbers say they are the hub.
- **Splitting for symmetry.** `recommendation` is already a 409-line app with 0 models and 0 routes. More empty apps is not modularity.

---

## 6. Sequencing summary

| Phase | Work | Migration risk | Reversible |
|---|---|---|---|
| 0 | Guardrails, metrics baseline ✅ | none | trivially |
| 1 | `core/` kernel ✅ (40 -> 31 edges) | none | yes, via shims |
| 2 | Break 7 cycles (24 edges), split the big `views.py` ◐ | none | yes |
| 3 | Domain packages inside apps | none | yes |
| 4 | App extraction, per domain | **high** | hard |

Phases 0 to 3 remove most of the pain with zero database risk.
Phase 4 is where "separate app" actually happens, and it should be a deliberate per-domain decision rather than a default.

---

## 7. Open questions

1. Is the frontend coupled to current URL paths? Phase 2 preserves routes, but confirm before phase 4 changes any prefix.
2. Is a production database clone available for phase 4 rehearsal? If not, phase 4 should not start.
3. Does anything outside this repo (workers, scripts, the Slack bot) import `apps.analyzer.*` directly? That would constrain the phase 1 shim removal.
4. Is `apps/recommendation/services/recommendation_engine.py` (224 lines) wanted, or is the whole app safe to delete outright?
