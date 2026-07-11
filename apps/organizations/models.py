import secrets

from django.db import models

from .utils import normalize_url


def generate_org_slug() -> str:
    """High-entropy, URL-safe brand slug (~22 chars / 128 bits).

    Random rather than name-derived so brand dashboard URLs can't be guessed or
    enumerated. Data is still email-scoped server-side, so a leaked slug alone
    exposes nothing.
    """
    return secrets.token_urlsafe(16)


class Organization(models.Model):
    name = models.CharField(max_length=255)
    url = models.URLField(blank=True, default="")
    # Unguessable public identifier used in the dashboard URL (/dashboard/<slug>).
    slug = models.CharField(max_length=32, unique=True, blank=True, default="", db_index=True)
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
        if not self.slug:
            candidate = generate_org_slug()
            while Organization.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = generate_org_slug()
            self.slug = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.owner_email})"
