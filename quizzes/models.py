from django.db import models
from django.conf import settings
from content.models import Topic

class Question(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField(help_text="Question text")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f"Q{self.order}: {self.text[:50]}"

class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False, help_text="Mark True if this is the single correct answer")

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"{self.text} {'(Correct)' if self.is_correct else ''}"

class QuizAttempt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quiz_attempts')
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='quiz_attempts')
    attempt_number = models.PositiveIntegerField()
    correct_count = models.PositiveIntegerField()
    total_questions = models.PositiveIntegerField()
    score = models.PositiveIntegerField(help_text="Percentage score (0-100)")
    passed = models.BooleanField()
    passing_score_used = models.PositiveIntegerField(help_text="Snapshot of the threshold percentage applied at attempt time")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.topic.name} Attempt #{self.attempt_number} ({self.score}% - {'PASS' if self.passed else 'FAIL'})"
