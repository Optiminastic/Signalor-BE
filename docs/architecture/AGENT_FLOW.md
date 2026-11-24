# How the signalor.ai agent works

Two diagrams.
The first shows the parts and how work moves between them.
The second follows one outreach email from plan to sent.
Both describe production as of 2026-11-24.

## 1. System overview

```mermaid
flowchart TB
    owner(["Brand owner"])

    subgraph FE["signalor.ai frontend (Next.js, Vercel)"]
        ui["Agent mode<br/>chat + tabs: Approvals, Articles, Guest posts,<br/>Keywords, Social, Google Business, Indexing, Tracking"]
        proxy["/api/agent-mode proxy<br/>checks the session, signs each request (HMAC)"]
    end

    subgraph BE["Backend (Django, Coolify server)"]
        api["Growth API<br/>re-checks signature, owner and plan"]
        sched["growth-scheduler<br/>a round every 15 min"]
        subgraph Q["RabbitMQ (quorum queues + dead-letter queues)"]
            qi[["growth.interactive<br/>chat replies, previews, approved sends"]]
            qb[["growth.background<br/>brand ticks, drafts, weekly checks"]]
        end
        wi["growth-interactive-worker (2 slots)"]
        wb["growth-worker (2 slots, 4 ready)"]
        db[("Postgres<br/>programs, cycles, tasks, actions,<br/>versions, approvals, audit trail")]
        cache[("Redis<br/>locks, queued markers, heartbeat")]
    end

    subgraph EXT["Outside services"]
        llm["AI models (OpenRouter)"]
        serper["Google search data (Serper)"]
        pub["Publishing and sending:<br/>Gmail, WordPress, GitHub, Buffer, satellite sites"]
        google["Google Search Console, Bing"]
    end

    owner --> ui --> proxy --> api
    api -- "chat message" --> qi
    api -- "approve / edit / reject" --> db
    api -- "approved send" --> qi
    sched -- "one tick per active brand<br/>(skipped if its last tick is still waiting)" --> qb
    qi --> wi
    qb --> wb
    wb -- "tick queues up to 5 drafts<br/>(runnable tasks first) and up to 5 sends" --> qb
    wb -- "tick queues sends" --> qi
    wi <--> llm
    wb <--> llm
    wb <--> serper
    wi -- "send / publish after final check" --> pub
    wb <--> google
    wi <--> db
    wb <--> db
    wb <--> cache
    sched <--> cache
    db -. "weekly report + email" .-> owner
```

## 2. One outreach email, start to finish

```mermaid
sequenceDiagram
    autonumber
    actor O as Brand owner
    participant UI as Agent mode (frontend)
    participant API as Growth API
    participant S as Scheduler
    participant W as Background worker
    participant I as Interactive worker
    participant AI as AI models
    participant G as Gmail

    Note over API,W: Onboarding: site analysis, then a positioning report,<br/>then a 4-week plan of tasks per channel
    S->>W: brand tick (every 15 min)
    W->>W: pick due tasks: runnable first, waiting ones hourly
    W->>W: queue a draft job for the task
    W->>AI: write the email (brand facts, competitors, target)
    AI-->>W: draft
    W->>W: safety checks, then save version 1 (content hash)
    W-->>UI: draft appears in Approvals
    O->>UI: review, maybe edit (each edit makes a new version)
    UI->>API: approve version N with its content hash
    API->>API: owner only, version and hash must match
    S->>W: next tick: action is approved and due
    W->>I: queue the send
    I->>I: claim, lock the brand, re-run every check:<br/>approval still valid, caps, opt-outs, connection unchanged
    I->>G: send the exact approved version
    G-->>I: result (or timeout)
    alt delivered
        I->>API: record evidence: succeeded
    else timeout or unclear
        I->>API: needs reconciliation (never blindly re-sent)
    end
    W->>W: later ticks: check replies, draft follow-ups (each needs its own approval)
    Note over O,G: Weekly: ranks, keywords, social, guest sites,<br/>indexing, then a weekly report in chat and by email
```

## Key rules the diagrams encode

- Nothing is sent or published without the owner approving that exact version.
- An edit creates a new version and needs a fresh approval.
- The final check runs again right before sending, under the brand's lock.
- An unclear send outcome goes to reconciliation; it is never retried blindly.
- Chat replies and approved sends never wait behind drafts or site analyses.
- One brand can't use up a whole round: at most 5 drafts and 5 sends per brand per round.
