# Indexing agent: weekly indexing work, like an SEO

Status: spec, awaiting go-ahead.
Owner decisions (13 Nov 2026): weekly and automatic once the owner turns it on; owners are asked once to give Search Console write access.

## Goal

Every week the agent does what an SEO does for indexing:
it checks which pages Google and Bing have indexed, finds out why the others are not, fixes what can be fixed, submits what changed, and shows progress.

## What may be automated (and what may not)

- Google has no public API for the Search Console "Request indexing" button.
- Google's Indexing API is only for job posting and livestream pages; using it for other pages goes against Google's guidelines, so the agent never uses it.
- Allowed and used:
  - URL Inspection API (read): each page's index verdict and reason. Quota 2,000 per day, 600 per minute, per property.
  - Sitemaps API (write, needs the `webmasters` scope): resubmit the sitemap after changes.
  - IndexNow: tell Bing (which ChatGPT search uses), Yandex, Seznam and Naver which URLs changed. Needs a key file hosted on the site.
  - Fixing the cause: noindex, robots.txt blocks, wrong canonicals, broken links. On sites connected through the GitHub App this is a pull request the owner merges.
- The agent never reports "submitted" as "indexed".

## Reused, not rebuilt

- `apps/integrations/services/gsc.py`: `inspect_sitemap_index_status`, `fetch_gsc_sitemaps`, `inspect_gsc_url`.
- `apps/integrations/models.GSCIndexStatusSnapshot`: per-page verdicts, already written by the index sync task.
- `apps/analyzer/services/indexnow.py`: key file check and submission.
- `apps/github_agent`: fix pull requests for robots, canonical and schema.
- Growth Agent Activity log (`AgentActionLog`) and the weekly `run_growth_tasks` scheduler.

## The weekly cycle (per brand)

1. Check: run the index sync if the latest snapshot is older than 7 days.
2. Compare with last week: newly indexed, newly dropped, still not indexed, grouped by Google's reason.
3. Submit (only if the owner turned on auto-submit): changed and new sitemap URLs to IndexNow; resubmit the sitemap to Google after fixes.
4. Fix: fixable reasons become tasks; with the GitHub App connected, a fix pull request.
5. Report: one Activity entry and one short chat summary, for example "41 of 52 pages indexed (+3). 5 crawled but not indexed, 4 blocked by noindex: fix PR opened."

## Approval policy

- Submissions (IndexNow, sitemap resubmit) publish nothing on the site.
  The owner turns auto-submit on once; that decision is recorded with who and when, and can be turned off at any time.
  Each weekly batch is logged.
- Site changes always need the owner: fix pull requests are merged by the owner, never by the agent.
- This is a standing approval for a narrow, non-publishing action, recorded as an exception to "approval per action" in `PHASE_1_DECISIONS.md`.

## Slices (each ends in something visible)

| Slice | Visible result | Backend |
|---|---|---|
| 1. Indexing card | An Indexing tab in Agent mode: indexed vs not, reasons in plain words, trend; "Connect Search Console" when missing | `GET programs/<slug>/indexing/` from the snapshots; weekly sync in `run_growth_tasks`; Activity entry |
| 2. Bing auto-submit | Toggle "Submit changed pages every week"; key file setup steps; weekly "Submitted 12 pages to Bing" | Program setting, diff against last submission, IndexNow submit, audit |
| 3. Google sitemap | "Reconnect Search Console" prompt for write access; weekly "Sitemap resubmitted" | Scope upgrade, `sitemaps.submit` |
| 4. Fix tasks | Each reason with what it means and the fix; fix PR links | Reason to action mapping; GitHub App PRs for noindex, robots, canonical |
| 5. Weekly summary | One chat message per week with the week's numbers | Summary from the snapshot diff |

## Risks and unknowns

- Google may require app re-verification for the `webmasters` (write) scope; to be checked in Google Cloud before slice 3 ships to production.
- The Google Cloud consent screen needs the new scope added by hand (as for the existing Search Console setup).
- Bing submission needs the key file on the site; without the GitHub App the owner uploads it themselves.
- URL Inspection is slow (about 8 seconds per page); large sites are capped per week and rotate through pages.
- Local testing needs Search Console connected for the local brand.

## Tests

- Authorization: another brand's owner gets 404 on every new endpoint.
- Weekly job: runs once per 7 days per brand, skips brands without Search Console, never submits without the opt-in.
- Submissions: only changed URLs, only on the brand's own host, never claimed as indexed.
- Fix tasks: one per reason group, no duplicates week to week.
