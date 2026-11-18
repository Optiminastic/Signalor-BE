# Signalor AI --- Implementation Roadmap

## Delivery principles

-   Extend the existing Signalor product; do not create a parallel
    platform by default.
-   Audit before building.
-   Preserve existing contracts and behavior.
-   Establish secure execution control before autonomous agents.
-   Deliver vertical slices with tests and documentation.
-   Keep phases independently reviewable.
-   Phase dependencies are gates; later phases must not bypass earlier
    controls.
-   Technology choices are made after repository and deployment
    assessment.

## Phase 0 --- Repository audit and architecture alignment

**Status:** Read-only audit reported complete.\
**Baseline:** `ranking-be-growth-main`, branch `feat/growth-agent-main`,
commit `4f708c8`.

Deliverables: - Repository responsibility map. - Verified current
architecture and data flows. - Existing scanning, customer, website,
auth, subscription, execution and integration inventory. - Reusable
components and gaps. - API compatibility map. - Tenant isolation
assessment. - Proposed integration boundaries. - Updated phase plan.

Exit criteria: - Production-authoritative repository and relevant
services identified. - Existing task lifecycle and external-action
pathways mapped. - Existing contracts and consumers identified. -
Architecture conflicts and risks documented. - Phase 1 implementation
plan reviewed and approved.

## Phase 1 --- Control foundation

**Goal:** Establish reliable, auditable control over external actions
before expanding agent autonomy.

Scope: - Map and preserve existing task status/API contracts. -
Centralize task state transitions. - Add durable approval records. -
Support individual and batch approval. - Support approve, reject, edit,
revoke and expiry semantics. - Bind approval to immutable action version
and destination. - Separate draft preparation from external execution. -
Change new program/channel defaults to approval-required. - Migrate
existing autonomous configurations safely and transparently. - Enforce
policy and approval server-side immediately before execution. - Prevent
duplicate external actions through idempotency and reconciliation. - Add
audit history and tenant-scoped authorization. - Add tests for race
conditions, worker crashes, partial batches and provider ambiguity.

Exit criteria: - No external action can execute without applicable
policy permission and required valid approval. - Approval is bound to
the exact immutable payload version. - Edited payloads require new
approval. - Batch approval creates individual decisions and reports
partial results. - Revocation blocks queued work and safely handles
in-flight work. - Duplicate/ambiguous provider outcomes are reconciled;
no blind retry. - Existing task status/API consumers remain
compatible. - New defaults require approval. - Existing configuration
migration is explicit, auditable and safe. - Cross-tenant and
unauthorized approval/action tests pass. - Unit, integration and
regression suites pass.

## Phase 2 --- Agent framework and connectors

Dependencies: Phase 1 complete.

Scope: - Agent interface/base contract. - Agent registry and
versioning. - Capability and task-type declarations. - Input/output
schemas. - Agent lifecycle, health and error contracts. - Connector
abstraction and credential references. - Wrap and reuse existing Gmail,
scraper and DataForSEO capabilities where verified. - Orchestrator
replaces the existing flat planner only after compatibility plan and
tests. - Agent selection, assignment and structured reporting. - Policy,
approval, quota and tenant controls remain enforced by shared services.

Exit criteria: - A sample agent can register, be discovered, receive a
typed task, report structured results and resume safely. - Missing
capabilities/integrations are handled explicitly. - Existing workflows
continue working. - No agent can bypass Phase 1 controls.

## Phase 3 --- Backlink acquisition vertical slice

Dependencies: Phases 1 and 2.

Scope: - Prospect and publisher discovery. - Contact discovery and
validation using approved providers. - Prospect qualification and
deduplication. - Placement opportunity and relationship tracking. -
Outreach draft creation and batch/per-item approval. - Email execution
through approved connector. - Reply handling and bounded negotiation. -
Follow-up scheduling. - Live-link verification and evidence. - Rejection
history and cooldown.

Exit criteria: - End-to-end campaign can discover, qualify, draft,
approve, send, wait, resume, verify and report. - Duplicate
prospect/contact/action protection works. - All external actions obey
policy and approval controls. - Outcomes and evidence are auditable.

