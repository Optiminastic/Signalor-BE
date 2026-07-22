# Backend Architecture & Conventions

The standard every app in `apps/` must follow, what each app is for, and the
remediation roadmap to bring the repo to production level. This is the source of
truth for structure decisions — when an app diverges from it, that divergence is
a bug to fix, not a precedent to copy.

---

## 1. What each app is (domain map)

The app names are valid Django (snake_case) and domain-meaningful — the problem
was that nothing documented them. It does now. **No rename is planned:** renaming
a Django app rewrites `app_label`, every migration, DB table prefixes, and every
import — high risk for zero functional gain. Keep the names; document them.

| App | Owns | Notes |
|-----|------|-------|
| `accounts` | Users, auth (Better Auth JWT verify), account profiles, agency membership, **subscriptions + invoices** (billing) | The money app. Needs the most test coverage. |
| `analyzer` | The GEO **analysis engine**: crawl → score 6 pillars → AI-visibility probes → recommendations. Also prompts, competitors, content optimisation, blog automation, backlinks, auto-fix, gamification. | **Overloaded** — 15+ domains in one app. The primary split target (§5). |
| `drip` | Pricing-page **drip email** lifecycle (`PricingDripState`, `DripSendLog`) via APScheduler | Small, single-purpose. |
| `github_agent` | GitHub App: installations + **fix jobs that open PRs** (sandbox orchestrator) | |
| `integrations` | Third-party **data connectors**: GA4, GSC, Shopify, WooCommerce, WordPress (OAuth + snapshots) | |
| `organizations` | Brands/orgs, **brand corpus** (pgvector embeddings), brand profile/context | |
| `partners` | **Partner program**: attribution, commissions, payouts | |
| `public_api` | The **external/public REST API**: API keys, usage metering, outbound webhooks, deploy hooks | Third-party-facing; strict auth + rate limits. |
| `recommendation` | **Recommendation/action generation** (service + endpoints, no own models) | |
| `referrals` | **Referral program**: codes, referrals, rewards | |
| `visibility` | Standalone **AI-visibility check** (`VisibilityCheck`) — the quick brand probe | |

---

## 2. The standard app skeleton

Every app follows the same layout. Deviations are debt.

```
apps/<app>/
  __init__.py
  apps.py                # AppConfig
  urls.py                # thin; path() → view
  admin.py               # register models (every app that has models)
  models.py  OR  models/ # split into a package once > ~500 lines
  serializers.py         # request + response schemas (Create/Update/Read separate)
  views.py   OR  views/  # thin: parse → service → serializer. Split once > ~500 lines
  services/              # business logic — ALWAYS a package (even a 1-file one), never a flat services.py, for consistency
    __init__.py
  repositories/          # ALL .objects/ORM access lives here (the only place queries are built)
    __init__.py
  selectors.py           # optional: read-side query helpers if a full repository is overkill
  tests/                 # ALWAYS a package with __init__.py; never a flat tests.py, never absent
    __init__.py
    test_*.py
  migrations/
```

**Rules (enforced, not aspirational):**
- **Layering:** `url → view → service → repository → db`. Views never build ORM
  queries or call external APIs directly. Repositories are the only place with
  `.objects`. Services hold rules and depend on repository interfaces.
- **File size:** no module > ~500 lines; no view class doing more than one
  resource. Split into a package by resource before it grows.
- **Serializers:** every endpoint returns an explicit **response serializer** —
  never a raw queryset, `.values()` dict, or ORM model.
- **No inline imports** to dodge circular deps — fix the dependency direction
  instead (see §4).
- **Consistency:** one convention only — `services/` package (not flat
  `services.py`), `tests/` package (not flat `tests.py`), `admin.py` present
  wherever there are models.

---

## 3. Naming conventions

- App: `snake_case`, domain noun (`public_api`, `github_agent`). ✅ current names comply.
- Models: `PascalCase` singular (`AnalysisRun`). Status/type fields use
  `TextChoices` enums, never loose strings.
- Views: `PascalCase` + `View` suffix. URL names: `kebab-case`.
- Services/functions: `snake_case`, verb-first (`start_sync`, `resolve_actor`).
- Constants: `SCREAMING_SNAKE_CASE`. No magic literals (`"syncing"` → a constant).
- One error envelope everywhere: `{ "detail", "code", "status_code" }`
  (via `core/exceptions.py`). No ad-hoc `{"error": ...}` / `{"message": ...}`.

---

## 4. Cross-app dependencies

- Dependencies point **one way**. `analyzer ↔ organizations` currently form a
  **cycle** papered over with inline imports — the shared surface (brand context,
  embeddings, cache keys) must be extracted into a neutral lower-level package
  both depend on, so the cycle is broken and inline imports removed.
- Apps talk to each other through **service functions**, never by reaching into
  another app's models/querysets.

---

## 5. Current-state gaps → remediation roadmap

Ordered so each step is independently shippable and **test-gated** (run
`python manage.py test --settings=config.settings.test` green after each).
Every import-changing move requires a bootable backend to verify — do NOT do
these blind.

### Structural debt (from the survey)
| Gap | Apps affected | Fix |
|-----|---------------|-----|
| No tests at all | `drip`, `partners`, `public_api`, `recommendation`, `referrals`, `visibility` | Add `tests/` package + regression tests (start with the money/webhook paths). |
| Flat `services.py` | `drip`, `partners`, `referrals` | Convert to `services/` package (import-transparent: `services.py` → `services/__init__.py`). |
| No service layer | `accounts`, `visibility` | Extract business logic out of views into `services/`. |
| No `admin.py` | `recommendation` | Add (or document why none). |
| No repository layer | **all** | Introduce `repositories/`; move `.objects` out of views incrementally. |

### The big splits (milestones — do with the suite green after each resource)
1. **Split `analyzer/views.py`** (7,700 lines / 106 classes) into `analyzer/views/`
   by resource (`runs.py`, `prompts.py`, `content.py`, `blog.py`, `backlinks.py`,
   `autofix.py`, `competitors.py`, `chat.py`, …). Keep the public import surface
   identical (re-export from `views/__init__.py`) so `urls.py` is untouched, then
   verify boot + tests before deleting the old file.
2. **Split `analyzer/models.py`** (42 classes) into `analyzer/models/` by domain.
3. **Split `analyzer/urls.py`** (108 paths) with `include()` per resource.
4. **Break the `analyzer ↔ organizations` cycle** (§4), then remove inline imports.
5. **Backfill `repositories/`** per resource as each view module is split.

### Consistency sweeps (mechanical, test-gated)
- One error envelope (replace 149 ad-hoc `{"error"/"detail"/"message"}` sites).
- Replace status-string literals with `TextChoices`/constants.
- Add explicit response serializers where views return `.values()` dicts.

---

## 6. Definition of "production level" (exit criteria)

- [ ] Every app matches the §2 skeleton (services/ pkg, tests/ pkg, admin).
- [ ] No module > 500 lines; no multi-resource view class.
- [ ] `repositories/` owns all ORM access; no `.objects` in views.
- [ ] Every endpoint has request + response serializers; no ORM/`.values()` leaks.
- [ ] One error envelope; no magic status strings.
- [ ] No app-to-app model reach-through; no import cycles; no inline-import hacks.
- [ ] Every app has tests; billing, webhooks, and the analysis pipeline covered.
- [ ] `manage.py test` green in CI on every PR (backend CI now exists).
