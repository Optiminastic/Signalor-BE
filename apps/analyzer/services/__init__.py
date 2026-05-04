"""
Service layer for analyzer features.

Each service encapsulates the business logic for one user-facing concept
(e.g. backlink opportunities, citation authority). Views become thin HTTP
adapters that delegate to a service and translate the result to a Response.

Why a service layer here:
  - Single Responsibility: views handle HTTP, services handle domain logic.
  - Testability: services don't depend on `request` / `Response`, so unit
    tests don't need DRF's APIClient harness.
  - Reuse: a Celery task or management command can call the same service
    without duplicating the orchestration.
"""
