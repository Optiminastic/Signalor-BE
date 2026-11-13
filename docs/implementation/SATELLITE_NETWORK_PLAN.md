# Satellite network: a one-month plan per client

Status: plan. Owner decisions (13 Nov 2026):
- Client links carry `rel="sponsored"`.
- One article per client every 3 days (about 10 a month), then the work goes into getting those pages ranking on Google.

## What exists today (verified in code)

- signalor.ai owns 5 sites (`config/settings/base.py: SATELLITE_SITES`):
  - brightsfindings.com (research)
  - thepickpost.com (listicles)
  - trendledgers.com (market trends)
  - betterversus.com (comparisons)
  - guidefactories.com (step-by-step guides)
- `apps/analyzer/services/backlink_engine.py` writes one AI article per site per day per client (5 a day, about 150 a month per client), each with a plain followed link to the client.
- Posts are stored for the sites to render; both sites checked serve a `sitemap.xml`.
- Nothing submits these posts to Bing or Google, and nothing checks whether they get indexed.
- The Growth Agent's monthly plan (`services/planning/tactics.py`) has no satellite tactic.

## The problem with the current approach

- Google's spam policies name two things this matches:
  - **Link schemes**: sites run mainly to pass links to other sites.
  - **Scaled content abuse**: many pages generated mainly to manipulate rankings.
- Likely result: Google ignores or removes the 5 sites, so the links are worth nothing.
- Worse result: a manual action against the client sites for unnatural links.
- For a product that sells AI visibility, that risk lands on our clients and our name.

## What actually helps a client get suggested (GEO)

- AI answers are built from pages the engines have indexed and trust.
- A mention in a genuinely useful comparison, list or guide is what gets a brand named in an answer; whether the link passes PageRank matters much less.
- So the network is worth most as a small set of good editorial sites that AI engines read and cite, not as a link farm.

## Proposed one-month process (per client, inside the agent's monthly plan)

| Week | What the agent does | Owner sees |
|---|---|---|
| 1. Explore | Reads the client's tracked prompts, competitors and brand profile. Finds prompts where the client is missing from AI answers, and the search queries behind them. Picks 10 topics, each with one target query, and matches each to the best-fit site (a "best X tools" list on thepickpost, an "A vs B" on betterversus, a how-to on guidefactories). | A topic plan in Approvals: 10 articles, which site, which query each targets |
| 1-4. Publish | One article every 3 days, rotating across the 5 sites. Each covers several real options, with the client among them, written to rank for its target query (title, headings, meta description, Article schema). Each draft goes through the normal approval flow. | Each draft in Approvals, exactly as it will appear |
| After each post | Sends the new URL to Bing (IndexNow, key files hosted on our own 5 sites). Resubmits that site's sitemap in signalor.ai's own Search Console. Links the post from the site's home and category pages so crawlers find it. | Activity: "Published on betterversus.com, sent to Bing" |
| 2-4. Rank | For every published post: index status (URL Inspection on our 5 properties), then its Google position and clicks for the target query (Search Console performance data, which we have because we own the sites). Posts not indexed after 7 days get more internal links from the other satellite posts and hub pages. Posts indexed but below page 1 after 14 days get a refresh draft (fuller answer, updated facts, better title), again through approval. | Per post: indexed or not, position for its query, clicks; refresh drafts in Approvals |
| 2-4. Track AI answers | Re-runs the client's target prompts in the visibility tracker. | Prompts where the client now appears |
| End of month | Report: posts live, indexed on Google and Bing, prompts gained, and next month's topics. | A monthly summary in chat and Activity |

## Indexing the satellite posts

- We own the 5 sites, so the agent can do more than for client sites:
  - verify all 5 in signalor.ai's own Search Console;
  - resubmit their sitemaps through the API with our own credentials (no client consent needed);
  - inspect each new post's index status.
- Bing: host one IndexNow key file per site and submit each new post the day it goes live.
- Google still decides what it indexes; "Request indexing" stays a manual button (see `INDEXING_AGENT.md`).

## Content and link rules (proposed)

- One article per client every 3 days across the network (about 10 a month), not 5 a day.
- Each article names several options honestly; no article exists only to link one client.
- Client links carry `rel="sponsored"` (Google's rule for placed links). The mention and citation still count for AI answers.
- Each site has real About and editorial-policy pages, and placements are disclosed.
- No article is published without the owner's approval (existing approval flow).

## Open questions (need answers before building steps 3 and 4)

1. ~~Link type~~: decided, `rel="sponsored"`.
2. ~~Volume~~: decided, one article every 3 days per client.
3. Where is the satellite sites' own code? Key files, sitemaps and home-page links are changed there, not in this repo.
4. Who owns the Google account that should verify the 5 sites in Search Console?
5. What happens to the posts already published with followed links: leave, mark sponsored, or remove?

## Build order once decided

1. Satellite tactic in the monthly plan, plus the week-1 topic plan in Approvals.
2. Drafts per topic through the existing approval flow (the `publish_article` action kind), publishing via the existing engine.
3. After publish: IndexNow per satellite site and sitemap resubmission on our own properties.
4. Ranking: index status, Google position and clicks per post from our own Search Console; internal-link and refresh actions for posts that lag.
5. Tracking AI answers and the monthly report.
6. Switch the existing daily 5-per-client batch off once the new flow is live, and apply `rel="sponsored"` to new posts.
