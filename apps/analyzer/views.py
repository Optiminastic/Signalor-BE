import logging

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Organization

from .models import (
    AnalysisRun,
    Recommendation,
    UserAction,
    UserGamification,
    ACHIEVEMENTS_INFO,
    ACTION_TEMPLATES,
)
from .serializers import (
    AnalysisRunDetailSerializer,
    AnalysisRunListSerializer,
    StartAnalysisSerializer,
    UserActionSerializer,
    UserGamificationSerializer,
    CreateUserActionSerializer,
    UpdateUserActionSerializer,
    ActionTemplateSerializer,
    ActionStatsSerializer,
)
from .tasks import start_analysis_task

logger = logging.getLogger("apps")


class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []  

    def get(self, request):
        from django.db import connection
        from django.conf import settings
        
        health_status = {
            "status": "healthy",
            "service": "geo-be",
            "timestamp": timezone.now().isoformat(),
        }
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            health_status["database"] = "connected"
        except Exception as e:
            health_status["status"] = "unhealthy"
            health_status["database"] = f"error: {str(e)}"
            return Response(health_status, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            from django.core.cache import cache
            cache.set("health_check", "ok", 10)
            cache_value = cache.get("health_check")
            health_status["cache"] = "connected" if cache_value == "ok" else "degraded"
        except Exception as e:
            health_status["cache"] = f"error: {str(e)}"
        
        return Response(health_status, status=status.HTTP_200_OK)

class StartAnalysisView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = StartAnalysisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        email = data.get("email", "")

        # Try to link to organization
        org = None
        if email:
            org = Organization.objects.filter(owner_email=email).first()

        run = AnalysisRun.objects.create(
            organization=org,
            url=data["url"],
            brand_name=data.get("brand_name", ""),
            email=email,
            run_type=data["run_type"],
            status=AnalysisRun.Status.PENDING,
        )

        # Start background task
        start_analysis_task(run.id)

        return Response(
            {
                "id": run.id,
                "url": run.url,
                "status": run.status,
                "message": "Analysis started",
            },
            status=status.HTTP_201_CREATED,
        )


class AnalysisRunListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        runs = AnalysisRun.objects.filter(email=email)
        serializer = AnalysisRunListSerializer(runs, many=True)
        return Response(serializer.data)


class AnalysisRunDetailView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []  # frequently loaded by analyzer pages

    def get(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AnalysisRunDetailSerializer(run)
        return Response(serializer.data)


class AnalysisRunStatusView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []  # No throttling — this is a polling endpoint

    def get(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "id": run.id,
                "status": run.status,
                "progress": run.progress,
                "composite_score": run.composite_score,
            }
        )


class ExportPDFView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, run_id):
        try:
            run = AnalysisRun.objects.get(pk=run_id)
        except AnalysisRun.DoesNotExist:
            return Response(
                {"error": "Analysis run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if run.status != AnalysisRun.Status.COMPLETE:
            return Response(
                {"error": "Analysis must be complete before exporting."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from django.template.loader import render_to_string
            from weasyprint import HTML
            import io

            main_page = run.page_scores.filter(url=run.url).first()
            recommendations = run.recommendations.all()
            competitors = run.competitors.filter(scored=True)

            context = {
                "run": run,
                "main_page": main_page,
                "recommendations": recommendations,
                "competitors": competitors,
                "ai_probes": run.ai_probes.all(),
            }

            html_string = render_to_string("analyzer/report.html", context)
            
            # Generate PDF with WeasyPrint
            pdf_file = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()

            response = HttpResponse(pdf_file, content_type="application/pdf")
            response["Content-Disposition"] = (
                f'attachment; filename="geo-analysis-{run.id}.pdf"'
            )
            return response

        except ImportError:
            return Response(
                {"error": "PDF export requires weasyprint package."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )


# ============ Gamification Views ============

class UserGamificationView(APIView):
    """Get user gamification profile"""
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        gamification, created = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
        )

        serializer = UserGamificationSerializer(gamification)
        return Response(serializer.data)


class ActionTemplatesView(APIView):
    """Get available action templates"""
    permission_classes = [AllowAny]

    def get(self, request):
        templates = [
            {**template, "action_type": key}
            for key, template in ACTION_TEMPLATES.items()
        ]
        return Response(templates)


class AchievementsView(APIView):
    """Get all possible achievements"""
    permission_classes = [AllowAny]

    def get(self, request):
        achievements = [
            {**info, "code": key}
            for key, info in ACHIEVEMENTS_INFO.items()
        ]
        return Response(achievements)


class UserActionListView(APIView):
    """List user's actions"""
    permission_classes = [AllowAny]
    throttle_classes = []  # used by sidebar/actions dashboard refreshes

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        status_filter = request.query_params.get("status")
        
        actions = UserAction.objects.filter(user_email=email)
        
        if status_filter:
            actions = actions.filter(status=status_filter)
        
        serializer = UserActionSerializer(actions, many=True)
        return Response(serializer.data)


class CreateUserActionView(APIView):
    """Create a new user action"""
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = CreateUserActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Get gamification profile
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
        )

        # Determine points value
        points = ACTION_TEMPLATES.get(data["action_type"], {}).get("points", 10)

        # Get related objects if provided
        recommendation = None
        if data.get("recommendation_id"):
            try:
                recommendation = Recommendation.objects.get(
                    pk=data["recommendation_id"]
                )
            except Recommendation.DoesNotExist:
                pass

        analysis_run = None
        if data.get("analysis_run_id"):
            try:
                analysis_run = AnalysisRun.objects.get(
                    pk=data["analysis_run_id"]
                )
            except AnalysisRun.DoesNotExist:
                pass

        # Create the action
        action = UserAction.objects.create(
            user_email=email,
            analysis_run=analysis_run,
            recommendation=recommendation,
            action_type=data["action_type"],
            title=data.get("title", ACTION_TEMPLATES.get(data["action_type"], {}).get("title", "Custom Action")),
            description=data.get("description", ""),
            points_value=points,
            score_before=data.get("score_before"),
            notes=data.get("notes", ""),
            status=UserAction.ActionStatus.PENDING,
        )

        return Response(
            UserActionSerializer(action).data,
            status=status.HTTP_201_CREATED
        )


class UpdateUserActionView(APIView):
    """Update a user action (start, complete, verify)"""
    permission_classes = [AllowAny]

    def post(self, request, action_id):
        try:
            action = UserAction.objects.get(pk=action_id)
        except UserAction.DoesNotExist:
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
            user_email=email,
            defaults={"user_email": email}
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
                # Award points for completion
                gamification.add_points(action.points_value)

            elif new_status == UserAction.ActionStatus.VERIFIED:
                if not action.verified_at:
                    action.verified_at = timezone.now()
                # Store score improvement
                if data.get("score_after"):
                    action.score_after = data["score_after"]
                    if action.score_before:
                        action.score_improvement = data["score_after"] - action.score_before
                        gamification.total_score_improvement += action
                        gamification.score_improvement.total_actions_verified += 1
                        gamification.save()
                # Award bonus points for verification
                gamification.add_points(action.points_value // 2)

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


class ActionStatsView(APIView):
    """Get action statistics for a user"""
    permission_classes = [AllowAny]

    def get(self, request):
        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        actions = UserAction.objects.filter(user_email=email)
        
        run_id = request.query_params.get("run_id")
        if run_id:
            actions = actions.filter(analysis_run_id=run_id)
        
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
        )

        # Get recent achievements
        recent_achievements = [
            {**ACHIEVEMENTS_INFO.get(code, {}), "code": code}
            for code in gamification.achievements[-5:]
            if code in ACHIEVEMENTS_INFO
        ]

        stats = {
            "total_actions": actions.count(),
            "pending_actions": actions.filter(status=UserAction.ActionStatus.PENDING).count(),
            "in_progress_actions": actions.filter(status=UserAction.ActionStatus.IN_PROGRESS).count(),
            "completed_actions": actions.filter(status=UserAction.ActionStatus.COMPLETED).count(),
            "verified_actions": actions.filter(status=UserAction.ActionStatus.VERIFIED).count(),
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
        email = request.data.get("email", "").lower().strip()
        recommendation_id = request.data.get("recommendation_id")
        
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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

        # Get the analysis run to get the score
        analysis_run = recommendation.analysis_run
        score_before = analysis_run.composite_score if analysis_run else None

        # Map recommendation category to action type
        action_type_map = {
            "schema": "add_schema",
            "technical": "add_robots",
            "eeat": "add_author",
            "entity": "post_reddit",
            "content": "add_faq",
            "ai_visibility": "post_medium",
        }

        action_type = action_type_map.get(recommendation.category, "add_faq")
        
        # Get gamification
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
        )
        points = ACTION_TEMPLATES.get(action_type, {}).get("points", 10)

        # Create action
        action = UserAction.objects.create(
            user_email=email,
            action_type=action_type,
            title=recommendation.title,
            description=recommendation.description,
            action=recommendation.action,
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
        email = request.data.get("email", "").lower().strip()
        recommendations_data = request.data.get("recommendations", [])
        
        if not email:
            return Response(
                {"error": "Email parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not recommendations_data:
            return Response(
                {"error": "Recommendations list is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get gamification
        gamification, _ = UserGamification.objects.get_or_create(
            user_email=email,
            defaults={"user_email": email}
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
            "ai_visibility": "post_medium",
        }

        created_actions = []
        
        for rec_data in recommendations_data:
            rec_id = rec_data.get("id")
            title = rec_data.get("title", "")
            description = rec_data.get("description", "")
            action_text = rec_data.get("action", "")
            priority = rec_data.get("priority", "medium")
            analysis_run_id = rec_data.get("analysis_run_id")
            
            # Only check for duplicates if we're scanning the SAME website again
            # Check by analysis_run_id + recommendation combination
            if rec_id and analysis_run_id:
                existing = UserAction.objects.filter(
                    recommendation_id=rec_id,
                    analysis_run_id=analysis_run_id,
                    user_email=email
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
                elif "reddit" in title.lower() or "medium" in title.lower():
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
