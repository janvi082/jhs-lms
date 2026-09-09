from django.db import models
from django.conf import settings
from content.models import Topic

class TopicProgress(models.Model):
    STATUS_NOT_STARTED = 'not_started'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_COMPLETED = 'completed'

    STATUS_CHOICES = [
        (STATUS_NOT_STARTED, 'Not Started'),
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='topic_progresses')
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='topic_progresses')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NOT_STARTED)
    best_score = models.PositiveIntegerField(default=0)
    latest_score = models.PositiveIntegerField(default=0)
    attempts_count = models.PositiveIntegerField(default=0)
    last_accessed = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'topic')
        verbose_name_plural = "Topic Progresses"

    def __str__(self):
        return f"{self.user.username} - {self.topic.name}: {self.get_status_display()} (Best: {self.best_score}%)"
