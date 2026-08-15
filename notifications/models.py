from django.db import models


class Notification(models.Model):
    """Per-user inbox row. user_id is a demo identity, not a User FK."""

    user_id = models.IntegerField(db_index=True)
    kind = models.CharField(max_length=32, default="dm")
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=300)
    from_user_id = models.IntegerField()
    channel = models.CharField(max_length=80)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user_id", "-created_at"]),
            models.Index(fields=["user_id", "read_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind} → {self.user_id}: {self.title}"
