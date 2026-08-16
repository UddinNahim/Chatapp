from django.db import models


class Room(models.Model):
    DIRECT = "direct"
    GROUP = "group"
    KIND_CHOICES = [(DIRECT, "direct"), (GROUP, "group")]

    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    title = models.CharField(max_length=120, blank=True)
    pair_key = models.CharField(max_length=32, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title or f"{self.kind}:{self.pk}"


class RoomMember(models.Model):
    room = models.ForeignKey(Room, related_name="memberships", on_delete=models.CASCADE)
    user_id = models.IntegerField()
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("room", "user_id")]
        indexes = [models.Index(fields=["user_id"])]


class ChatMessage(models.Model):
    room = models.ForeignKey(Room, related_name="messages", on_delete=models.CASCADE)
    sender_id = models.IntegerField()
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["room", "created_at"])]


class MessageReceipt(models.Model):
    message = models.ForeignKey(ChatMessage, related_name="receipts", on_delete=models.CASCADE)
    user_id = models.IntegerField()
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("message", "user_id")]
        indexes = [models.Index(fields=["user_id", "read_at"])]
