"""User-facing tasks and the gamification around them."""

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Organization
from core.auth.identity import resolve_request_email
from core.permissions.throttling import (
    ExpensiveThrottle,
    PollingThrottle,
)

from ..models import (
    ACHIEVEMENTS_INFO,
    ACTION_TEMPLATES,
    AnalysisRun,
    Recommendation,
    UserAction,
    UserGamification,
)
from ..serializers import (
    CreateUserActionSerializer,
    UpdateUserActionSerializer,
    UserActionSerializer,
    UserGamificationSerializer,
    prompt_track_index,
)

# One request should not be able to queue unbounded writes.
MAX_BULK_RECOMMENDATIONS = 50

class UserGamificationView(APIView):
    """Get user gamification profile"""

    permission_classes = [AllowAny]

    def get(self, request):
        # Never the client-supplied ?email=: that is a cross-tenant read.
        email, err = resolve_request_email(request)
        if err is not None:
            return err

        gamification, created = UserGamification.objects.get_or_create(
            user_email=email, defaults={"user_email": email}
        )

        serializer = UserGamificationSerializer(gamification)
        return Response(serializer.data)

class ActionTemplatesView(APIView):
    """Get available action templates"""

    permission_classes = [AllowAny]

    def get(self, request):
        templates = [{**template, "action_type": key} for key, template in ACTION_TEMPLATES.items()]
        return Response(templates)

class AchievementsView(APIView):
    """Get all possible achievements"""

    permission_classes = [AllowAny]

    def get(self, request):
        achievements = [{**info, "code": key} for key, info in ACHIEVEMENTS_INFO.items()]
        return Response(achievements)

class UserActionListView(APIView):
    """List user's actions"""

    permission_classes = [AllowAny]
    throttle_classes = [PollingThrottle]  # used by sidebar/actions dashboard refreshes

    def get(self, request):
        # Never the client-supplied ?email=: that is a cross-tenant read.
        email, err = resolve_request_email(request)
        if err is not None:
            return err

        status_filter = request.query_params.get("status")

        from apps.accounts.agency_utils import agency_org_ids, get_agency_context

        # Role-scoped visibility:
        #   agency admin  → every task across the agency's brands
        #   agency member → only tasks assigned to them
        #   individual    → their own tasks (owner)
        ctx = get_agency_context(email)
        if ctx is not None and ctx.is_admin:
            org_ids = agency_org_ids(ctx.agency_email)
            actions = UserAction.objects.filter(analysis_run__organization_id__in=org_ids)
        elif ctx is not None:
            actions = UserAction.objects.filter(assignee_email=email)
        else:
            actions = UserAction.objects.filter(user_email=email)

        if status_filter:
            actions = actions.filter(status=status_filter)

        # Backstop against unbounded response growth — the agency-admin branch spans
        # every brand in the agency (see shared.pagination).
        from shared.pagination import bounded_slice

        # select_related is load-bearing: the serializer reads pillar/finding_code
        # off the linked Recommendation for each row's attribution, which would
        # otherwise be one extra query per task.
        actions = actions.select_related("recommendation")

        # Materialize the page once: the prompt-link index needs to read each
        # row's evidence before serialization, and re-evaluating the queryset
        # would run the whole query a second time.
        page = list(bounded_slice(request, actions.distinct()))
        serializer = UserActionSerializer(
            page,
            many=True,
            context={"prompt_track_index": prompt_track_index(page)},
        )
        return Response(serializer.data)

class CreateUserActionView(APIView):
    """Create a new user action"""

    permission_classes = [AllowAny]

    def post(self, request):
        from core.auth.identity import resolve_request_email

        from ..access import caller_owns_run

        email, err = resolve_request_email(request)
        if err:
            return err

        serializer = CreateUserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Get gamification profile
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email, defaults={"user_email": email}
        )

        # Determine points value
        points = ACTION_TEMPLATES.get(data["action_type"], {}).get("points", 10)

        # Get related objects if provided
        recommendation = None
        if data.get("recommendation_id"):
            # select_related: the scope check below reads .analysis_run.
            recommendation = (
                Recommendation.objects.select_related("analysis_run")
                .filter(pk=data["recommendation_id"])
                .first()
            )

        # Both ids were taken verbatim, so an action could be pinned to any run or
        # finding by integer id. Silently dropping an out-of-scope reference keeps
        # the existing "unknown id is ignored" contract rather than leaking whether
        # the row exists.
        analysis_run = None
        if data.get("analysis_run_id"):
            candidate = AnalysisRun.objects.filter(pk=data["analysis_run_id"]).first()
            if candidate is not None and caller_owns_run(email, candidate):
                analysis_run = candidate

        if recommendation is not None and not caller_owns_run(email, recommendation.analysis_run):
            recommendation = None

        # Create the action
        action = UserAction.objects.create(
            user_email=email,
            analysis_run=analysis_run,
            recommendation=recommendation,
            action_type=data["action_type"],
            title=data.get(
                "title", ACTION_TEMPLATES.get(data["action_type"], {}).get("title", "Custom Action")
            ),
            description=data.get("description", ""),
            points_value=points,
            score_before=data.get("score_before"),
            notes=data.get("notes", ""),
            status=UserAction.ActionStatus.PENDING,
        )

        return Response(UserActionSerializer(action).data, status=status.HTTP_201_CREATED)

