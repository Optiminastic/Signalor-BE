# Signalor AI --- Governance, Approval and Execution Specification

## 1. Policy precedence

When policies conflict, apply this precedence:

1.  Non-overridable system safety rules.
2.  Applicable legal and platform restrictions.
3.  Customer account rules.
4.  Website-specific rules.
5.  Campaign-level execution policies.
6.  Agent operational preferences and recommendations.

Higher-priority rules win. Record the conflict, applicable policies and
resolution. Customer approval cannot override a prohibited action.

## 2. Policy layers

System policies define global non-negotiable constraints.

Customer policies define brand voice, outreach volume, allowed channels,
prohibited activities, risk tolerance, negotiation boundaries, budget
limits and approval preferences.

Website policies define property-specific restrictions, target pages,
allowed changes and connected CMS access.

Campaign policies define scope, goals, budget, target lists, content
guidelines, channels, timing and authorized activities.

Agent constraints define only operational choices inside all
higher-level rules.

Policy changes must trigger reevaluation of queued and active work: -
Stop queued actions that now violate policy. - Pause in-progress actions
where safe. - Reevaluate follow-ups and scheduled actions. - Preserve
completed history and commitments. - Recalculate affected
dependencies. - Notify authorized users of material impacts. - Resume
only when compliant and authorized.

## 3. Approval defaults and risk levels

Default external-action mode is approval-required.

A governed risk model may permit low-risk actions to execute within an
explicitly approved campaign policy. Medium-risk actions may be prepared
for batch review. High-risk actions require explicit individual
approval. The precise risk taxonomy and thresholds must be configurable
and documented.

Examples of external actions: - Sending email or messages. - Publishing
or submitting content. - Posting to communities. - Editing a live
website or CMS. - Purchasing placements or services. - Making
commitments, negotiating prices or spending money.

An action must not execute unless: - Its actor and tenant context are
authorized. - All applicable policies permit it. - Required integration
permissions exist. - Required approval is valid for the exact action
version. - Quotas, budget and concurrency allow it. - Dependencies are
satisfied. - The action has a durable execution record and idempotency
strategy.

## 4. Approval record model

Each approval decision must be durable and auditable. The logical record
should include: - Approval ID. - Tenant/account and website scope. -
Campaign/task/action reference. - Action type and destination. - Exact
payload version or immutable payload hash. - Approval status and
decision. - Approver actor and role. - Decision timestamp. - Optional
reason or comment. - Batch reference, if any. - Policy/risk snapshot or
reference. - Expiry, if applicable. - Revocation record and reason. -
Audit correlation ID.

Do not store only a mutable pointer to a draft. Approval must bind to
the exact content and execution parameters reviewed.

## 5. Batch plus per-item approval

Support both batch and individual review.

-   A batch groups eligible actions for convenience.
-   Approve/reject batch operations must validate every item
    independently.
-   Each item gets its own durable decision record.
-   Items may be individually edited, approved or rejected.
-   A batch result may be partially successful; report per-item
    outcomes.
-   Batch approval cannot override item-level policy restrictions.
-   High-risk actions require individual approval even if grouped in a
    batch.
-   Batch membership and contents must be immutable for the decision or
    versioned.
-   Items added or materially changed after review are not covered by an
    earlier batch approval.

## 6. Approval invalidation and revocation

-   Any material edit to content, recipient, destination, account, URL,
    amount, timing or execution parameters invalidates prior approval.
-   The edited version returns to the appropriate review state.
-   Rejected actions cannot execute unless a new version is created and
    separately reviewed under policy.
-   Revocation must block queued execution.
-   For in-flight actions, attempt cancellation if supported; otherwise
    reconcile provider state and report honestly.
-   Revocation cannot undo an external action already completed.
-   Keep prior approvals and revocations in history; never erase the
    audit trail.

## 7. Draft versus execute separation

External action workflows must separate preparation from execution.

Example: 1. Agent researches recipient and context. 2. Agent creates a
draft with recipient, subject, body, sender/account and relevant
metadata. 3. Quality and policy checks run. 4. Draft is presented for
approval. 5. Approval binds to an immutable version. 6. Executor reloads
the approved version and validates its hash and policy. 7. Executor
performs the external action. 8. Provider result is stored. 9.
Independent verification/reconciliation confirms outcome.

