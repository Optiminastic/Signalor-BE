from django.db import models


class PricingDripState(models.Model):
    """One row per email-identified user enrolled in the pricing-dropoff drip.

    Lifecycle:
    - Row created when the FE reports `pricing_page_viewed` for an email that
      has not already started checkout. `entered_at` = creation time.
    - `current_step` advances 0 -> 1 -> 2 -> 3 -> 4 as each email is sent.
    - `suppressed=True` is set the moment `checkout_started` or
      `purchase_completed` fires; the cron skips suppressed rows forever.
    """

    email = models.EmailField(unique=True, db_index=True)
    amplitude_user_id = models.CharField(max_length=64, blank=True, default="")

    # Merge-tag fields refreshed on every pricing_page_viewed ping so the
    # email body always renders with the user's latest known state.
    first_name = models.CharField(max_length=120, blank=True, default="")
    domain = models.CharField(max_length=255, blank=True, default="")
    geo_score = models.FloatField(null=True, blank=True)
    fix_count = models.IntegerField(null=True, blank=True)
    top_competitor = models.CharField(max_length=255, blank=True, default="")
    competitor_list = models.TextField(blank=True, default="")
    cms_platform = models.CharField(max_length=32, blank=True, default="")
    top_recommendation_title = models.CharField(max_length=512, blank=True, default="")
    issue_count = models.IntegerField(null=True, blank=True)
    competitor_count = models.IntegerField(null=True, blank=True)

    entered_at = models.DateTimeField(auto_now_add=True)
    current_step = models.IntegerField(default=0)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    # Consecutive send failures since last successful send. Resets to 0 once a
    # send goes through. Hitting MAX_SEND_FAILURES auto-suppresses the row.
    failure_count = models.IntegerField(default=0)

    suppressed = models.BooleanField(default=False)
    suppressed_reason = models.CharField(max_length=32, blank=True, default="")
    suppressed_at = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-entered_at"]
        indexes = [
            models.Index(fields=["suppressed", "current_step"]),
        ]

    def __str__(self) -> str:
        return f"{self.email} (step={self.current_step}, suppressed={self.suppressed})"


class DripSendLog(models.Model):
    """Audit trail of every email actually dispatched, so we can answer 'did
    this user get email N?' without tailing SendGrid logs."""

    state = models.ForeignKey(PricingDripState, on_delete=models.CASCADE, related_name="sends")
    step = models.IntegerField()
    subject_variant = models.CharField(max_length=4)
    subject = models.CharField(max_length=998)
    sent_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-sent_at"]
        indexes = [
            models.Index(fields=["state", "step"]),
        ]
        constraints = [
            # At most one *successful* log row per (state, step). Failed
            # attempts are unconstrained so the cron can retry. This is the
            # DB-side guarantee against the rare race where the SMTP send
            # returns 250 but the Python wrapper raises afterwards.
            models.UniqueConstraint(
                fields=["state", "step"],
                condition=models.Q(success=True),
                name="drip_one_successful_send_per_step",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.state.email} step={self.step} variant={self.subject_variant}"