class UpdateUserActionView(APIView):
    """Update a user action (start, complete, verify)"""

    permission_classes = [AllowAny]

    def post(self, request, action_id):
        from core.auth.identity import resolve_request_email

        from ..access import caller_owns_action

        caller, err = resolve_request_email(request)
        if err:
            return err

        try:
            action = UserAction.objects.select_related("analysis_run").get(pk=action_id)
        except UserAction.DoesNotExist:
            return Response(
                {"error": "Action not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # There was no check here at all. ``action_id`` is a sequential integer and
        # ``email`` was read off the fetched row, so an anonymous caller could walk
        # ids and flip anyone's task to VERIFIED, write ``notes`` onto it and award
        # that account points. 404 (not 403) so a miss cannot confirm the row.
        if not caller_owns_action(caller, action):
            return Response(
                {"error": "Action not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = UpdateUserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        email = action.user_email

        # Get or create gamification
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email, defaults={"user_email": email}
        )

        # Handle status changes
        old_status = action.status
        new_status = data.get("status")

        if new_status and new_status != old_status:
            action.status = new_status

            if new_status == UserAction.ActionStatus.IN_PROGRESS and not action.started_at:
                action.started_at = timezone.now()

            elif new_status == UserAction.ActionStatus.COMPLETED:
                if not action.completed_at:
                    action.completed_at = timezone.now()
                # No points on 'completed' — points are gated on live-site
                # verification (see task_verify) so an unconfirmed "Mark complete"
                # can't inflate the score. The user verifies to earn them.

            elif new_status == UserAction.ActionStatus.VERIFIED:
                if not action.verified_at:
                    action.verified_at = timezone.now()
                # Store score improvement
                if data.get("score_after"):
                    action.score_after = data["score_after"]
                    if action.score_before:
                        action.score_improvement = data["score_after"] - action.score_before
                        gamification.total_score_improvement += action.score_improvement
                        gamification.total_actions_verified += 1
                        gamification.save()
                # Points are earned only on verification.
                gamification.add_points(action.points_value)

        # Update notes if provided
        if data.get("notes"):
            action.notes = data["notes"]

        action.save()

        # Check for new achievements
        new_achievements = gamification.check_achievements()

        return Response(
            {
                "action": UserActionSerializer(action).data,
                "gamification": UserGamificationSerializer(gamification).data,
                "new_achievements": new_achievements,
            }
        )

class VerifyActionView(APIView):
    """POST actions/<id>/verify/ — re-crawl the live page and confirm the fix.

    Trustworthy 'done': instead of taking the user's word (Mark complete), this
    re-runs the specific finding's detector against the live site. On a pass the
    task flips to VERIFIED; on a fail it stays put and returns why, so a cosmetic
    or half-done change can't masquerade as resolved.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ExpensiveThrottle]

    def post(self, request, action_id):
        from core.auth.identity import resolve_request_email

        from ..access import caller_owns_action

        caller, err = resolve_request_email(request)
        if err:
            return err

        try:
            action = UserAction.objects.select_related("recommendation", "analysis_run").get(
                pk=action_id
            )
        except UserAction.DoesNotExist:
            return Response({"error": "Action not found."}, status=status.HTTP_404_NOT_FOUND)

        # Unowned callers could previously drive this: it re-crawls a live site per
        # request under ExpensiveThrottle, so it was both a cross-tenant write and
        # an amplification vector pointed at someone else's domain.
        if not caller_owns_action(caller, action):
            return Response({"error": "Action not found."}, status=status.HTTP_404_NOT_FOUND)

        from ..task_verify import verify_action

        result = verify_action(action)
        return Response(
            {
                "verified": bool(result.get("verified")),
                "message": result.get("message", ""),
                "status": action.status,
                "verified_at": action.verified_at.isoformat() if action.verified_at else None,
            }
        )

class SyncActionsView(APIView):
    """POST actions/sync/ {email, org_id} → materialize the org's latest-run
    recommendations into UserAction tasks (idempotent). This is what makes tasks
    'auto' — no manual 'start this action' step."""

    permission_classes = [AllowAny]

    def post(self, request):
        from apps.accounts.agency_utils import get_agency_context
        from core.auth.identity import resolve_request_email

        # The ownership check below is sound; its input was not. Reading the email
        # from the body compared attacker input to attacker input, so claiming the
        # victim's address passed it.
        email, err = resolve_request_email(request)
        if err:
            return err
        org_id = request.data.get("org_id")
        if not org_id:
            return Response(
                {"error": "org_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ctx = get_agency_context(email)
        owner_email = ctx.agency_email if ctx else email
        if not Organization.objects.filter(id=org_id, owner_email=owner_email).exists():
            return Response(
                {"detail": "Brand not found for this account.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Latest run that actually produced recommendations (a newer run may have
        # failed or be mid-flight with none yet).
        run = (
            AnalysisRun.objects.filter(organization_id=org_id, recommendations__isnull=False)
            .order_by("-created_at")
            .distinct()
            .first()
        )
        if run is None:
            return Response({"created": 0, "total": 0})

        from ..action_sync import materialize_run_actions

        created, total = materialize_run_actions(run, owner_email)
        return Response({"created": created, "total": total})

class AssignActionView(APIView):
    """POST actions/<id>/assign/ {email, assignee_email} → admin assigns a task
    to an agency teammate ('' = unassign)."""

    permission_classes = [AllowAny]

    def post(self, request, action_id):
        from apps.accounts.agency_utils import agency_org_ids, get_agency_context
        from apps.accounts.models import AgencyMembership
        from core.auth.identity import resolve_request_email

        # Same shape as SyncActions: the admin check was real, but the identity it
        # checked came from the body — so anyone could name the agency admin's
        # address and become the agency admin.
        email, err = resolve_request_email(request)
        if err:
            return err
        assignee = (request.data.get("assignee_email") or "").lower().strip()

        ctx = get_agency_context(email)
        if ctx is None or not ctx.is_admin:
            return Response(
                {"detail": "Only an agency admin can assign tasks.", "code": "forbidden"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            action = UserAction.objects.select_related("analysis_run").get(pk=action_id)
        except UserAction.DoesNotExist:
            return Response({"error": "Action not found."}, status=status.HTTP_404_NOT_FOUND)

        org_ids = agency_org_ids(ctx.agency_email)
        if not action.analysis_run or action.analysis_run.organization_id not in org_ids:
            return Response(
                {"detail": "This task is not in your agency.", "code": "forbidden"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if assignee:
            is_team = (
                assignee == ctx.agency_email
                or AgencyMembership.objects.filter(
                    agency_email=ctx.agency_email,
                    member_email=assignee,
                    status=AgencyMembership.Status.ACTIVE,
                ).exists()
            )
            if not is_team:
                return Response(
                    {"detail": "That person is not on your team.", "code": "not_member"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        action.assignee_email = assignee
        action.save(update_fields=["assignee_email"])
        return Response(UserActionSerializer(action).data)

class ActionStatsView(APIView):
    """Get action statistics for a user"""

    permission_classes = [AllowAny]

    def get(self, request):
        from core.auth.identity import resolve_request_email

        email, err = resolve_request_email(request)
        if err:
            return err

        # Already scoped by user_email, so run_id only narrows within the caller's
        # own rows — it cannot reach another account's actions.
        actions = UserAction.objects.filter(user_email=email)

        run_id = request.query_params.get("run_id")
        if run_id:
            actions = actions.filter(analysis_run_id=run_id)

        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email, defaults={"user_email": email}
        )

        # Get recent achievements
        recent_achievements = [
            {**ACHIEVEMENTS_INFO.get(code, {}), "code": code}
            for code in gamification.achievements[-5:]
            if code in ACHIEVEMENTS_INFO
        ]

        # One aggregate for the five status totals — was 5 round trips.
        from django.db.models import Count, Q

        _c = actions.aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(status=UserAction.ActionStatus.PENDING)),
            in_progress=Count("id", filter=Q(status=UserAction.ActionStatus.IN_PROGRESS)),
            completed=Count("id", filter=Q(status=UserAction.ActionStatus.COMPLETED)),
            verified=Count("id", filter=Q(status=UserAction.ActionStatus.VERIFIED)),
        )
        stats = {
            "total_actions": _c["total"],
            "pending_actions": _c["pending"],
            "in_progress_actions": _c["in_progress"],
            "completed_actions": _c["completed"],
            "verified_actions": _c["verified"],
            "total_points": gamification.total_points,
            "points_this_week": gamification.points_this_week,
            "current_streak": gamification.current_streak,
            "level": gamification.level,
            "level_name": gamification.get_level_display(),
            "level_progress": gamification.level_progress,
            "recent_achievements": recent_achievements,
        }

        return Response(stats)

class QuickActionView(APIView):
    """Quick action - create action from recommendation"""

    permission_classes = [AllowAny]

    def post(self, request):
        email, err = resolve_request_email(request)
        if err is not None:
            return err
        recommendation_id = request.data.get("recommendation_id")

        if not recommendation_id:
            return Response(
                {"error": "Recommendation ID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            recommendation = Recommendation.objects.get(pk=recommendation_id)
        except Recommendation.DoesNotExist:
            return Response(
                {"error": "Recommendation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # A recommendation id is guessable; check the caller owns its run.
        from ..access import caller_owns_run

        analysis_run = recommendation.analysis_run
        if not caller_owns_run(email, analysis_run):
            return Response(
                {"error": "Recommendation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        score_before = analysis_run.composite_score if analysis_run else None

        # Map recommendation category to action type
        action_type_map = {
            "schema": "add_schema",
            "technical": "add_robots",
            "eeat": "add_author",
            "entity": "post_reddit",
            "content": "add_faq",
            "ai_visibility": "post_reddit",
        }

        action_type = action_type_map.get(recommendation.category, "add_faq")

        # Get gamification
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email, defaults={"user_email": email}
        )
        points = ACTION_TEMPLATES.get(action_type, {}).get("points", 10)

        # Create action
        action = UserAction.objects.create(
            user_email=email,
            action_type=action_type,
            title=recommendation.title,
            description=recommendation.description,
            points_value=points,
            status="pending",
            score_before=score_before,
            recommendation=recommendation,
            analysis_run=analysis_run,
        )

        serializer = UserActionSerializer(action)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class BulkCreateUserActionView(APIView):
    """Bulk create actions from recommendations"""

    permission_classes = [AllowAny]

    def post(self, request):
        email, err = resolve_request_email(request)
        if err is not None:
            return err
        recommendations_data = request.data.get("recommendations", [])

        if not isinstance(recommendations_data, list):
            return Response(
                {"error": "Recommendations must be a list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(recommendations_data) > MAX_BULK_RECOMMENDATIONS:
            return Response(
                {"error": f"At most {MAX_BULK_RECOMMENDATIONS} recommendations per request."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not recommendations_data:
            return Response(
                {"error": "Recommendations list is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get gamification
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email, defaults={"user_email": email}
        )

        # Priority to points mapping
        priority_points = {
            "critical": 50,
            "high": 30,
            "medium": 20,
            "low": 10,
        }

        # Category to action type mapping
        action_type_map = {
            "schema": "add_schema",
            "technical": "add_robots",
            "eeat": "add_author",
            "entity": "post_reddit",
            "content": "add_faq",
            "ai_visibility": "post_reddit",
        }

        created_actions = []

        for rec_data in recommendations_data:
            rec_id = rec_data.get("id")
            title = rec_data.get("title", "")
            action_text = rec_data.get("action", "")
            priority = rec_data.get("priority", "medium")
            analysis_run_id = rec_data.get("analysis_run_id")

            # Only check for duplicates if we're scanning the SAME website again
            # Check by analysis_run_id + recommendation combination
            if rec_id and analysis_run_id:
                existing = UserAction.objects.filter(
                    recommendation_id=rec_id, analysis_run_id=analysis_run_id, user_email=email
                ).first()
                if existing:
                    continue

            # Get recommendation from DB if it exists
            recommendation = None
            analysis_run = None
            score_before = None

            if rec_id:
                try:
                    recommendation = Recommendation.objects.get(pk=rec_id)
                    analysis_run = recommendation.analysis_run
                    score_before = analysis_run.composite_score if analysis_run else None
                except Recommendation.DoesNotExist:
                    pass

            # Determine action type from category
            action_type = "add_faq"
            if recommendation:
                action_type = action_type_map.get(recommendation.category, "add_faq")
            else:
                # Try to determine from title/description
                if "schema" in title.lower():
                    action_type = "add_schema"
                elif "robot" in title.lower():
                    action_type = "add_robots"
                elif "author" in title.lower() or "e-e-a-t" in title.lower():
                    action_type = "add_author"
                elif "reddit" in title.lower():
                    action_type = "post_reddit"

            points = priority_points.get(priority, 10)

            action = UserAction.objects.create(
                user_email=email,
                action_type=action_type,
                title=title,
                description=action_text,
                points_value=points,
                status="pending",
                score_before=score_before,
                recommendation=recommendation,
                analysis_run=analysis_run,
            )
            created_actions.append(action)

        serializer = UserActionSerializer(created_actions, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
