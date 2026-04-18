from django.db import models


class VisibilityCheck(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        CHECKING_GOOGLE = "checking_google"
        CHECKING_REDDIT = "checking_reddit"
        SCORING = "scoring"
        COMPLETE = "complete"
        FAILED = "failed"

    brand_name = models.CharField(max_length=255)
    brand_url = models.URLField(max_length=2048)
    email = models.EmailField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    progress = models.IntegerField(default=0)

    # Google
    google_score = models.FloatField(null=True, blank=True)
    google_details = models.JSONField(default=dict, blank=True)

    # Reddit
    reddit_score = models.FloatField(null=True, blank=True)
    reddit_details = models.JSONField(default=dict, blank=True)

    # Overall
    overall_score = models.FloatField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.brand_name} ({self.status})"
