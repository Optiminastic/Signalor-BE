"""Page content: suggestions, rewrites and raw file management."""

import json

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions.throttling import (
    ExpensiveThrottle,
    PollingThrottle,
)

from ..models import (
    AnalysisRun,
)
from ._shared import (
    _resolve_shopify_integration_for_run,
    _serialize_content_suggestion,
)


class ContentPagesView(APIView):
    """GET /api/analyzer/runs/s/<slug>/content/pages/

    Returns the list of pages the user can open in the content editor —
    sourced from the latest sitemap audit, with a fallback to the run's
    root URL.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..services import content_optimisation as co

        run = get_object_or_404(AnalysisRun, slug=slug)
        return Response({"pages": co.list_pages_for_run(run)})

class ContentPageFieldsView(APIView):
    """GET /api/analyzer/runs/s/<slug>/content/page/?url=...

    Returns editable fields + a sandbox-friendly preview HTML for one page.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..services import content_optimisation as co

        url = (request.query_params.get("url") or "").strip()
        if not url:
            return Response({"detail": "url query param required"}, status=400)
        run = get_object_or_404(AnalysisRun, slug=slug)
        try:
            fields = co.fetch_page_fields(run, url)
        except co.ContentOptimisationError as exc:
            return Response({"detail": str(exc)}, status=400)
        # Existing AI suggestions on this page (so a refresh restores them)
        suggestions = [_serialize_content_suggestion(s) for s in co.list_active_suggestions(run, url)]
        return Response({**fields, "suggestions": suggestions})

class ContentSuggestionsView(APIView):
    """POST /api/analyzer/runs/s/<slug>/content/suggestions/  body: {url}

    Generates fresh AI suggestions for a page. Persists ContentSuggestion rows
    and returns them. Old PROPOSED suggestions for the same page are dismissed.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..services import content_optimisation as co

        url = (request.data.get("url") or "").strip()
        if not url:
            return Response({"detail": "url is required"}, status=400)
        run = get_object_or_404(AnalysisRun, slug=slug)
        try:
            suggestions = co.generate_suggestions(run, url)
        except co.ContentOptimisationError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(
            {
                "suggestions": [_serialize_content_suggestion(s) for s in suggestions],
            }
        )

class ContentSuggestionDismissView(APIView):
    """POST /api/analyzer/runs/s/<slug>/content/suggestions/<id>/dismiss/"""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]

    def post(self, request, slug, suggestion_id):
        from django.shortcuts import get_object_or_404

        from ..services import content_optimisation as co

        run = get_object_or_404(AnalysisRun, slug=slug)
        s = co.dismiss_suggestion(run, suggestion_id)
        if not s:
            return Response({"detail": "suggestion not found"}, status=404)
        return Response({"ok": True, "id": s.id, "status": s.status})

class ContentSaveView(APIView):
    """POST /api/analyzer/runs/s/<slug>/content/save/

    Body: {url, fields: {title?, meta_description?, body_html?, schema_jsonld?},
           used_suggestion_ids?: [int, ...]}

    Pushes each provided field to the connected plugin (WP/Shopify) and
    marks any used suggestions as USED. 503 if no integration is connected.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..services import content_optimisation as co

        run = get_object_or_404(AnalysisRun, slug=slug)
        url = (request.data.get("url") or "").strip()
        fields = request.data.get("fields") or {}
        used_ids = request.data.get("used_suggestion_ids") or []

        if not url:
            return Response({"detail": "url is required"}, status=400)
        if not isinstance(fields, dict) or not any(fields.get(f) is not None for f in co.ALL_FIELDS):
            return Response({"detail": "fields must include at least one editable field"}, status=400)

        # Filter to known fields only
        edits = {f: fields[f] for f in co.ALL_FIELDS if fields.get(f) is not None}

        try:
            result = co.save_page_edits(run, url, edits)
        except co.ContentOptimisationError as exc:
            return Response(
                {"detail": str(exc), "code": "no_integration"},
                status=503,
            )

        if isinstance(used_ids, list):
            for sid in used_ids:
                try:
                    co.mark_suggestion_used(run, int(sid))
                except (TypeError, ValueError):
                    continue

        return Response(result)

class ContentRewriteElementView(APIView):
    """POST /api/analyzer/runs/s/<slug>/content/rewrite-element/

    Body: {tag, text, instruction?} — ask the LLM to rewrite one element.
    Returns: {new_text}.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..services import content_optimisation as co

        run = get_object_or_404(AnalysisRun, slug=slug)
        tag = (request.data.get("tag") or "p").strip()
        text = (request.data.get("text") or "").strip()
        instruction = (request.data.get("instruction") or "").strip()
        if not text:
            return Response({"detail": "text is required"}, status=400)
        # Suppress unused-arg warning — `run` is here for future per-run telemetry.
        _ = run
        new_text = co.rewrite_element_text(tag, text, instruction)
        return Response({"new_text": new_text})

class ContentApplyElementView(APIView):
    """POST /api/analyzer/runs/s/<slug>/content/apply-element/

    Body: {url, original_text, new_text} — replace the first occurrence of
    `original_text` in the page's body_html with `new_text` and push the new
    body via the connected plugin.

    Returns the same shape as ContentSaveView: {saved, failed, plugin_responses}.
    503 if no plugin is connected. 400 if the text can't be located.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, slug):
        from django.shortcuts import get_object_or_404

        from ..services import content_optimisation as co

        run = get_object_or_404(AnalysisRun, slug=slug)
        url = (request.data.get("url") or "").strip()
        original_text = (request.data.get("original_text") or "").strip()
        new_text = (request.data.get("new_text") or "").strip()
        if not url or not original_text or not new_text:
            return Response(
                {"detail": "url, original_text, and new_text are required"},
                status=400,
            )

        try:
            result = co.apply_element_edit(run, url, original_text, new_text)
        except co.ContentOptimisationError as exc:
            msg = str(exc)
            status_code = 503 if "integration" in msg.lower() else 400
            return Response({"detail": msg}, status=status_code)
        return Response(result)