## Phase 4 --- Remaining initial off-page agents

Dependencies: Phases 1 and 2; Phase 3 patterns may be reused.

Domains: - Guest Posting. - Digital PR & Brand Mention. - Community
Engagement. - Business Directory & Profile Submission.

Each domain must have a bounded workflow, capability contracts,
policies, required approvals, integrations, verification and acceptance
tests. Community participation must be authentic, platform-compliant and
use disclosed brand identity where applicable.

## Phase 5 --- Shared SEO/GEO capabilities and intelligence

Scope: - Search and SERP research. - Website/page analysis. - Keyword
and competitor research. - Publisher/contact enrichment. - Content
quality and relevance checks. - Backlink/mention verification. - Shared
intelligence provenance and freshness. - Customer-specific knowledge. -
Relationship/knowledge graph where justified by real query needs.

Exit criteria: - Agents reuse verified capabilities and research. -
Provenance, freshness, confidence and tenant boundaries are enforced. -
Stale/conflicting data triggers refresh or escalation.

## Phase 6 --- Orchestrator strategy and campaign management

Scope: - Intent understanding and context resolution. - Investigation
planning. - Goal decomposition. - Roadmap/campaign/task generation. -
Agent selection. - Multi-factor task evaluation. - Conflict resolution
and re-planning. - Strategy approval and negotiation. - Unified handling
of overlapping customer requests.

Exit criteria: - Natural-language goals produce evidence-backed,
reviewable plans. - Customer-approved scope governs execution. -
Strategy changes preserve completed work and commitments.

## Phase 7 --- Scheduler and persistent workflow infrastructure

Scope: - Durable task scheduling and dependencies. - Controlled
parallelism and fairness. - Event-driven triggers and scheduled
wakeups. - Checkpoint/resume. - Deadlines, expiry and retry budgets. -
Quota-aware resource allocation. - Duplicate-event protection and
missed-event recovery.

This phase may be pulled earlier if the existing execution framework
cannot safely support Phase 3. Any such sequencing change must be
documented and approved.

## Phase 8 --- Governance, team and commercial expansion

Scope: - Full layered policy configuration. - Website and campaign
rules. - Role-based website access. - Plan-aware entitlements and
metering. - Budget reservations and third-party cost reporting. - Human
intervention inbox. - Data retention/export/deletion workflows. -
Integration connection and permission UX.

Phase 1 includes the minimum controls required for safe execution; this
phase expands configuration and product experience.

## Phase 9 --- On-page and technical SEO agents

Scope: - Technical SEO analysis and remediation. - On-page
optimization. - Content strategy and optimization. - Keyword research. -
Internal links and schema. - CMS integrations and live-change approval.

Critical technical/indexability problems must be prioritized regardless
of the 65/35 strategic emphasis.

## Phase 10 --- Verification, analytics and learning

Scope: - Independent verification engine. - SEO/GEO and business outcome
measurement. - Baselines and forecasts. - Campaign attribution
caveats. - Agent evaluation and quality metrics. - Controlled
experiments. - Governed improvement proposals.

Some verification and analytics capabilities are required earlier for
Phase 1 and Phase 3; this phase expands them.

## Phase 11 --- Customer workspace

Scope: - Unified conversation. - Business profile and strategy. -
Campaigns, tasks and agent activity. - Approval inbox. - Evidence and
audit history. - Performance and costs. - Website portfolio and team
access.

Existing UI components should be reused where practical.

## Phase 12 --- Hardening and progressive rollout

Scope: - Security review and penetration testing. - Load, concurrency
and recovery tests. - Provider outage and disaster recovery tests. -
Cost and latency optimization. - Observability and operational
runbooks. - Staged rollout and rollback. - Customer beta cohorts and
support procedures.

## Phase governance

Before each phase: 1. Read this roadmap, global architecture and phase
spec. 2. Inspect current code and branch. 3. Produce a plan with files,
migrations, contracts, risks and tests. 4. Wait for explicit approval.

After each phase: 1. Run acceptance tests. 2. Report exact evidence. 3.
Update docs and roadmap status. 4. Identify follow-up work. 5. Do not
begin the next phase automatically unless explicitly authorized.
