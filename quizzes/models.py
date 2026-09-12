from django.db import models
from django.conf import settings
from content.models import Topic

class Question(models.Model):
    TYPE_SINGLE_CHOICE = 'single'
    TYPE_MULTIPLE_CHOICE = 'multiple'
    TYPE_TRUE_FALSE = 'true_false'
    TYPE_SHORT_ANSWER = 'short_answer'
    TYPE_PARAGRAPH = 'paragraph'

    QUESTION_TYPE_CHOICES = [
        (TYPE_SINGLE_CHOICE, 'Single Choice'),
        (TYPE_MULTIPLE_CHOICE, 'Multiple Choice'),
        (TYPE_TRUE_FALSE, 'True / False'),
        (TYPE_SHORT_ANSWER, 'Short Answer'),
        (TYPE_PARAGRAPH, 'Paragraph'),
    ]

    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='questions')
    text = models.TextField(help_text="Question text")
    question_type = models.CharField(
        max_length=20,
        choices=QUESTION_TYPE_CHOICES,
        default=TYPE_SINGLE_CHOICE
    )
    required = models.BooleanField(default=True)
    accepted_answers = models.TextField(
        blank=True,
        default='',
        help_text="For Short Answer questions: list accepted answers, one per line."
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f"[{self.get_question_type_display()}] Q{self.order}: {self.text[:50]}"

    def get_accepted_answers_list(self):
        """Returns list of clean, non-empty accepted answer strings."""
        if not self.accepted_answers:
            return []
        return [line.strip() for line in self.accepted_answers.splitlines() if line.strip()]

    @property
    def is_gradable(self):
        """Paragraph questions are open-ended and not automatically graded."""
        return self.question_type != self.TYPE_PARAGRAPH


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False, help_text="Mark True if this is a correct answer")

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


class QuizResponse(models.Model):
    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name='responses')
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name='responses')
    selected_choices = models.ManyToManyField(Choice, blank=True, related_name='responses')
    text_response = models.TextField(blank=True, default='')
    is_correct = models.BooleanField(null=True, blank=True, help_text="Null for ungraded/paragraph questions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['question__order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['attempt', 'question'], name='unique_attempt_question_response')
        ]

    def __str__(self):
        status = 'Correct' if self.is_correct is True else 'Incorrect' if self.is_correct is False else 'Ungraded/Pending'
        return f"Attempt #{self.attempt.attempt_number} Q{self.question.id} ({status})"
