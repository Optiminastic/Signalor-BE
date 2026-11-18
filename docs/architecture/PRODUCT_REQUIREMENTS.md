# Signalor AI --- Product Requirements

## 1. Product vision

Build a natural-language SEO/GEO agent platform inside Signalor AI.
Customers should be able to express outcomes such as improving organic
visibility, improving AI-search visibility, earning relevant backlinks,
building domain authority, finding guest-post publishers, improving
content and resolving technical SEO problems.

The product is not merely an audit/reporting tool. It should
investigate, understand the business, propose a strategy, coordinate
specialized agents, execute authorized work, verify results, report
progress and adapt based on evidence.

The platform must extend the existing Signalor SaaS and reuse its
scanning, customer, website, authentication, subscription and analytics
capabilities where suitable.

## 2. SEO/GEO scope

-   Full SEO scope: technical SEO, on-page SEO, content, keyword
    research, off-page SEO, competitor intelligence and performance
    monitoring.
-   Strategic resource emphasis: approximately 65% off-page and 35%
    on-page/technical as a prioritization preference, not a hard
    limitation.
-   Critical technical/indexability problems take precedence when they
    materially block visibility or business outcomes.
-   Traditional search and AI-search visibility are connected but
    distinct. Improving one does not guarantee improvement in the other.
-   GEO measurement may include AI brand mentions, citations,
    recommendations, share of voice and competitor comparisons across
    supported AI platforms. Metrics must be grounded in actual available
    data.

## 3. Initial domain agents

The first five off-page domains are:

### 3.1 Backlink Acquisition Agent

-   Discover relevant publishers and websites that may accept guest
    posts or link insertions.
-   Find relevant existing articles and potential placement
    opportunities.
-   Identify editors, owners and authors using permitted sources.
-   Qualify opportunities by relevance, quality, trust, audience,
    likelihood, value, risk and evidence confidence.
-   Prepare personalized outreach and follow-ups.
-   Negotiate only within approved boundaries.
-   Track pending, accepted, rejected and published placements.
-   Verify live backlinks and record evidence.

### 3.2 Guest Posting Agent

-   Discover publishers and contribution guidelines.
-   Qualify sites and identify relevant topics.
-   Generate publisher-specific pitches and topic proposals.
-   Draft articles after acceptance and according to approved briefs.
-   Submit only through permitted workflows and required approvals.
-   Track editorial feedback, publication and backlink verification.

### 3.3 Digital PR & Brand Mention Agent

-   Discover relevant journalists, media opportunities and expert
    requests.
-   Prepare evidence-based expert quotes, founder responses, research
    angles and pitches.
-   Support press-release workflows where appropriate.
-   Track outreach, replies, publication, brand mentions and backlinks.
-   Never fabricate credentials, statistics, quotes or claims.

### 3.4 Community Engagement Agent

-   Discover relevant public discussions, communities, forums, Q&A and
    social discussions.
-   Draft genuinely useful, contextual responses.
-   Avoid spam, astroturfing, deceptive identity, undisclosed promotion
    and repetitive posting.
-   Publishing must comply with platform rules and available
    APIs/workflows.
-   Community posts must use the disclosed brand account only where
    account identity is relevant.
-   Require approval according to risk and campaign policy.
-   Track responses, engagement, mentions and resulting opportunities.

### 3.5 Business Directory & Profile Submission Agent

-   Discover relevant industry directories, startup listings and product
    discovery platforms.
-   Qualify opportunities and record submission requirements.
-   Prepare accurate, consistent company profiles and descriptions.
-   Submit through permitted workflows with required approval.
-   Track pending, accepted and rejected listings.
-   Maintain business information consistency and verify published
    profiles.

These are initial domains, not a permanent cap. Future agents must be
addable through a registry and explicit contracts.

## 4. Other SEO capabilities

The platform should be extensible to: - Technical SEO audits and
remediation. - On-page optimization and metadata. - Content strategy,
briefs and optimization. - Keyword research and intent mapping. -
Internal linking. - Schema and structured data. - Competitor
intelligence. - Local SEO. - Ecommerce SEO. - Search and AI visibility
monitoring. - Core Web Vitals and performance analysis.

Do not assume all future capabilities must be independent agents.
Reusable capabilities may be shared across domain agents.

## 5. Business understanding and persistent profile

The system must investigate and maintain a customer-specific business
profile containing, as relevant: - Business model, products/services and
value proposition. - Target audience, markets and geography. - Industry
and positioning. - Website structure and important pages. -
Competitors. - Customer goals and success metrics. - Brand voice and
communication preferences. - Restrictions, risk tolerance and
negotiation boundaries. - Relevant connected integrations and
permissions.

Use hybrid discovery: investigate automatically first, then present the
system's understanding for correction and ask only essential unanswered
questions. Customer corrections override assumptions. Revalidate stale
facts and preserve provenance.

## 6. Strategy and campaign management

-   Combine customer goals with actual website condition and evidence.
-   Do not blindly execute a requested tactic when evidence suggests a
    materially better path; explain the alternative.
-   Create an overall roadmap (for example 30/60/90 days or longer),
    campaigns, tasks, dependencies and milestones.
-   Estimate effort, cost, risks, dependencies, expected signals and
    measurable outcomes.
-   Customers can approve the roadmap or individual campaigns, reject
    activities, change priorities and budgets, and request revisions.
