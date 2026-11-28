# signalor.ai Agent: what we're building

As of 2026-11-24.

We are building an AI agent that grows a brand's visibility in AI answers (ChatGPT, Perplexity, Gemini) and on Google.
It studies the brand and its competitors, plans a month of work, and drafts every email, article and post.
It executes only what the owner approves, then measures the result each week.

For how it works internally, see [AGENT_FLOW.md](AGENT_FLOW.md).

## The problem

When buyers ask an AI assistant what to buy, it names a few brands, and most brands are not among them.
Getting named takes steady off-site work: guest articles, mentions on sites the AI already cites, answer pages, structured data, social presence.
That work is slow and repetitive, and agencies charge for it monthly.

signalor.ai already measures a brand's AI visibility.
The agent closes the loop: it does the work that moves that number, with the owner in control of everything that goes out.

## What the agent does

The agent runs the same loop for every brand, every week:

1. **Understand the brand.** A site analysis and a positioning report: what the brand sells, who its competitors are, and which AI answers name a competitor instead. If the analysis finds no competitors, the agent takes the sites that rank in Google's top 10 for the brand's keywords.
2. **Plan.** A 4-week plan of tasks across channels: outreach emails, guest posts, answer pages on the brand's site, articles on signalor.ai's network sites, social posts, and site fixes such as structured data.
3. **Draft.** Every 15 minutes it drafts what is due next: up to 5 drafts per brand per round, tasks that can run first.
4. **Get approval.** Every draft waits in Approvals until the owner approves that exact version.
5. **Execute.** It sends or publishes the approved version through Gmail, WordPress, GitHub, Buffer or the network sites, after re-checking everything.
6. **Measure.** Weekly: Google ranks against competitors, keyword gaps, social presence, guest-post leads, indexing and site uptime.
7. **Report.** A weekly report in the chat and by email.

## How a brand owner uses it

Everything happens in **Agent mode** on signalor.ai.

- **Chat.** The owner asks questions or gives instructions ("pause outreach", "check my social now", "why did my rank drop?"). The agent answers from the brand's real data and can pause, resume, skip tasks or run checks. It cannot approve or send anything.
- **Tabs.** Activity, Articles, Guest posts, Keywords, Social, Approvals, Indexing and Tracking, each with a count badge when something needs the owner.
- **Approvals.** The owner reads each draft, edits it if needed, then approves or rejects it. An edit creates a new version that needs its own approval.
- **Connections.** The owner connects what the agent may use: Gmail to send outreach, WordPress or GitHub to publish, Buffer for social, Search Console for indexing, Hunter or Apollo to find contact emails.
- **Weekly report.** The calendar icon in the chat, plus an email every week.

## What is live today

| Capability | Status | Depends on |
| --- | --- | --- |
| Chat with tools | Live | AI models (OpenRouter) |
| 4-week plan and drafting | Live | Site analysis, positioning report |
| Approvals with versions | Live | - |
| Outreach email sending | Live | Owner connects Gmail |
| Guest-post sites (10+ a month) | Live | Contact finder (Hunter or Apollo) to pitch them |
| Articles on network sites | Live | Network sites set up for publishing |
| Publishing to the brand's site | Live | Owner connects WordPress or GitHub |
| Social posts | Live | Owner connects Buffer |
| Rank tracking vs competitors | Live | Serper |
| Competitors found on Google (fallback) | Live since 2026-11-24 | Serper |
| Keyword gaps vs competitors | Live (Google positions) | Search volumes need DataForSEO credit |
| Social presence vs competitors | Live (partial) | YouTube and Reddit numbers need API keys |
| Indexing (Google and Bing) | Live | Owner connects Search Console |
| Weekly report (chat + email) | Live | - |
| Google Business Profile | Built, not merged | Google API access, photo storage (S3) |

## Rules it never breaks

- **Nothing goes out without approval.** The owner approves the exact version, and the backend enforces it, not just the interface.
- **A final check before every send.** Right before sending, the agent locks the brand and re-checks the approval, daily caps, opt-outs and that the connection hasn't changed.
- **No blind retries.** If a send's outcome is unclear (a timeout), the action goes to a "needs checking" queue. It is never re-sent automatically.
- **Sends at most once.** Even if a job runs twice, the message goes out once.
- **Everything is recorded.** Every draft, approval and send is in an audit trail that can't be edited.
- **No guessing.** When data is missing (no Search Console, no search volumes), the agent says so instead of making numbers up.
- **Fair share.** One brand can't take over a round: at most 5 drafts and 5 sends per brand per round.

## How it works

See [AGENT_FLOW.md](AGENT_FLOW.md) for two diagrams: the system overview, and one outreach email from plan to sent.

In short:

- **Frontend:** Agent mode on signalor.ai (Next.js on Vercel). Every request goes through a signed proxy.
- **Backend:** the Growth API (Django) re-checks who is asking, that they own the brand, and their plan.
- **Queues:** RabbitMQ. Chat replies and approved sends use a fast queue; brand ticks, drafts and weekly checks use a background queue.
- **Workers:** an interactive worker and a background worker, 2 slots each today.
- **Scheduler:** a round every 15 minutes, one tick per active brand.
- **Storage:** Postgres (plans, tasks, actions, versions, approvals, audit trail) and Redis (locks and markers).
- **Outside services:** OpenRouter (AI models), Serper (Google search data), DataForSEO (search volumes, backlinks), Gmail, WordPress, GitHub, Buffer, Search Console, Bing.

Measured in production this week:

| What | Time |
| --- | --- |
| Scheduler round for 50 brands | about 1.5 s |
| Writing one draft | about 3 s (max 17 s) |
| Chat reply | about 10 s (max 15 s) |

## Open items

- **DataForSEO credit.** Credentials are live in production; the account needs a top-up before search volumes appear.
- **Country for keyword data.** DataForSEO requests default to the US; they should follow each brand's market (India for the3dprintingstore).
- **Worker capacity.** The interactive worker (chat and sends) should go from 2 to 4 slots before many owners chat at once; the background worker and memory limits too. Needs applying in Coolify.
- **Owner connections.** Most brands haven't connected Gmail, Search Console or a contact finder, so outreach, indexing and guest-post pitches can't run.
- **Brands without keywords.** LOKMAT has no tracked keywords, so rank tracking and competitor discovery do nothing for it.
- **Status colours.** Score yellow, green and other status colours fail contrast as text; they need darker text versions.
- **Google Business Profile.** Built (PRs #75 and #98), waiting on Google API access and photo storage.
