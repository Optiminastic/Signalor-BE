"""Citation-gap outreach state."""

from django.db import models


class CitationOutreach(models.Model):
    """The user's outreach state for one citation-gap domain.

    Only user-set state lives here. The ranking itself (which domains win which
    prompts) is derived from ``PromptCitation`` on read, so it cannot drift out
    of sync with the prompts it came from.

    Deliberately no ``live`` choice: a domain counts as live only when the brand
    is actually found on it, verified by a search. An outreach tracker whose
    "done" column is self-reported stops reflecting reality within weeks.

    Scoped to the organization rather than a run, because outreach outlives any
    single analysis.
    """

    class Status(models.TextChoices):
        IDENTIFIED = "identified", "Identified"
        PITCHED = "pitched", "Pitched"
        DISMISSED = "dismissed", "Not pursuing"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="citation_outreach"
    )
    domain = models.CharField(max_length=255, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IDENTIFIED)
    note = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("organization", "domain")]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self):
        return f"{self.domain} ({self.status})"

