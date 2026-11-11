# Phase 1 --- Control Foundation

**Status:** Planned; implementation not yet approved.\
**Repository:** `ranking-be-growth-main`\
**Audit baseline:** branch `feat/growth-agent-main`, commit `4f708c8`.

## 1. Objective

Build the minimum reliable control foundation required before Signalor
expands autonomous SEO/GEO agents. The system must prepare external
actions, obtain approval when required, execute the exact approved
version, prevent duplicate actions and maintain an auditable lifecycle.

This phase is not the implementation of the five domain agents or the
complete orchestrator.

## 2. Verified repository findings supplied by audit

1.  The executor can set a task to `awaiting_approval`
    (`services/execution/executor.py:110`), but no approval endpoint,
    chat tool or durable approval record was found.
2.  New programs currently default every channel to autonomous
    (`models.py:37`).
3.  Current channel behavior writes content and sends it in one step.
    Exact-content approval requires draft and execute to become separate
    steps.
4.  External sends/actions are not protected against repeats after a
    crash between provider success and local database update.
5.  Renaming task statuses would break the existing API contract.
    Existing names must be mapped and preserved.
6.  The audit was read-only; no code changes were made.

Claude must revalidate line numbers and current code before
implementation because the repository may have changed since the audit.

## 3. Scope

### 3.1 Existing lifecycle mapping

-   Inventory all current task statuses and transitions.
-   Identify all readers/writers and API/frontend consumers.
-   Preserve existing status names and response contracts.
-   Add only necessary compatible states or metadata.
-   Centralize transition validation.
-   Document migration and backward compatibility.

### 3.2 Durable approval manager

Support: - Approve. - Reject. - Edit and resubmit. - Revoke. - Expire
where configured. - Individual review. - Batch review with per-item
decisions. - Authorized actor and role validation. - Tenant and website
scoping. - Audit history.

Approval must bind to an immutable action version, including destination
and relevant execution parameters.

### 3.3 Draft/execute separation

Refactor existing channel pathways so preparation does not perform the
external action.

A prepared action should persist: - Tenant/account and website scope. -
Campaign/task reference where applicable. - Action type and
connector/account. - Destination/recipient/target. - Exact content or
payload version. - Payload hash or immutable version reference. -
Required scopes and risk classification. - Policy evaluation
reference. - Approval state. - Idempotency identity. - Execution state
and evidence.

The executor must load the approved persisted version, verify it is
unchanged and execute that exact version. It must not regenerate or
mutate the payload.

### 3.4 Default approval mode

-   New programs/channels default to approval-required.
-   Existing autonomous configurations must be inventoried.
-   Do not silently change active customers' behavior without a
    migration and communication/authorization strategy.
-   Re-enabling autonomy must require explicit authorization and remain
    bounded by policy, risk and campaign rules.
-   Non-overridable safety rules always apply.

### 3.5 Backend enforcement

Immediately before external execution, validate: - Authenticated
actor/service identity. - Tenant/account and website ownership. -
Current policy permission. - Approval requirement and approval
validity. - Approved payload version/hash. - Destination and connector
ownership. - Integration permissions and credential availability. -
Quotas and budgets where currently available. - Task/dependency state. -
Cancellation/revocation state. - Idempotency/execution record.

Do not rely on UI hiding, model prompts or a prior stale check.

### 3.6 Duplicate-action prevention

Design and implement effectively-once behavior: - Stable idempotency key
per immutable action version. - Durable external-action execution
record. - Unique/concurrency guard preventing duplicate dispatch. -
Persist attempt lifecycle and provider identifiers. - Provider-side
idempotency when supported. - Reconciliation after timeout or crash with
ambiguous outcome. - Explicit unknown/needs-reconciliation state. - No
blind retry of potentially successful non-idempotent actions.

Use an outbox/dispatch pattern if it fits the existing architecture and
closes the database-to-worker dispatch gap. Do not introduce it without
explaining the repository-specific design.

### 3.7 Batch approval

