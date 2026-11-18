# Google Tag Manager as a Growth Agent tool

Status: parked 16 Nov 2026 (decisions made: publish after approval; Organization schema first). Keyword data comes first.

## Goal

Let a brand connect its own Google Tag Manager container from the + panel in Agent mode.
The agent can then put structured data (schema) on the brand's site through GTM.
This covers brands with no WordPress or GitHub connected, which today get nothing on-site.

## Why it matters

- The monthly plan already contains an "Add Organization schema and sameAs links to your site" task (tactic `entity_schema`, channel `entity`).
- No executor exists for the `entity` channel, so that task is planned but never runs.
- GTM is on a large share of business sites and needs no developer once connected.

## What exists today (verified in code)

| Piece | Where | Reused for |
|---|---|---|
| Google OAuth with signed state and a return page | `services/outreach/connect.py` (Gmail) | GTM sign-in, same pattern |
| Encrypted tokens per brand | `apps.integrations.Integration` | GTM tokens and chosen container |
| `entity_schema` tactic | `services/planning/tactics.py` | The first GTM task |
| Approval of exact, versioned drafts | `GrowthAction` / `ActionVersion` | Every tag change |
| + panel with Gmail, Hunter, Apollo, Buffer | signalor-new `tools/` | A new "Site tags" group with GTM |

## The flow

| Step | Who | What happens |
|---|---|---|
| 1. Connect | Owner | Signs in with Google from the + panel and picks the account, container and website. |
| 2. Draft | Agent | Writes the Organization JSON-LD (name, URL, logo, sameAs social profiles) from the brand's verified profile. |
| 3. Approve | **Owner** | Sees the exact JSON-LD and where it goes, then approves or edits. |
| 4. Publish | Agent | In its own GTM workspace, creates or updates one tag named `Signalor - Organization schema` (fires on all pages), creates a version and publishes it. |
| 5. Check | Agent | Fetches the home page and reports whether the schema shows up after rendering. |

## Safety rules

- The agent only ever touches tags whose name starts with `Signalor - `; it never edits, pauses or deletes anything else.
- Changes go in a dedicated `Signalor` workspace, so the owner's unpublished work in other workspaces is never published by the agent.
- If the agent's workspace has a conflict, it stops and asks the owner; it never resolves conflicts on their behalf.
- Every publish is its own approved action; the version note says "Published by Signalor after owner approval".
- Disconnecting removes our tokens; the published tag stays until the owner removes it (stated in the UI).

## An honest limit

- JSON-LD added through GTM is injected by JavaScript.
- Google renders JavaScript and reads it.
- Many AI crawlers do not run JavaScript, so they may not see it.
- So when WordPress or GitHub is connected, the agent prefers putting schema directly in the page, and GTM is the fallback.
- The UI and the chat say this plainly.

## Access

- OAuth scopes: `tagmanager.readonly`, `tagmanager.edit.containers`, `tagmanager.publish`.
- These are sensitive (not restricted) scopes: production needs Google's app verification, without the security assessment Gmail needs.
- A new redirect URI must be registered per environment: `/api/growth/oauth/gtm/callback/`.

## Build steps (each ends in something visible)

| Step | Delivers | Visible as |
|---|---|---|
| 1 | GTM sign-in, container picker, status, disconnect | GTM row in the + panel showing the connected container |
| 2 | `entity` channel through GTM: draft, approve, publish, check | Organization schema draft in Approvals; published tag in the brand's GTM |

## Data

- No new tables: GTM is an `Integration` provider (`google_tag_manager`), with account, container, workspace ids in `metadata`.
- One choices-only migration for the new provider value.

## Out of scope

- Analytics, ads or conversion tags.
- Editing or removing tags the agent did not create.
- FAQ or article schema per page (a later step, once the first one is proven).