-   Recalculate dependencies and forecasts when the strategy changes.
-   Preserve useful completed work and commitments when campaigns are
    changed.
-   Coordinate overlapping customer requests under a unified strategy.
-   Agents may challenge a strategy and recommend alternatives; the
    orchestrator decides adoption.
-   Discovery of an opportunity does not authorize outreach, spend,
    publication or other external action.

## 7. Execution and autonomy

-   Persistent tasks must survive worker restarts and long waits.
-   Support event-driven triggers (replies, webhooks, approvals, scan
    completion, publication) and scheduled triggers (follow-ups,
    verification, periodic reviews and missed-event recovery).
-   Support checkpoints, deadlines, retries, waiting conditions,
    cancellation and resumability.
-   Agents may adapt internal workflows within approved boundaries.
-   Orchestrator owns cross-campaign coordination, agent assignment and
    strategy changes.
-   Domain agents own specialized workflows and bounded recovery.
-   Independent verification is required for important outcomes.
-   Report attempted, completed, verified, failed and partially
    successful states accurately.
-   Do not guarantee rankings, traffic, publication acceptance or other
    outcomes controlled by third parties.

## 8. Human collaboration

Authorized users must be able to: - Approve, reject, edit or revoke
individual actions. - Approve or reject eligible batches. - Provide
missing information. - Change campaign constraints and preferences. -
Take over a task or negotiation. - Review evidence, risks, costs and
recommended options.

Intervention requests must include context, evidence, recommended
options and required decision/permission. Preserve task state while
waiting.

## 9. Measurement and reporting

Track execution metrics such as: - Emails drafted/sent/replied to. -
Opportunities discovered and qualified. - Placements
accepted/published/verified. - Articles drafted/published. - Directory
submissions accepted/rejected. - Technical fixes attempted and verified.

Track SEO/business outcomes where data is available: - Search
impressions, clicks, rankings and organic traffic. - Referring domains
and backlinks. - Brand mentions and AI citations. - Leads, conversions
and organic revenue where measurable.

Establish baselines. Separate execution progress, leading indicators and
lagging business outcomes. Do not claim causation from correlation.
Explain delays, uncertainty and forecast changes.

## 10. Multi-tenant and multi-website requirements

-   Shared platform implementation may serve many customers.
-   Each customer and website has isolated business context,
    credentials, approvals, budgets, campaigns, usage, execution state
    and private history.
-   One account may manage multiple websites with separate strategies
    and permissions.
-   Provide portfolio-level reporting and carefully controlled shared
    resources where appropriate.
-   Team access must be role-based and website-scoped.
-   A member with access to one website does not automatically gain
    access to all websites.
-   Never leak private customer data across tenants or websites.

## 11. Subscription and commercial constraints

-   Respect plan entitlements, feature availability, usage quotas,
    concurrency limits and resource limits.
-   Track Signalor subscription usage separately from third-party API
    charges and external campaign expenses.
-   Allocate resources intelligently within available quotas.
-   When one quota is exhausted, pause only dependent tasks and continue
    eligible work.
-   Preserve state and resume when quota resets or plan changes.
-   Do not incur unapproved overage charges.
-   Explain when a task needs a higher plan, more quota or a paid
    external service.

## 12. Integration requirements

Support a hybrid model: - Signalor-managed shared integrations/services
where appropriate. - Customer-connected accounts and API credentials
where required.

Credentials must be encrypted, isolated, scoped, revocable and
monitored. The orchestrator must know which integrations and permissions
are available before assigning work. Diagnose failures, retry transient
errors safely, preserve state, prevent duplicates and never silently
change permissions, data handling or cost.

Potential providers discussed during planning are candidates only, not
approved commitments. Choose providers only after capability, cost,
terms, reliability and repository fit are evaluated.

## 13. Intelligence and knowledge

-   Maintain customer-specific knowledge separately from shared
    public/industry intelligence.
-   Preserve source, discovery time, verification time, confidence,
    status and provenance.
-   Represent facts, inferences and unknowns distinctly.
-   Preserve conflicting source findings and assess
    reliability/freshness rather than manufacturing a single unsupported
    answer.
-   Reuse verified research when relevant; refresh stale or critical
    facts.
-   Maintain a relationship-based SEO knowledge graph connecting
    customers, websites, competitors, publishers, authors, keywords,
    backlinks, mentions, communities and opportunities where justified.
-   Do not generalize one customer's private results to another.

## 14. Risk and brand protection

Policies must classify activities as prohibited, restricted or
permitted. Assess search-engine guidelines, platform rules, link-spam
risk, content quality, brand safety, outreach reputation, privacy, legal
requirements and live-site impact.

Customer approval cannot make an inherently prohibited action
acceptable. Explain risks and safer alternatives. Avoid deceptive
identities, fabricated evidence, spam, unauthorized access, hidden paid
links and manipulative tactics.

## 15. Continuous improvement

Evaluate agent completion, quality, success/failure, time, cost per
success, recovery, customer approvals/rejections and measurable
outcomes.

The system may propose new capabilities, agents, tools and workflows.
Any self-evolution must be designed, tested in isolation,
security/cost/reliability validated, explicitly authorized before
production deployment, gradually rolled out and reversible. Agents
cannot grant themselves permissions or rewrite production governance.
