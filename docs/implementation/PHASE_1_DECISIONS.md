# Phase 1 - Control Foundation: decisions

These decisions were made during the Phase 1 review rounds.
They are frozen.
`PHASE_1_CONTROL_FOUNDATION.md` is the original phase specification; where the two differ, this file is the later decision.

## Approval and actions

- Task status values are unchanged: `planned`, `ready`, `awaiting_approval`, `executing`, `done`, `failed`, `skipped`.
- The precise state of an external action is exposed on an additive, read-only `task.action` object.
- Every external action needs the brand owner's individual approval.
- Batches group actions for review. Bulk reject is allowed. Bulk approve is not.
- Every follow-up email is a separate action with its own approval.
- Approval is bound to an exact, immutable version of the content and destination. An edit creates a new version with no approval.
- Only the brand owner may approve, reject, edit or revoke. Agency admins come later.
- Existing `channel_modes` values are preserved. A legacy `autonomous` value never bypasses individual approval.
- No chat tool may approve, reject or confirm anything.

## Sending safely

- Preparation and sending are separate steps. The sender uses only the stored, approved version.
- Each send is claimed first, then passes a final check immediately before the provider is called.
- That final check re-runs every safety condition under the same row locks that revocation and other writers take.
- An unknown outcome is never retried automatically. It goes to `needs_reconciliation` for a person to resolve.
- For Gmail, "not found in search" is not proof that an email was not sent.
- For WordPress, a marker in the content identifies our post. A create is attempted only after a reliable "not present" check.
- Attempts are recorded in an append-only ledger before they run. An attempt that cannot be recorded does not happen.

## Evidence and audit

- Versions, decisions, batch items, execution events, safety changes, reviews and the audit log are append-only.
- That is enforced in the model, in the queryset, and by database triggers.
- The running application connects as `signalor_app`, which does not own the monitored tables and cannot disable their triggers.
- Migrations run as `signalor_owner`.

## Safety changes

- Changes to ownership and to a connector's identity are recorded by database triggers, whatever code path made them.
- A high-risk change from an unknown source blocks dispatch and approval for that brand until it is reviewed.
- The brand owner may confirm a change to their own connector (site, repository, mailbox) with a one-time emailed code.
- Ownership changes and brand moves are reviewed by a superuser with TOTP.

## Deleting a brand

- Deletion returns `409 Conflict` while any action is in flight or awaiting reconciliation.
- It proceeds only after each execution is finalized or actually reconciled.
- Deleting a brand erases all of its agent records.

## Running 10 brands

- The agent gets two queues of its own, `growth.interactive` and `growth.background`, with two small workers.
- The scheduler enqueues one job per brand per round. Each brand's work is done in order, with a fair share per round.
- A heartbeat alert fires when a brand has had no round for 45 minutes.
- At most 10 programs may be active until the load test is rerun at a higher number.

## Release

- No migration runs against a database whose target has not been verified with `check_db_target`, after a snapshot.
- A full deployment rehearsal on a production-like copy is required before production.
- The Postgres test suites run in CI and must be green before production.
- The three minimal customer screens (approvals, needs attention, connector change) ship with Phase 1.
- Sign-in and ownership enforcement on the brand edit and delete endpoint (SEC-1a) must be live first.

## Step 1 (done)

- `check_db_target`: read-only database target and role check.
- `growth_inventory_modes`: read-only inventory of existing programs.
- `deploy/sql/phase1_roles.sql`: role setup script. Written, not run.

## Approval slice (done)

The smallest complete flow, for the two existing channels (owned content and outreach email).

- A due task is prepared into a `GrowthAction` with an immutable `ActionVersion` and a content hash. Nothing is sent.
- The task shows `awaiting_approval`. The plan response carries an additive `action` object on each task.
- The brand owner approves or rejects that exact version: `POST programs/<slug>/actions/<id>/approve/` or `/reject/` with `{version, payload_hash}`.
- `GET programs/<slug>/approvals/` lists pending drafts. `GET programs/<slug>/actions/<id>/` shows one with its decisions.
- Each decision is an immutable `ApprovalDecision` and an audit entry that names who decided.
- Approving sends nothing. The scheduler sends approved actions through `services/control/dispatch.py`.
- Before sending, the dispatcher checks the decision record and recomputes the content hash. A changed draft is refused.
- A send whose outcome is not certain becomes `needs_reconciliation` and is never retried automatically.
- A stored `autonomous` channel mode no longer skips approval. `dry_run` still only drafts.
- Automatic follow-up emails are switched off. A follow-up will be its own approved action.

Not in this slice yet:

- Editing a draft, revoking, expiry and batches.
- Resolving a `needs_reconciliation` item (there is no endpoint for it yet).
- The claim, final check and result protocol with full lock ordering, the attempt ledger, and provider reconciliation.
- Database triggers and the role split. Immutability is enforced in the model and queryset only for now.
- Follow-up actions, and the per-brand scheduling for 10 brands.