The executor must not regenerate or silently modify approved content. If
any material parameter changes, require new approval.

## 8. Task lifecycle and backward compatibility

Existing repository task status names are part of the current API
contract. Do not rename or reinterpret them without an explicit,
versioned migration plan.

Claude's repository audit reported an existing `awaiting_approval`
status. Preserve existing names and add only necessary
states/transitions after mapping all current states and consumers.

Define a centralized state machine with: - Allowed transitions. -
Actor/permission requirements. - Preconditions. - Side effects. - Audit
events. - Retry and cancellation semantics.

Logical lifecycle concepts include: - Created/planned. - Ready/queued. -
Running. - Awaiting approval. - Approved/authorized. - Waiting for
external event. - Retryable failure. - Failed. - Partially successful. -
Completed. - Verified. - Rejected. - Cancelled. - Expired. - Needs
reconciliation / unknown external outcome.

These are conceptual states, not a command to add every label verbatim.
Map them compatibly to existing API and persistence contracts.

## 9. Idempotency and duplicate external-action prevention

Exactly-once execution cannot be assumed across a database and an
external provider.

Implement effectively-once behavior through: - Stable idempotency keys
derived from a durable action identity and immutable version. - Unique
constraints or equivalent concurrency protection for action dispatch. -
Durable execution attempt records. - Atomic internal state transitions
where possible. - Transactional outbox/dispatch pattern where
appropriate. - Provider-side idempotency when available. -
Provider-state reconciliation after ambiguous timeout/crash. - Explicit
`unknown/needs_reconciliation` handling. - Safe retry only after
determining whether the prior attempt took effect.

A worker crash after provider success but before local persistence must
not cause blind resend. If provider APIs cannot reliably establish
outcome, escalate or use a conservative policy rather than duplicate an
action.

## 10. Failure and recovery

Classify errors: - Transient provider/network failure. - Rate limit. -
Invalid credentials or revoked permissions. - Policy denial. - Approval
missing/expired/revoked. - Quota/budget exhaustion. - Permanent
validation error. - External outcome unknown. - Human decision required.

Use bounded retries with backoff for transient errors. Preserve state
and evidence. Alternative providers or strategies must be approved and
compatible with permissions, cost, data handling and policy. Never retry
indefinitely.

## 11. Quotas, budgets and plan limits

-   Check plan entitlements before task assignment and before metered
    execution.
-   Reserve/consume usage safely to avoid concurrent overspend.
-   Track Signalor subscription quota, third-party API cost and campaign
    external spend separately.
-   Pause only tasks dependent on an exhausted quota.
-   Continue eligible independent tasks.
-   Preserve progress and resume after reset/upgrade.
-   Never incur unapproved overages.
-   Explain blocked tasks and upgrade requirements.

## 12. Integration permission handling

If an action needs a missing integration or permission: - Identify the
exact provider, scope and task dependency. - Explain why it is needed. -
Request explicit connection/authorization. - Keep the task resumable. -
Continue unrelated eligible work. - Never borrow another tenant's
connection or silently broaden scopes. - Resume only after validating
connection, scope and ownership.

## 13. Audit and human intervention

Record policy evaluations, approval decisions, edits, revocations, task
transitions, external attempts, provider IDs, retries, reconciliation,
cost and verification.

Human intervention requests include: - Context and objective. - Evidence
and confidence. - Recommended options. - Risks and estimated costs. -
Required permission or decision. - Deadline or consequence of waiting.

Authorized roles may approve, reject, edit, provide information or take
over. Resume from persisted state after intervention.

## 14. Security rules

-   Enforce all checks server-side.
-   Resolve identity and tenant scope from authenticated context.
-   Do not trust tenant IDs, actor IDs, approval flags or action
    payloads supplied by the model or client.
-   Use least privilege and credential isolation.
-   Protect against replay, race conditions and
    time-of-check/time-of-use gaps.
-   Revalidate policy, approval and payload immediately before external
    execution.
-   Record immutable evidence of the approved and executed version.
