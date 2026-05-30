from django.db import models

from .utils import normalize_url


class Organization(models.Model):
    name = models.CharField(max_length=255)
    url = models.URLField(blank=True, default="")
    # Canonicalized host derived from ``url`` (no scheme, no www, no path).
    # Used to dedupe org creation per (owner_email, normalized_url) without
    # being fooled by trivial URL variants. Maintained by .save() — never
    # set this field directly.
    normalized_url = models.CharField(max_length=255, blank=True, default="", db_index=True)
    owner_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner_email"]),
            models.Index(fields=["owner_email", "normalized_url"]),
        ]

    def save(self, *args, **kwargs):
        # Keep normalized_url in sync with url on every write. Doing it here
        # (rather than the serializer) means admin edits and shell tweaks
        # also stay consistent.
        self.normalized_url = normalize_url(self.url or "")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.owner_email})"