class ContentRawFilesListView(APIView):
    """GET /api/analyzer/runs/s/<slug>/content/raw-files/

    Returns the current state of every supported crawler/AI file on the
    connected Shopify store. Shape per item:

        {name, label, kind, url, present, content, default_template}

    `kind` is "theme_asset" (robots.txt) or "page" (everything else served
    at /pages/<handle>).
    """

    permission_classes = [AllowAny]

    def get(self, request, slug):
        from django.shortcuts import get_object_or_404

        from apps.integrations.services.shopify_raw_files import (
            RawFileError,
            list_raw_files,
        )

        run = get_object_or_404(AnalysisRun, slug=slug)
        integ, err = _resolve_shopify_integration_for_run(run)
        if err:
            return err
        try:
            items = list_raw_files(integ)
        except RawFileError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response({"items": items})

class ContentRawFileUpsertView(APIView):
    """PUT /api/analyzer/runs/s/<slug>/content/raw-files/<name>/

    Body: {content: str}. Name is one of: robots, llms, humans, ads.

    For robots.txt the content is written to templates/robots.txt.liquid
    in the active theme. For the others it's saved as the body_html of a
    Shopify Page with handle `<name>-txt`, creating the page if missing.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def put(self, request, slug, name):
        from django.shortcuts import get_object_or_404

        from apps.integrations.services.shopify_raw_files import (
            RAW_FILES,
            RawFileError,
            upsert_raw_file,
        )

        if name not in RAW_FILES:
            return Response(
                {"detail": f"Unknown raw file '{name}'. Allowed: {list(RAW_FILES)}"},
                status=400,
            )
        content = request.data.get("content")
        if content is None:
            return Response({"detail": "content is required"}, status=400)

        run = get_object_or_404(AnalysisRun, slug=slug)
        integ, err = _resolve_shopify_integration_for_run(run)
        if err:
            return err
        try:
            saved = upsert_raw_file(integ, name, content)
        except RawFileError as exc:
            return Response({"detail": str(exc)}, status=502)
        return Response(saved)

class AnswerGapFaqView(APIView):
    """POST /runs/s/<slug>/answer-gap-faq/

    Turns the run's weakest tracked prompts (real questions where AI engines
    under-represent the brand) into publishable FAQ content: question/answer
    pairs written for the brand, plus FAQPage JSON-LD built server-side so the
    schema is always valid regardless of LLM output.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    _MAX_PROMPTS = 6

    @staticmethod
    def _generation_prompt(run, tracks) -> str:
        brand = run.brand_name or run.url
        questions = "\n".join(f"- {t.prompt_text}" for t in tracks)
        return (
            f"You write FAQ content for {brand} ({run.url}).\n"
            "These are real questions people ask AI assistants, where the brand is "
            "currently under-represented in the answers:\n"
            f"{questions}\n\n"
            "Write an FAQ section the brand can publish on its own site. For each "
            "input question produce one entry: rephrase the question naturally from "
            "a customer's perspective, then answer in 2-4 factual, direct sentences "
            "that lead with the answer and mention the brand naturally exactly once. "
            "No marketing fluff and no superlatives you cannot verify.\n"
            'Return STRICT JSON only: [{"question": "...", "answer": "..."}]'
        )

    def post(self, request, slug):

        from django.shortcuts import get_object_or_404

        from core.llm.client import ask_llm
        from core.llm.structured import extract_json

        run = get_object_or_404(AnalysisRun, slug=slug)
        tracks = list(
            run.prompt_tracks.filter(deleted_at__isnull=True).order_by("score", "-created_at")[
                : self._MAX_PROMPTS
            ]
        )
        if not tracks:
            return Response(
                {"detail": "No tracked prompts yet."}, status=status.HTTP_400_BAD_REQUEST
            )

        text = ask_llm(
            self._generation_prompt(run, tracks),
            max_tokens=1600,
            purpose=f"Answer-gap FAQ ({slug})",
            tier="medium",
        )
        raw_items = extract_json(text or "", expect=list) or []
        items = [
            {
                "question": str(i.get("question", "")).strip(),
                "answer": str(i.get("answer", "")).strip(),
            }
            for i in raw_items
            if isinstance(i, dict) and i.get("question") and i.get("answer")
        ]
        if not items:
            return Response(
                {"detail": "Generation failed. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        jsonld = json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": i["question"],
                        "acceptedAnswer": {"@type": "Answer", "text": i["answer"]},
                    }
                    for i in items
                ],
            },
            indent=2,
        )
        return Response({"items": items, "jsonld": jsonld})
