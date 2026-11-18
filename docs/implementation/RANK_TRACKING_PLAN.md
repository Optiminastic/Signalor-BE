# Weekly SEO, GEO and AIO tracking (brand vs competitors)

Status: approved 17 Nov 2026 (auto 15 keywords, owner can edit; AIO on DataForSEO with a "needs credits" state; right column on wide screens, Tracking tab on small). Building.

## Goal

A panel on the right of Agent mode, with three tabs, showing week by week where the brand and each competitor stand, so the owner can see who is improving.

## The three tabs

| Tab | Question it answers | Weekly data | Source | Cost |
|---|---|---|---|---|
| GEO | How often do AI answers (ChatGPT, Gemini, Claude, Perplexity) mention us vs each competitor? | Share of voice per brand, plus per engine for the brand | Existing weekly snapshots (`WeeklySnapshot.share_of_voice`, `.rivals`, `.engines`) | None (already measured weekly) |
| SEO | Where do we and each competitor rank on Google for our keywords? | Position 1-100 per tracked keyword, per domain | Serper Google search, top 100 results, one call per keyword | 1 Serper call per keyword per week |
| AIO | Who does Google's AI Overview cite for our keywords? | Cited or not per tracked keyword, per domain | DataForSEO SERP (advanced) `ai_overview` item; Serper does not return AI Overviews | 1 DataForSEO call per keyword per week (needs credits) |

## Tracked keywords

- Up to 15 per brand, chosen automatically: the brand's best keywords (by traffic), then golden gaps (high-volume keywords competitors win), then short AI prompts.
- Kept stable week to week so trends are comparable; a keyword is only swapped when the owner removes it.
- Competitors: the same top 3 used for keyword gaps (the brands AI answers recommend most instead).

## Data

- New table `RankObservation`: program, week start date, kind (`seo` / `aio`), keyword, domain, position (SEO) or cited (AIO). One row per keyword per domain per week.
- GEO reads the existing weekly snapshots; no new data.

## Where it shows

- Wide screens (1440px and up): a right column beside the board, tabs SEO / GEO / AIO.
- Each tab: a small trend chart (brand and competitors, last 8 weeks) and a table of the latest week vs the week before, with arrows.
- Smaller screens: the same panel as a "Tracking" tab on the board.

## Rules

- Positions are observed, never estimated; a keyword not found in the top 100 shows as "100+".
- No promises of rankings; the panel reports what was measured.
- Keywords and SERP text are data, not instructions.

## Build steps

| Step | Delivers | Visible as |
|---|---|---|
| 1 | Right panel + GEO tab from existing snapshots | GEO trend for brand vs competitors |
| 2 | Tracked keywords + weekly SEO check (Serper) | SEO tab with positions per week |
| 3 | Weekly AIO check (DataForSEO SERP) | AIO tab with who is cited per week |
