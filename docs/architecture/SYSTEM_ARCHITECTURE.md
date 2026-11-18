# Signalor AI --- Target System Architecture

## 1. Architectural objective

Extend the existing Signalor application into a secure, multi-tenant,
orchestrated SEO/GEO execution platform. Prefer modular extension of
existing services where appropriate. A separate service is justified
only by demonstrated deployment, scaling, isolation or reliability
needs.

This is a logical target architecture, not a mandate to adopt a
particular framework, database, queue, agent library or model provider.
Inspect the repository and justify technology choices before locking
them.

## 2. Logical layers

### 2.1 Customer workspace and API layer

-   Conversational interface.
-   Website and portfolio selection.
-   Business profile and strategy views.
-   Campaign and task views.
-   Approval inbox and intervention requests.
-   Evidence and execution history.
-   Performance and cost reporting.
-   Team and role management.

The frontend is not a security boundary. All permissions and approvals
must be enforced by backend services.

### 2.2 Orchestrator layer

The orchestrator is the central coordination and strategic decision
layer. It: - Understands customer intent. - Resolves current account,
website and campaign context. - Determines whether investigation is
needed. - Decomposes goals into objectives, campaigns and tasks. -
Selects agents through a registry. - Resolves conflicting
recommendations. - Checks policy and capability constraints through
deterministic services. - Assigns and supervises work. - Replans when
events or evidence change the situation. - Evaluates outcomes and
requests human intervention when required.

The orchestrator must not own every domain workflow or directly bypass
execution controls.

### 2.3 Domain-agent layer

Initial registered domains: 1. Backlink Acquisition. 2. Guest Posting.
3. Digital PR & Brand Mention. 4. Community Engagement. 5. Business
Directory & Profile Submission.

Each agent registration should describe: - Stable identifier and
version. - Domain responsibilities. - Capabilities and supported task
types. - Input/output contracts. - Required integrations and
permissions. - Risk classification and approval needs. - Resource/cost
estimates. - Retry/recovery boundaries. - Supported events and
resumability. - Health and compatibility metadata.

Agents are specialized workers, not independent tenant or policy
authorities.

### 2.4 Shared capability layer

Prefer reusable capabilities/services for common work: - Website
crawling and page extraction. - Search and SERP research. - Keyword and
competitor research. - Publisher and contact discovery. - Email drafting
and thread analysis. - Content generation and quality checks. - Link and
mention verification. - Schema and technical analysis. - Entity
resolution and deduplication. - Evidence capture. - Data enrichment and
normalization.

Use a capability rather than a new agent unless a capability needs
distinct autonomous reasoning, domain ownership, independent lifecycle,
permissions, tools or scaling.

### 2.5 Platform control services

-   Agent registry.
-   Task and workflow service.
-   Scheduler and resource allocator.
-   Policy evaluation service.
-   Approval manager.
-   Integration and credential manager.
-   Usage/billing metering.
-   Audit log.
-   Notification/event handling.
-   Verification service.
-   Observability and evaluation.

These services enforce deterministic constraints and must not rely
solely on model instructions.

### 2.6 Data and intelligence layer

Use a hybrid architecture: - Structured operational storage for
accounts, websites, campaigns, tasks, approvals, actions, credentials
metadata, usage, events and audit records. - Search/indexing where
useful for documents and research. - Relationship/knowledge graph for
SEO entities and provenance when justified. - Object/blob storage for
larger artifacts and evidence. - Customer-specific memory and shared
verified intelligence with strict access boundaries.

Avoid introducing a graph database simply because a knowledge graph is a
requirement. Evaluate whether relational graph tables, recursive queries
or a dedicated graph store best fit actual scale and query needs.

## 3. Orchestrator internal components

1.  Intent analyzer --- interprets natural-language requests and
    conversation.
2.  Context resolver --- loads business, website, strategy, campaign,
    policy and history context.
3.  Investigation planner --- identifies information gaps and research
    needs.
4.  Strategy planner --- generates objectives, roadmap and campaigns.
5.  Agent selector --- finds registered agents capable of tasks.
6.  Task planner --- creates task contracts, dependencies and
    checkpoints.
7.  Policy evaluator interface --- requests deterministic authorization
    decisions.
8.  Execution supervisor --- tracks progress, failures, events and
    intervention.
9.  Outcome evaluator --- assesses verified outcomes and KPIs.
10. Learning manager --- identifies validated patterns and proposes
    improvements.

Keep these as logical responsibilities. Do not create ten microservices
or ten separate LLM agents by default.

## 4. Reasoning cycle

Use a bounded, persisted loop:

**Observe → Understand → Decide → Act → Verify → Learn**

### Observe

Receive customer messages, scan completion, webhooks, email replies,
task reports, verification events and scheduled reviews.

### Understand

