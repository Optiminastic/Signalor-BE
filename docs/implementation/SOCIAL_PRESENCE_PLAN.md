# Social presence: score the brand's marketing, then help it grow

Status: spec, awaiting go-ahead (13 Nov 2026).

## Goal

Show each brand how strong its marketing is on LinkedIn, X, YouTube, Instagram and Reddit, as a score per platform and overall.
Then help it grow: the agent drafts posts, the owner approves them, and they are scheduled through the brand's own Buffer account.

## What exists today (verified in code)

- `apps/analyzer/pipeline/social_presence.py`: finds the brand's profiles (links on its own site, then web mentions, then a Serper search) and tries to read follower counts by fetching the profile pages.
  - Results are stored on the analysis run (`social_presence_details`).
  - Reading follower counts from LinkedIn, Instagram and X pages is unreliable: those sites block or hide it for logged-out visitors.
- The dashboard has a Reddit roadmap page (`features/catalyst/components/socials/RedditRoadmapView.tsx`).
- The Growth Agent has an `owned_social` channel in its allowlist but nothing runs it yet.
- Sign-in to outside tools already exists: Hunter and Apollo connect through their MCP servers with OAuth (`contact_finders/oauth.py`, `mcp_client.py`).

## Where the numbers come from (honest about each)

| Platform | Without Buffer | With the brand's Buffer connected |
|---|---|---|
| LinkedIn | Profile found or not; follower count only when the page shows it | Followers, posts, engagement from Buffer insights |
| X | Profile found or not; follower count rarely visible | Same, from Buffer |
| Instagram | Profile found or not; follower count rarely visible | Same, from Buffer (business accounts) |
| YouTube | Exact subscribers, videos, views and last upload from the YouTube Data API (public data) | Also Buffer insights if the channel is in Buffer |
| Reddit | Brand mentions in the last 90 days and in which communities, from Reddit search (public data) | Not a Buffer network; stays public data |

- Where a number can't be read, the score says "not measured" for that part; it never guesses.

## The score

Per platform, 0 to 100:

| Part | Weight | Measured by |
|---|---|---|
| Presence | 20 | A profile exists and the brand's site links to it |
| Audience | 30 | Followers or subscribers, on a log scale |
| Activity | 30 | Posts in the last 30 days |
| Engagement | 20 | Likes, comments and shares per post (Buffer insights) |

- Reddit is scored on mentions instead: how often and in how many communities the brand comes up, in the last 90 days.
- Overall score: the average over all five platforms (a platform the brand isn't on counts as 0), with "partial" shown when parts aren't measured.
- Parts that can't be measured score 0 and are listed as "not measured", so the score is never padded with guesses.
- Recomputed weekly; the change since last week is shown.

## Connecting Buffer (checked 13 Nov 2026)

- Buffer runs a hosted MCP server at `https://mcp.buffer.com/mcp` with OAuth: self-registration, PKCE, refresh tokens (`offline_access`), and scopes including `posts:write`, `channels:write` and `insights:read`.
- This is the same pattern as Hunter and Apollo, so the sign-in code is reused: the owner clicks "Connect Buffer", signs in on Buffer's own page, and the token is stored encrypted on the brand.
- Only scopes needed: `posts:read`, `posts:write`, `account:read`, `insights:read`, `offline_access`.
- Alternatives if Buffer won't do: Ayrshare (built for SaaS, paid per profile). Hootsuite's API is licensed for a company's own accounts, not for acting for customers.

## Helping the brand grow

- Each week the agent drafts posts per connected channel from what the brand already has: its pages, its articles on signalor.ai sites, and its plan topics.
- Drafts appear in the Social tab; the owner edits, approves or rejects each one. Nothing is posted without approval.
- Approved posts are scheduled in the brand's Buffer queue (Buffer's `create_post`), never posted directly by Signalor.
- Reddit is never posted to automatically (it gets brand accounts banned); for Reddit the agent shows which communities mention the brand or its topics, as a list to engage with by hand.

## Owner-facing: a Social tab

- Its own tab in Agent mode (as with Articles), with a count when a post draft waits.
- Sections: the overall score and per-platform scores with what each is based on; "Connect Buffer"; post drafts to review; scheduled and recent posts with their results; Reddit communities talking about the brand.

## Slices (each ends in something visible)

| Slice | Visible result |
|---|---|
| 1. Social score | Social tab with the score per platform and overall, from today's data plus YouTube and Reddit public data |
| 2. Connect Buffer | "Connect Buffer" in the Social tab; once connected, exact followers and engagement replace the estimates |
| 3. Post drafts | Weekly post drafts per channel in the Social tab, with edit, approve and reject |
| 4. Scheduling | Approved posts go to the brand's Buffer queue; their results come back into the tab and the weekly summary |

## Needed before building

- A YouTube Data API key (Google Cloud, free quota) and a Reddit API app (free for low volume), both as server settings.
- Buffer: confirm with a real sign-in that its MCP server accepts our registration (the metadata says it does).

## Risks

- Follower counts without Buffer stay partial for LinkedIn, X and Instagram; the tab says so instead of showing made-up numbers.
- Buffer's MCP tool names aren't published; as with Apollo, the agent reads the tool list after sign-in.
- Platform rules change; posting always goes through Buffer, which keeps up with them.
