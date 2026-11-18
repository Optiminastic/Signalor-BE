# Guest posting on the brand's behalf

Status: approved. Steps 1 (find sites) and 2 (pitching) built, 16 Nov 2026.

## Goal

Get the brand published as a guest author on relevant, real sites in its niche, each article carrying one natural link back to the brand.
The agent finds the sites, pitches the editors from the brand's own Gmail, writes the article once a pitch is accepted, delivers it, and checks that it went live.
The owner approves every email and every article before it leaves.

## Why it matters

- A byline on a site that AI engines already cite is one of the strongest off-page signals for both Google and AI answers.
- Unlike the satellite network, these are independent sites with their own editors, so the links are editorial.

## What exists today (verified in code)

| Piece | Where | Reused for |
|---|---|---|
| Pitch emails from the brand's Gmail, with fact check, sender line and opt-out | `services/outreach/pitch.py`, `channel.py` | The guest-post pitch |
| Email threads, reply detection, opt-outs, follow-ups (each approved on its own) | `models.OutreachThread`, `followups.py`, `follow_up_actions.py` | Tracking each pitch |
| Contact discovery on a site, then Hunter / Apollo when connected | `outreach/contacts.py`, `contact_finders/` | Finding the editor |
| Article writer with brand facts and safety checks | `execution/writer.py`, `safety.py` | Writing the guest article |
| Web search (Serper) | `apps/analyzer/pipeline/*` | Finding "write for us" pages |
| Sites AI answers cite but the brand is missing from | `PositioningReport` (`lost_prompts`, cited domains) | Choosing which sites matter most |
| Weekly indexing routine and Request-indexing links | `services/indexing/` | Getting the live article indexed |
| Owner approval of exact, versioned drafts | `GrowthAction` / `ActionVersion` | Every pitch, article and delivery |

## The flow

| Stage | Who | What happens |
|---|---|---|
| 1. Find | Agent | Builds a list of sites that accept guest posts in the brand's topic (details below). |
| 2. Pitch | Agent drafts, **owner approves** | One short email to the editor with 2 or 3 article ideas fitted to that site. Sent from the brand's Gmail. |
| 3. Follow up | Agent drafts, **owner approves** | Up to 2 short follow-ups, as today. A reply or opt-out stops them. |
| 4. Accepted? | Agent suggests, **owner confirms** | When the editor replies, the agent reads it and suggests "accepted (topic X)", "declined", or "asks for a fee". The owner confirms with one click. |
| 5. Write | Agent drafts, **owner edits and approves** | The full article, written to that site's guidelines (length, tone, link rules), with one natural link to the brand. |
| 6. Deliver | Agent drafts, **owner approves** | A reply in the same thread, with the article in the email body. |
| 7. Live check | Agent | Weekly, looks for the article on the site and checks the link to the brand is there. When live, it's added to the weekly indexing routine. |

Each stage that sends something is its own approved action, so nothing goes out without the owner seeing the exact text.

## Finding sites (stage 1)

Candidates, in this order:
1. **Sites AI answers already cite** for the brand's prompts, where the brand is missing (from the positioning report). Highest value.
2. **"Write for us" searches** through Serper, built from the brand's topics: `"<topic>" "write for us"`, `"<topic>" "guest post" guidelines`, `"<topic>" "contribute"`.
3. **Sites competitors have guest posted on**, from searches for the competitor's name with "guest post" or "author".

Each candidate is checked before it's pitched:
- It has a guest-post or contribute page, or an author listing showing outside authors.
- It's on topic (the page text matches the brand's topics).
- It isn't the brand itself, a competitor, a signalor.ai satellite site, or already contacted (existing outreach contacts).
- It isn't a link farm: no "sponsored post" price list on the guidelines page, no "we accept all topics".

The guidelines page is saved with the candidate, so the pitch and the article follow it.

## Rules

- **No paying for links.** The agent never offers money. If an editor asks for a fee, the thread is marked "asks for a fee" and handed to the owner. If the owner chooses to pay, the link should be marked `rel="sponsored"`; the agent says so, but it can't enforce that on someone else's site.
- **One link to the brand per article**, in context, to a relevant page (not always the home page). No exact-match keyword anchors.
- **Original articles only.** Never the same article to two sites, and never a copy of the brand's own pages.
- **Facts come from the brand's knowledge base** and pass the same fact check as other agent content.
- **Pace:** a cap on new guest pitches per month (default 8), counted with the other outreach emails under the existing daily email cap.

## Where it shows

- A **Guest posts** tab in Agent mode, with a count badge, like the Articles and Social tabs.
- Rows grouped by stage: To pitch, Pitched, Accepted (writing), Delivered, Live, Closed.
- Each row: the site and its logo, the topic, the stage, the next action, and links to the thread and the live article.
- Drafts (pitches, articles, deliveries) open the shared review screen for edit, approve and reject.
- The Agent log records each step, and the weekly summary and the chat agent's status answer include guest posts.

## The plan

- Monthly plans include guest-post tasks (new tactic `guest_post`, its own channel `guest_outreach`), one per candidate site, one every 3 days.
- The `guest_outreach` channel is in the executor from step 2: a due guest-post task is drafted as a pitch, waits for the owner's approval, and is sent from Gmail. It shares the daily outreach email cap (10 a day across both email channels).
- The weekly run (and "Find sites now", at most every 6 hours) searches and checks new sites, up to 8 per plan.
- Plans created before this feature get them added, the same way satellite topics were added.

## Build steps (each ends in something visible)

| Step | Delivers | Visible as |
|---|---|---|
| 1 | Site finder and candidate checks; `guest_post` tactic in plans | Guest posts tab listing candidate sites with why each was picked |
| 2 | Guest pitch drafts and sending (reusing outreach) | Pitches to approve in the tab; sent pitches move to Pitched |
| 3 | Reply reading, suggestion, and owner confirmation | "Editor replied: accepted? Confirm" in the tab |
| 4 | Article writing to the site's guidelines, and delivery in the thread | Article draft to edit and approve; then Delivered |
| 5 | Weekly live check and indexing | Live with a link to the article; added to indexing |

## Data

- No new tables expected for steps 1 and 2: candidates are planned tasks (`target_url` is the site, the guidelines summary goes in the task's payload).
- Step 3 adds the agent's reading of the editor's reply (suggestion and owner confirmation) to the thread.
- Step 4 adds one action kind, `send_guest_article`: a reply in the existing thread whose approved version holds the full article. Writing the article is not an external action, so it needs no action of its own.
- Any migration is choices-only unless a step says otherwise.

## Decisions (16 Nov 2026)

1. **Acceptance:** the agent reads the editor's reply and suggests; the owner confirms with one click before any article is written.
2. **Fees:** an editor asking for a fee hands the thread to the owner; the agent never negotiates money.
3. **Monthly cap:** 8 new guest pitches per brand.
4. **Delivery:** the article goes in the email body.

## Out of scope

- Paying for placements or negotiating prices.
- Posting directly into another site's CMS.
- Guest posts on the signalor.ai satellite sites (that is the satellite network).
