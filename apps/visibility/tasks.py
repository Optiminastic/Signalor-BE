import logging
import threading

from .models import VisibilityCheck
from .pipeline.google_check import check_google
from .pipeline.reddit_check import check_reddit

logger = logging.getLogger("apps")


def _update_status(check: VisibilityCheck, status: str, progress: int = 0):
    check.status = status
    check.progress = progress
    check.save(update_fields=["status", "progress", "updated_at"])


def run_visibility_check(check_id: int):
    """Full visibility check pipeline — runs sequentially to avoid rate limits."""
    try:
        check = VisibilityCheck.objects.get(pk=check_id)
    except VisibilityCheck.DoesNotExist:
        logger.error("VisibilityCheck %d not found", check_id)
        return

    try:
        brand_name = check.brand_name
        brand_url = check.brand_url

        # Phase 1: Google (10-50%)
        _update_status(check, VisibilityCheck.Status.CHECKING_GOOGLE, 10)
        try:
            google_score, google_details = check_google(brand_name, brand_url)
        except Exception as exc:
            logger.warning("Google check failed for %d: %s", check_id, exc)
            google_score, google_details = 0.0, {"error": str(exc)}

        check.google_score = google_score
        check.google_details = google_details
        check.progress = 50
        check.save(update_fields=[
            "google_score", "google_details", "progress", "updated_at",
        ])

        # Phase 2: Reddit (55-85%)
        _update_status(check, VisibilityCheck.Status.CHECKING_REDDIT, 55)
        try:
            reddit_score, reddit_details = check_reddit(brand_name)
        except Exception as exc:
            logger.warning("Reddit check failed for %d: %s", check_id, exc)
            reddit_score, reddit_details = 0.0, {"error": str(exc)}

        check.reddit_score = reddit_score
        check.reddit_details = reddit_details
        check.progress = 85
        check.save(update_fields=[
            "reddit_score", "reddit_details", "progress", "updated_at",
        ])

        # Phase 3: Overall scoring (90-100%)
        _update_status(check, VisibilityCheck.Status.SCORING, 90)

        # Weighted: Google 62%, Reddit 38%
        overall = (
            (google_score or 0) * 0.62
            + (reddit_score or 0) * 0.38
        )

        check.overall_score = round(overall, 1)
        check.status = VisibilityCheck.Status.COMPLETE
        check.progress = 100
        check.save()
        logger.info(
            "Visibility check complete for %d (%s): score %.1f",
            check_id, brand_name, overall,
        )

    except Exception as exc:
        logger.error("Visibility check failed for %d: %s", check_id, exc, exc_info=True)
        check.status = VisibilityCheck.Status.FAILED
        check.error_message = str(exc)
        check.save()


def start_visibility_task(check_id: int):
    """Start the visibility check in a background thread."""
    try:
        VisibilityCheck.objects.get(pk=check_id)
    except VisibilityCheck.DoesNotExist:
        return

    thread = threading.Thread(
        target=run_visibility_check, args=(check_id,), daemon=True
    )
    thread.start()
