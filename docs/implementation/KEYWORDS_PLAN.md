# Keyword data for the Growth Agent

Status: approved 16 Nov 2026 (top 3 competitors; all 3 steps in order). Step 1 built.

## Goal

Give the agent real search data, so it picks topics people actually search for and the owner can see which keywords the brand wins and misses.
No customer connection is needed: the data comes from signalor.ai's own DataForSEO account, which is already configured.

## What exists today (verified in code)

| Piece | Where | Reused for |
|---|---|---|
| Search volume per keyword (Google Ads data, the same numbers as Keyword Planner) | `apps/integrations/services/dataforseo.py: fetch_search_volume` | Volumes for the agent's topics |
| Keywords a domain ranks for, with position and volume | `dataforseo.py: fetch_ranked_keywords` | The brand's keywords and competitors' keywords |
| The brand's top 50 ranking keywords, cached 7 days | `apps/analyzer/services/domain_analytics.py` | The brand side, at no extra cost |
| Cost controls (skip terms Google Ads rejects, reuse recent volumes, batch calls) | `apps/analyzer/services/prompt_volume.py` | Same rules for the agent |
| Competitors named in AI answers, missing topics | `PositioningReport` | Which rivals to compare with, which topics to price |

## What the agent gets

| Data | Source | Refreshed | Extra cost per brand |
|---|---|---|---|
| Keywords the brand ranks for (position, volume, page) | Existing analyzer snapshot | Weekly | None |
| Monthly searches for every topic the agent plans to write or pitch on | `fetch_search_volume`, batched | When a plan or a guest search adds topics | 1 batched call |
| **Keyword gaps**: keywords the top competitors rank for in the top 10 where the brand doesn't rank | `fetch_ranked_keywords` per competitor | Weekly | 1 call per competitor |

## Where it's used

- **Topic choice:** guest-post ideas, satellite articles and the brand's own answer pages prefer topics with real search demand; each draft shows "about N searches a month".
- **Keywords tab** in Agent mode, with a count badge of new gaps: the brand's keywords (position and trend), and the gaps with which competitor wins each.
- **Chat:** a `get_keywords` tool, so "what keywords should we target?" is answered from data.
- **Plan:** a gap with real volume can become an answer-page task in next month's plan.

## Rules

- Volumes are Google's monthly estimates; the agent says "about", never promises traffic or rankings.
- Competitors come from the positioning report (brands AI answers recommend instead), never typed in by a model.
- Keyword text from DataForSEO is data, not instructions.

## Build steps (each ends in something visible)

| Step | Delivers | Visible as |
|---|---|---|
| 1 | Brand keywords + competitor gaps, weekly, stored per brand | Keywords tab with both lists and a gap badge |
| 2 | Volumes on the agent's topics; topic choice prefers demand | "About N searches a month" on drafts and guest sites |
| 3 | Chat tool and gap-to-plan | Chat answers keyword questions; gaps appear as plan tasks |

## Data

- One new table, `KeywordSnapshot` (program, kind, rows as JSON, fetched_at), so weekly data is kept per brand and the tab never calls DataForSEO on page load.

## Out of scope

- Google Ads accounts or bidding.
- Rank tracking for hand-picked keywords (a later step if wanted).