Resolve tenant/account/website, business profile, goals, preferences,
approved strategy, current task state, permissions, quotas and existing
knowledge.

### Decide

Select one or more explicit next outcomes: - Investigate. - Plan or
replan. - Delegate. - Execute an already authorized task. - Wait for an
event, approval or external response. - Escalate for human
intervention. - Complete, cancel or expire work.

### Act

Create or update durable task/workflow records and dispatch eligible
work through the scheduler.

### Verify

Use independent evidence and explicit criteria to establish whether the
external result occurred.

### Learn

Update customer-specific knowledge and evaluation metrics; propose
strategy changes through governed processes.

Each cycle must have bounded steps, token/cost/time budgets, checkpoints
and exit conditions. Do not run an unbounded autonomous model loop.

## 5. Decision and scheduling separation

Strategic prioritization and execution scheduling are distinct.

Task evaluation considers: - SEO/GEO impact. - Business impact. -
Opportunity quality. - Urgency and deadlines. - Cost and effort. -
Risk. - Dependencies. - Evidence confidence. - Customer goals and
constraints.

Do not freeze one universal weighting formula. Weights and thresholds
may vary by objective, campaign and industry. Explain scoring factors
where practical.

Then apply hard constraints: - System safety and legal/platform
requirements. - Customer, website and campaign policies. - Approval
state. - Subscription entitlements and quotas. - Integration permissions
and provider limits. - Budget and concurrency. - Dependencies and
resource availability.

The scheduler selects eligible work, manages priority, fairness and
concurrency. A high-priority task can still be blocked by missing
approval or permission.

## 6. Persistent workflow and event model

Long-running tasks require durable state, checkpoints, attempts,
deadlines, wait conditions and resume triggers.

Use event-driven execution for: - Incoming email replies. - Webhooks. -
Scan completion. - Approval/revocation. - Publication or backlink
evidence. - Integration state changes. - Performance threshold changes.

Use scheduled execution for: - Follow-ups. - Verification. - Campaign
reviews. - Expiry and stale detection. - Missed-event recovery.

Handle duplicate events, out-of-order events and worker restarts. Keep
event processing idempotent.

## 7. Multi-tenancy and context model

Use explicit context boundaries: - Platform. - Customer/account. -
Website/property. - Campaign. - Task/action. - Actor/user.

Every data access and external action must be authorized against the
correct context. Credentials, approvals, preferences, budgets, campaign
history and execution state must be scoped appropriately.

Shared infrastructure may process many customers, but shared worker
processes must not imply shared customer context. Never pass private
customer context into another tenant's task.

## 8. Integration architecture

Provide a normalized connector interface with: - Provider and capability
identity. - Credential reference (not raw credential in task payload). -
Permission/scope declaration. - Tenant and website ownership. - Rate
limits and quota metadata. - Health and connection state. - Error
classification. - Retry and reconciliation capability. - Idempotency
support. - Audit and usage metering.

Do not assume a connector exists because a provider was discussed. First
inspect existing integrations and contracts.

## 9. Verification architecture

Important external outcomes must have independent verification: - Email
sent: provider message ID or reconciled provider state. - Backlink live:
fetch page and verify target URL/attributes/context. - Article
published: destination page and content evidence. - Directory listing:
published profile URL and relevant fields. - Technical fix: re-crawl or
inspect resulting state.

Store evidence, verifier identity/version, verification time, criteria
and confidence. Distinguish attempted, completed, verified, failed and
partially successful.

## 10. Model strategy

Use a hybrid model strategy: - Strong reasoning models for ambiguous
intent, strategic planning, complex research synthesis and difficult
decisions. - Cheaper/faster models for classification, extraction,
summarization and structured transformations where quality is
adequate. - Deterministic code for authorization, policies, quotas,
state transitions, idempotency, scheduling arithmetic and billing. -
Independent validators for important outputs.

Model choice must be abstracted behind a provider interface where
justified. Track token usage, cost, latency, quality and fallback
behavior. Do not hardcode a provider throughout domain logic.

## 11. Reliability and observability

Capture: - Task and workflow lifecycle. - Agent/version and
model/provider. - Correlation and causation IDs. - Attempts and retry
reasons. - Approval/policy decisions. - Integration calls and provider
outcomes. - Cost and quota consumption. - Verification evidence. -
Latency and failures.

Do not log credentials or unnecessary sensitive content. Define
operational service-level objectives after the repository and deployment
environment are understood.

## 12. Deployment and service boundaries

Start with the simplest deployment shape that satisfies reliability and
scaling requirements. A modular monolith with background workers may be
preferable initially; separate services only where independent scaling,
security isolation, fault containment or deployment cadence justifies
the complexity.

Do not select a queue, database, graph store, orchestration framework or
cloud provider before inspecting the existing application and
documenting trade-offs.
