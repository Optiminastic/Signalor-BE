"""Endpoints without a home yet. Re-home these as domains firm up."""

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import (
    AiChatThrottle,
)

from ..models import (
    AnalysisRun,
)
from ._shared import (
    logger,
)


class AiChatView(APIView):
    """
    GET  /api/analyzer/runs/s/<slug>/chat/ — return persisted chat history.
    POST /api/analyzer/runs/s/<slug>/chat/ — send a message; reply persists.

    The backend is the source of truth for chat history — clients no longer
    need to round-trip the conversation. Sending `history` in the body is
    ignored unless the run has zero saved messages (legacy migration aid).
    """

    permission_classes = [AllowAny]
    throttle_classes = [AiChatThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import ChatMessage

        run = get_object_or_404(AnalysisRun, slug=slug)
        msgs = ChatMessage.objects.filter(analysis_run=run).order_by("created_at")
        return Response(
            {
                "messages": [
                    {
                        "role": m.role,
                        "content": m.content,
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in msgs
                ]
            }
        )

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..models import ChatMessage

        run = get_object_or_404(AnalysisRun, slug=slug)
        message = request.data.get("message", "").strip()
        # Pull persisted history; fall back to client-sent on first ever message.
        persisted = list(ChatMessage.objects.filter(analysis_run=run).order_by("created_at"))
        if persisted:
            history = [{"role": m.role, "content": m.content} for m in persisted]
        else:
            history = request.data.get("history", []) or []

        if not message:
            return Response({"error": "message required"}, status=status.HTTP_400_BAD_REQUEST)

        # Build context from THIS run's stored data only — no re-crawling
        page_score = run.page_scores.filter(url=run.url).first()
        all_page_scores = list(
            run.page_scores.all().values(
                "url", "content_score", "schema_score", "eeat_score", "technical_score"
            )
        )
        recs = list(run.recommendations.values_list("title", "priority", "pillar", "description")[:15])

        # Extract brand info from stored analysis details
        word_count = 0
        site_discovery = {}

        if page_score and page_score.content_details:
            checks = page_score.content_details.get("checks", {})
            coverage = checks.get("coverage_depth", {})
            word_count = checks.get("word_count", coverage.get("word_count", 0))
            site_discovery = checks.get("site_discovery", {})

        # Build context
        brand = run.brand_name or run.url
        context_parts = [
            f"Brand: {brand}",
            f"URL: {run.url}",
            f"Word count on homepage: {word_count}",
        ]

        if site_discovery:
            context_parts.append(
                f"Site structure: {site_discovery.get('products', 0)} products, "
                f"{site_discovery.get('collections', 0)} collections, "
                f"{site_discovery.get('pages', 0)} pages, "
                f"{site_discovery.get('blog_posts', 0)} blog posts"
            )

        context_parts.append("\n--- EXACT SCORES (from this analysis) ---")
        context_parts.append(f"Overall GEO Score: {round(run.composite_score, 1)}/100")

        if page_score:
            context_parts.extend(
                [
                    f"Technical: {round(page_score.technical_score, 1)}/100",
                    f"Schema: {round(page_score.schema_score, 1)}/100",
                    f"Content: {round(page_score.content_score, 1)}/100",
                    f"E-E-A-T: {round(page_score.eeat_score, 1)}/100",
                    f"Entity: {round(page_score.entity_score, 1)}/100",
                    f"AI Visibility: {round(page_score.ai_visibility_score, 1)}/100",
                ]
            )

            # Add detail breakdowns if available
            if page_score.content_details and page_score.content_details.get("checks"):
                cc = page_score.content_details["checks"]
                context_parts.append(
                    f"\nContent breakdown: intent={cc.get('intent_score', '?')}, "
                    f"coverage={cc.get('coverage_score', '?')}, "
                    f"density={cc.get('density_score', '?')}, "
                    f"structure={cc.get('structure_score', '?')}"
                )

            if page_score.eeat_details and page_score.eeat_details.get("checks"):
                ec = page_score.eeat_details["checks"]
                context_parts.append(
                    f"E-E-A-T breakdown: identity={ec.get('identity_score', '?')}, "
                    f"evidence={ec.get('evidence_score', '?')}, "
                    f"experience={ec.get('experience_score', '?')}, "
                    f"trust={ec.get('trust_score', '?')}"
                )

            if page_score.technical_details and page_score.technical_details.get("checks"):
                tc = page_score.technical_details["checks"]
                context_parts.append(
                    f"Technical breakdown: infra={tc.get('infra_score', '?')}, "
                    f"perf={tc.get('perf_score', '?')}, "
                    f"crawl={tc.get('crawl_score', '?')}, "
                    f"ai_read={tc.get('ai_read_score', '?')}, "
                    f"struct={tc.get('struct_score', '?')}"
                )

        if len(all_page_scores) > 1:
            context_parts.append(f"\nPages analyzed: {len(all_page_scores)}")
            for ps in all_page_scores[:5]:
                context_parts.append(
                    f"  - {ps['url']}: content={round(ps['content_score'], 1)}, "
                    f"schema={round(ps['schema_score'], 1)}, eeat={round(ps['eeat_score'], 1)}"
                )

        if recs:
            context_parts.append("\nRecommendations:")
            for title, priority, pillar, desc in recs:
                context_parts.append(f"- [{priority}] {pillar}: {title} — {desc[:120]}")

        # Include findings (specific issues detected)
        if page_score and page_score.content_details:
            findings = page_score.content_details.get("findings", [])
            if findings:
                context_parts.append(f"\nContent issues found: {', '.join(findings)}")
        if page_score and page_score.eeat_details:
            findings = page_score.eeat_details.get("findings", [])
            if findings:
                context_parts.append(f"E-E-A-T issues found: {', '.join(findings)}")
        if page_score and page_score.technical_details:
            findings = page_score.technical_details.get("findings", [])
            if findings:
                context_parts.append(f"Technical issues found: {', '.join(findings)}")

        context = "\n".join(context_parts)

        # Detect platform from URL
        is_shopify = ".myshopify.com" in (run.url or "")

        # Build system prompt
        platform_name = "Shopify" if is_shopify else "WordPress"
        system_prompt = f"""You are Signalor's GEO (Generative Engine Optimization) assistant for {brand}.
You help D2C brand owners improve their AI visibility — how often ChatGPT, Gemini, and Perplexity recommend their brand.

The user is a {platform_name} store owner. They are NOT a developer. Give instructions using ONLY the {platform_name} admin UI — never tell them to edit code, Liquid templates, or theme files.

{context}

RESPONSE FORMAT RULES:
- Use **bold** for important terms and action items.
- Use numbered steps (1. 2. 3.) for instructions.
- Use bullet points (- ) for lists.
- Keep each step short and specific: "Go to X → click Y → do Z".
- Give EXACT {platform_name} Admin paths. Example: "Shopify Admin → Online Store → Pages → click on your page → edit the title"
- Include specific examples relevant to their brand when possible.
- If they ask "how to fix" something, give step-by-step {platform_name} instructions immediately — don't explain theory first.
- Maximum 4-5 short paragraphs or a numbered list of 5-8 steps.
- Be encouraging but direct. No fluff.
- ONLY use the EXACT scores shown above. Never guess.
- If you don't know something about their products, say so."""

        messages = [{"role": "system", "content": system_prompt}]
        for h in history[-8:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": message})

        # Persist the user message before invoking the LLM so it survives errors.
        ChatMessage.objects.create(analysis_run=run, role=ChatMessage.Role.USER, content=message)

        # Call Gemini via the LLM pipeline
        try:
            from core.llm.client import ask_llm

            # Build a single prompt with conversation context
            conv = ""
            for m in messages:
                if m["role"] == "system":
                    conv += f"System: {m['content']}\n\n"
                elif m["role"] == "user":
                    conv += f"User: {m['content']}\n"
                elif m["role"] == "assistant":
                    conv += f"Assistant: {m['content']}\n"
            conv += "Assistant:"

            reply = ask_llm(conv, preferred_provider="gemini", max_tokens=800, purpose="GEO Chat")

            if not reply:
                reply = "I'm having trouble connecting right now. Please try again in a moment."

            reply_text = reply.strip()
            ChatMessage.objects.create(analysis_run=run, role=ChatMessage.Role.ASSISTANT, content=reply_text)
            return Response({"reply": reply_text})
        except Exception as exc:
            logger.warning("AI Chat failed: %s", exc)
            fallback = "Sorry, I couldn't process that right now. Please try again."
            ChatMessage.objects.create(analysis_run=run, role=ChatMessage.Role.ASSISTANT, content=fallback)
            return Response({"reply": fallback})

class WeeklyTestEmailView(APIView):
    """POST /api/analyzer/runs/s/<slug>/email/weekly-test/
    Sends a weekly analytics email to the given address using live run data."""

    permission_classes = [AllowAny]

    def post(self, request, slug):
        import datetime
        import urllib.parse

        from apps.analyzer.email_utils import send_weekly_email

        to_email = request.data.get("email", "").strip()
        if not to_email:
            return Response({"error": "email is required."}, status=400)

        try:
            run = AnalysisRun.objects.get(slug=slug)
        except AnalysisRun.DoesNotExist:
            return Response({"error": "Run not found."}, status=404)

        def _domain(url: str) -> str:
            try:
                u = url if url.startswith("http") else f"https://{url}"
                netloc = urllib.parse.urlparse(u).netloc
                # .lstrip("www.") strips any combination of those chars
                # (e.g. "wxample.com" -> "xample.com"); use removeprefix
                # to match the literal "www." only.
                return netloc.removeprefix("www.")
            except Exception:
                return url

        competitors = [
            {
                "name": c.name or "",
                "url": c.url or "",
                "domain": _domain(c.url or ""),
                "composite_score": c.composite_score,
                "relevance_score": c.relevance_score,
            }
            for c in run.competitors.order_by("-relevance_score")[:6]
        ]

        prompts = list(run.prompt_tracks.filter(deleted_at__isnull=True).order_by("-score")[:5])
        recommendations = list(
            run.recommendations.filter(priority__in=["critical", "high"]).order_by("priority")[:5]
        )
        brand_vis = getattr(run, "brand_visibility", None)

        from django.db.models import Case, When
        from django.db.models import IntegerField as DjIntegerField

        from ..models import SitemapAudit

        def _page_issue(page):
            sc = page.status_code or 0
            if sc >= 500:
                return ("FAIL", "Server Error", f"HTTP {sc} — server error")
            if sc >= 400:
                return ("FAIL", "Page Not Found", f"HTTP {sc} — unreachable")
            ai_blocked = (
                not page.robots_allows_gptbot
                and not page.robots_allows_claudebot
                and not page.robots_allows_perplexitybot
            )
            if ai_blocked:
                return ("FAIL", "AI Crawlers Blocked", "robots.txt blocks all AI crawlers")
            if page.is_noindex:
                return ("FAIL", "Excluded from Search", "noindex directive set")
            if page.ai_score is not None and page.ai_score < 30:
                return ("FAIL", "Critical AI Visibility Gap", f"AI score {page.ai_score}/100")
            if not page.jsonld_count:
                return ("WARN", "No Structured Data", "Missing JSON-LD schema")
            if not page.robots_allows_gptbot or not page.robots_allows_claudebot:
                return ("WARN", "Partial AI Crawl Block", "Some AI crawlers blocked")
            if not page.has_canonical:
                return ("WARN", "Missing Canonical Tag", "No canonical URL")
            if not page.has_og:
                return ("WARN", "Missing Open Graph Tags", "OG tags absent")
            if page.ai_score is not None and page.ai_score < 60:
                return ("WARN", "Low AI Visibility Score", f"AI score {page.ai_score}/100")
            findings = page.findings if isinstance(page.findings, list) else []
            if findings:
                f = findings[0]
                return (
                    (f.get("severity") or page.severity or "WARN").upper(),
                    f.get("title") or f.get("name") or "Issue Detected",
                    f.get("description") or f.get("message") or "Unresolved issue",
                )
            return ("WARN", "Low AI Visibility", f"AI score {page.ai_score or 0}/100")

        sitemap = SitemapAudit.objects.filter(analysis_run=run).order_by("-created_at").first()
        critical_pages = []
        if sitemap:
            severity_order = Case(
                When(severity="fail", then=0),
                When(severity="warn", then=1),
                default=2,
                output_field=DjIntegerField(),
            )
            raw_pages = sitemap.pages.exclude(severity="ok").order_by(severity_order, "ai_score")[:5]
            for page in raw_pages:
                sev, title, desc = _page_issue(page)
                path = page.path or page.url or ""
                critical_pages.append(
                    {
                        "severity": sev,
                        "title": title,
                        "description": desc,
                        "path": path if len(path) <= 65 else path[:62] + "...",
                        "url": page.url or "",
                    }
                )

        context = {
            "brand_name": run.brand_name or "",
            "url": run.url or "",
            "brand_domain": _domain(run.url or ""),
            "slug": run.slug,
            "score": round(run.composite_score or 0),
            "competitors": competitors,
            "prompts": prompts,
            "recommendations": recommendations,
            "brand_visibility": brand_vis,
            "critical_pages": critical_pages,
            "report_date": datetime.date.today().strftime("%B %d, %Y"),
        }

        try:
            sent = send_weekly_email(to_email, context)
            return Response({"ok": True, "sent": sent})
        except Exception:
            logger.exception("WeeklyTestEmailView error for %s slug=%s", to_email, slug)
            return Response({"ok": False, "error": "Email dispatch failed."}, status=500)
