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

## Resolving an unknown outcome (done)

- `POST programs/<slug>/actions/<id>/resolve/` with `{resolution, version, payload_hash, reason?}`.
- Only from `needs_reconciliation`, only by the owner.
- `confirmed_sent`: the action succeeds and the task is done. It is counted against the daily cap and budget like any send.
- `cancel`: the action becomes `cancelled` ("closed, outcome unknown") and the task is skipped.
- "Send it again" is not offered. A resend after an unknown outcome needs its own decision first.
- Each resolution is an immutable `ApprovalDecision` (`resolved_sent` / `resolved_cancelled`) and an audit entry. Migration `0010`.

## Editing a draft (done)

- `POST programs/<slug>/actions/<id>/edit/` with `{version, payload_hash, fields}`.
- An edit creates the next `ActionVersion` with the same destination. The action goes back to `awaiting_approval`; an approval of an earlier version stops counting.
- Editable from `awaiting_approval`, `approved` and `rejected`.
- Outreach email: subject and message. The footer (sender line and opt-out) is kept and re-attached by the server.
- Article: title, meta description, slug and opening answer. Sections and FAQ are not editable yet.
- Recipient and destination are never editable. Validation is deterministic. No migration.

## Postgres tests (done)

- `config/settings/test_postgres.py` runs the tests on a real Postgres with real migrations. It reads only `TEST_DATABASE_URL` and refuses any host that is not local or a CI service.
- `tests/test_concurrency_pg.py` releases two operations on one action at the same moment: identical approvals, approve against edit, two sends, reject against send, two different resolutions. It is skipped on SQLite.
- Removing the action row lock makes every one of those tests fail, including a duplicate send.
- CI job `postgres-suites` runs the whole Growth Agent suite this way. It runs as a single database role; the role split comes with the triggers.

Run locally against the Docker stack's Postgres (Django creates and drops its own `test_<name>` database):

    TEST_DATABASE_URL=postgres://<user>:<password>@localhost:55432/signalor         python manage.py test apps.growth_agent.tests.<module> --settings=config.settings.test_postgres

## Expiry and revoke (done)

- An approval counts for 14 days (`GROWTH_APPROVAL_TTL_DAYS`). After that the action becomes `expired` and is never sent.
- The sender checks expiry itself before every send, so an expired approval can't go out even if the sweep doesn't run. `run_growth_tasks` also expires them each round.
- An expiry is recorded as an immutable decision with the system as the actor (`actor_role="system"`, empty actor email).
- `POST programs/<slug>/actions/<id>/revoke/` withdraws an approval before sending starts. The action becomes `revoked`; the task is skipped.
- Revoking while a send is in progress needs the claim, final check and result protocol, and comes with it.
- Responses carry an additive `approval_expires_at`. Migration `0011` changes only choices and blank-ness (no SQL).

## Send protocol, part 1: trail, final check, crash recovery (done)

- Every attempt writes an append-only `ActionExecutionEvent` trail: `claimed`, then `provider_started` (committed immediately before the provider is called), then `provider_result` (succeeded / failed / unknown). An attempt stopped before any outside call ends `aborted`. Migration `0012`.
- At most one claim, one provider start and one result per attempt (database constraint).
- A final check runs under the action lock right before the call: still dispatching (not withdrawn), approval valid and unexpired, content intact, guardrails allow. If anything changed, the attempt is aborted and nothing is sent.
- `services/control/recovery.py` (run by the scheduler before sending): claimed but never started for 5 minutes goes back to approved; started with no result for 15 minutes becomes `needs_reconciliation` and is never resent.
- Revoke works while an action is claimed but the call hasn't started. Once the provider call has started it is refused with 409 `in_flight`.
- Not yet: the full canonical lock set (organization to action) and the complete preflight list (P1 to P14).

## Send protocol, part 2: the attempt ledger (done)

- `DispatchAttemptLedger` (append-only, migration `0013`): before any attempt an `attempt` ticket is written; if it can't be (number taken by another worker, database trouble), the attempt doesn't happen.
- Each ticket gets a `result`: `provider_started`, `preflight_wait` (pause, kill switch, cap), `closed`, `lock_conflict` (Postgres 40P01 / 55P03) or `error`. A ticket with no result counts as a failure.
- `seq` only goes up and is never reused. The consecutive-failure count is derived: attempts after the newest resetting result or unblock.
- One failure or more: a wait of 1, 2, 4... minutes (capped at 60) before the next ticket. Ten in a row: blocked.
- `manage.py growth_unblock_dispatch --action <id> --admin-username <superuser> --reason "..."` authenticates a superuser by password in an interactive terminal and records the unblock against them.
- Deleting a brand removes its trail and ledger rows.

## Follow-up actions (done)

- When a follow-up is due on an open thread (4 days after the first email, then 5), the scheduler drafts it as a `send_follow_up` action linked to the thread and its step. A database constraint allows one action per step. Migration `0014`.
- The owner approves that exact follow-up; it can be edited like any email (the footer stays fixed). Its approval lasts 3 days (`GROWTH_FOLLOW_UP_APPROVAL_TTL_DAYS`).
- Final check before sending: the thread is still open, the contact hasn't opted out, no other email was sent in the thread since the draft, and the step is next in order.
- Just before sending, the Gmail thread is read live. If the contact replied, nothing is sent and the reply (or opt-out) is recorded.
- A reply recorded at any time stops pending follow-ups (state `stopped`, recorded as a system decision).
- A rejected, expired or stopped follow-up ends the sequence: the next step is drafted only after the previous one is sent.
- Follow-ups never change the original task's status (`actions.updates_task`). The plan's `task.action` stays the original action.

Not done yet:

- Batches.
- The full canonical lock set (organization to action), the complete preflight list (P1 to P14), and provider reconciliation.
- Database triggers and the role split. Immutability is enforced in the model and queryset only for now.
- Follow-up actions, and the per-brand scheduling for 10 brands.