-   Batch is a review grouping, not a single all-powerful permission.
-   Each item is independently validated.
-   Each item receives a durable approval decision.
-   Report partial outcomes.
-   High-risk items remain individual approval.
-   A modified or newly added item is not covered by a prior batch
    decision.
-   Prevent race conditions between batch review, edits, revocation and
    execution.

### 3.8 Audit

Record who did what, when, to which tenant/site/action/version,
decision/reason, before/after version references, batch ID, policy
result and execution result. Protect audit history from ordinary
mutation.

## 4. Out of scope

-   Implementing the five SEO domain agents.
-   Replacing the complete planner/orchestrator.
-   Adding new external providers unless required to wrap an existing
    path and explicitly approved.
-   Building the full customer dashboard.
-   Replatforming the queue/database/framework.
-   Broad subscription/billing redesign.
-   Enabling autonomous execution for new agent domains.

## 5. Required repository investigation before plan approval

Claude must inspect: - `services/execution/executor.py` and all
callers. - `models.py` and all task/program/channel models. - All task
status definitions, serializers, API schemas and frontend consumers. -
Existing channel implementations and external side-effect locations. -
Gmail and GitHub action paths, if present. - Existing transaction
boundaries and worker retry behavior. - Database engine and migration
conventions. - Authentication, role and tenant/website scoping. -
Existing audit/event patterns. - Existing tests and fixtures. - Existing
autonomous configurations and their migration implications.

Report exact file paths and verified call chains.

## 6. Acceptance criteria

### Task lifecycle and compatibility

-   Existing status names remain compatible.
-   All state transitions are centrally validated.
-   Invalid transitions are rejected and audited.
-   Existing API and frontend consumers pass regression tests.

### Approval

-   Unauthorized users cannot approve or revoke.
-   Cross-tenant and cross-website approval attempts fail.
-   Approval binds to exact payload version and destination.
-   Any material edit invalidates the approval.
-   Rejected, expired or revoked actions cannot be dispatched.
-   Batch review validates every item independently and supports partial
    results.
-   Concurrent approve/edit/revoke/execute operations are safe and
    deterministic.

### Draft/execute

-   Draft creation has no external side effect.
-   The exact approved payload is what the provider receives.
-   Payload or destination mutation after approval is detected and
    blocked.
-   Approval cannot be reused for a materially different action.

### Defaults

-   New programs/channels default to approval-required.
-   Existing autonomous configurations are inventoried and migrated
    safely.
-   Migration does not silently authorize or revoke external actions.
-   Customer authorization and resulting mode are auditable.

### Idempotency and recovery

-   Duplicate worker delivery does not duplicate an external action
    where provider idempotency or reconciliation can prevent it.
-   Crash after provider success but before local persistence does not
    cause blind resend.
-   Ambiguous provider outcomes enter reconciliation rather than
    automatic retry.
-   Provider IDs and attempts are persisted.
-   Race and concurrency tests pass.

### Audit and security

-   Every approval, edit, rejection, revocation, dispatch and
    reconciliation is auditable.
-   Tenant and website isolation tests pass.
-   No secrets are exposed in logs.
-   Backend enforcement cannot be bypassed by calling executor paths
    directly.

### Quality

-   Unit, integration and regression tests pass.
-   Migration and rollback approach is documented.
-   API contracts and developer documentation are updated.
-   No out-of-scope agent implementation is introduced.

## 7. Required plan before coding

Claude must present: 1. Existing flow diagrams or precise call-chain
descriptions. 2. Current task status map and proposed
backward-compatible additions. 3. Proposed data model and migration. 4.
API and chat/tool contracts, if applicable to existing interface. 5.
Approval and batch concurrency semantics. 6. Draft/execute refactor
design. 7. Idempotency and ambiguous-outcome reconciliation design for
each external provider. 8. Existing autonomous configuration migration
plan. 9. Tenant/role authorization design. 10. Exact files/modules to
change. 11. Test matrix mapped to acceptance criteria. 12. Rollback and
deployment sequencing. 13. Risks, unresolved questions and any scope
changes.

Stop and wait for explicit user approval of this plan before changing
code.
