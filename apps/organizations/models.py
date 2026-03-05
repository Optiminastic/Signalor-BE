from django.db import models


class Organization(models.Model):
    name = models.CharField(max_length=255)
    url = models.URLField(blank=True, default="")
    owner_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner_email"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.owner_email})"
