from django.db import models


class Instructor(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self) -> str:
        return self.name


class Student(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self) -> str:
        return self.name


class Post(models.Model):
    # Post always instructor er. Student post create korte pare na.
    instructor = models.ForeignKey(Instructor, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class Message(models.Model):
    """Ek instructor + ek student er 1:1 kotha."""

    instructor = models.ForeignKey(Instructor, on_delete=models.CASCADE)
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    sender = models.CharField(max_length=20)  # "instructor" or "student"
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.sender}: {self.text[:40]}"


class LmsNotification(models.Model):
    """Message gele onno joner inbox. role + person_id = ke pabe."""

    recipient_role = models.CharField(max_length=20)  # instructor | student
    recipient_id = models.IntegerField()
    from_role = models.CharField(max_length=20)
    from_name = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=300)
    channel = models.CharField(max_length=80)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.recipient_role}:{self.recipient_id} ← {self.title}"
