# Brand endpoint observation: alerts and response (SEC-1a-0)

## What this is

`PATCH` and `DELETE /api/organizations/<id>/` do not require sign-in yet.
Before sign-in is enforced (SEC-1a), the endpoint is observed for 7 days per environment.
During that time anyone can still rename or delete any brand.
The owner of a brand can no longer be changed through this endpoint by anyone.

Code: `apps/organizations/observation.py`.

## What is recorded

One info-level line per request, event `org_detail_observation`, with these fields only:

- `method`: `PATCH` or `DELETE`.
- `caller`: `anonymous`, `invalid_token` (a header was sent but did not verify) or `verified`.
- `would_be_authorized`: `yes`, `no`, `n/a` (the brand does not exist) or `error`.
- `owner_change_attempted`: `yes` or `no`.
- `origin_host`: the host of the `Origin` header, empty, or `invalid`.
- `status`: the response status code.
- `org_id`.

Never recorded: the token, any email address, the request body, the brand name or URL.

## Who would be authorized

A valid token is identity, not authorization.

| Caller | Rename | Delete |
|---|---|---|
| No token, or a token that does not verify | No | No |
| Signed-in owner of the brand | Yes | Yes |
| Signed-in admin of the agency that owns the brand | Yes | No |
| Anyone else signed in, including agency members | No | No |

## When an alert fires

An error-level line, event `org_detail_unauthorized_attempt`, with the same fields plus `attempt`.

| Request | `attempt` |
|---|---|
| A delete the caller would not be authorized for | `delete` |
| A request that tries to change `owner_email`, from anyone | `owner_change` |
| A rename by a signed-in caller who would not be authorized | `cross_account_edit` |

A rename with no valid token does not alert.
It is the legacy traffic the observation exists to count.

## How an alert reaches a person

- Primary: Sentry, which receives error-level logs when `SENTRY_DSN` is set.
- Fallback: a direct email to `ORG_ENDPOINT_ALERT_EMAILS`, at most one per brand and attempt every 10 minutes.

Every alert is always written to the log and to Sentry, whether or not the email is sent.

## Deploy gate (mandatory, per environment)

Do not deploy SEC-1a-0 to an environment until every line is true for it.
A daily manual log review does not satisfy this gate.

| # | Requirement | Evidence |
|---|---|---|
| AL1 | A named person receives alerts, and a named cover | Two names |
| AL2 | `SENTRY_DSN` is set and Sentry notifies that person | A Sentry test event sent before deploy, receipt confirmed |
| AL3 | The fallback email reaches them | `python manage.py sendtestemail <address>` run in that environment before deploy, receipt confirmed |
| AL4 | `ORG_ENDPOINT_ALERT_EMAILS` is set to those addresses | Present in the environment |
| AL5 | The named people have read this runbook | Confirmed |
| AL6 | End to end, right after deploy: create a throwaway test brand and delete it with no token | Both the Sentry notification and the email arrive within 15 minutes |
| AL7 | The channels still work mid-window | AL6 repeated on day 4 |

If AL6 fails, roll the deploy back.
The observation window does not start until AL6 passes.

Also confirm once, during AL2, that the Sentry event does not contain the `Authorization` header.
The backend sets `send_default_pii=True`, so this must be checked rather than assumed.

## Responding to an alert

1. Acknowledge the same day, within 4 working hours.
2. Read the fields: which brand, which attempt, what kind of caller, which origin.
3. Decide which case it is.
   - Legacy traffic: a tokenless delete from one of our own frontends by the brand's real owner. Confirm with the owner.
   - Suspicious: anything from a signed-in caller who would not be authorized, any owner-change attempt, any origin that is not ours, any repetition.
4. If suspicious, tell the brand's owner, check whether the brand still exists, and restore it from backup if it was deleted.
5. If it repeats or looks deliberate, escalate with a recommendation to enforce SEC-1a at once in that environment, or to block the route at the proxy until it is enforced.
6. Record every alert and what was done about it.

The list of alerts and their handling is part of the observation's evidence.

## Evidence at the end of the window

A table of counts by `(origin_host, caller, would_be_authorized, method)`.
The window passes when every row with real traffic shows `would_be_authorized=yes`.
Any other row is a caller to identify and fix before SEC-1a is enforced, or to accept in writing.

## Once sign-in is enforced (SEC-1a)

SEC-1a is a separate change, deployed to an environment only after that environment's observation has passed.

After it is deployed:

- A caller with no verified token gets `401`, whatever the brand id.
- A signed-in caller who may not act on the brand gets `404`, the same answer as for a brand that does not exist.
- Only `name` and `url` can be edited. Any other key in the body is rejected with `400`.
- With `BETTER_AUTH_JWKS_URL` unset, both methods return `503` and nothing can be edited or deleted.

The observation line and the alerts stay on.
The `status` field now shows that an attempt was refused (`401` or `404`), so an alert means "someone tried", not "something was changed".
An unverified caller is refused before the body is read, so an `owner_email` sent without a valid token is not reported as an owner-change attempt.

Watch the count of `status=401` by `origin_host` for the first 7 days.
A legitimate caller that still sends no token shows up there, and is fixed by making it send one.
Rolling the enforcement back is not the remedy for a broken caller, because it reopens the endpoint.

Changing the owner of an existing brand has no application or admin path until the audited transfer view (SEC-1b) exists.
Until then it needs a database administrator and a written change record: who, why, when, and digests of the old and new owner.

## Limits

- This change does not stop renames or deletes. It only stops ownership changes, and makes the rest visible.
- With `BETTER_AUTH_JWKS_URL` unset, no token can be verified, so every caller is `anonymous` or `invalid_token`.
- The tests show the classification and the alert content. They do not show that an alert reaches a person. Gate AL1 to AL7 does that.
