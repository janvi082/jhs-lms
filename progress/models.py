import uuid
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

    PROVENANCE_SYSTEM_CAPTURED = 'system_captured'
    PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED = 'retrospectively_reconstructed'

    PROVENANCE_CHOICES = [
        (PROVENANCE_SYSTEM_CAPTURED, 'System Captured'),
        (PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED, 'Retrospectively Reconstructed'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='topic_progresses')
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='topic_progresses')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NOT_STARTED)
    best_score = models.PositiveIntegerField(default=0)
    latest_score = models.PositiveIntegerField(default=0)
    attempts_count = models.PositiveIntegerField(default=0)
    last_accessed = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expected_end_day_snapshot = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Historical Learning Phase benchmark captured when this record was first created."
    )
    frozen_deadline = models.DateField(
        null=True,
        blank=True,
        help_text="Benchmark nominal deadline frozen when topic completion is first recorded."
    )
    timing_provenance = models.CharField(
        max_length=32,
        choices=PROVENANCE_CHOICES,
        null=True,
        blank=True,
        help_text="Provenance of the frozen deadline benchmark."
    )

    class Meta:
        unique_together = ('user', 'topic')
        verbose_name_plural = "Topic Progresses"

    def __str__(self):
        return f"{self.user.username} - {self.topic.name}: {self.get_status_display()} (Best: {self.best_score}%)"


class TopicProgressTimingAudit(models.Model):
    ACTION_HISTORICAL_RECONSTRUCTION = 'historical_reconstruction'
    ACTION_ROLLBACK = 'rollback'
    ACTION_CORRECTION = 'correction'

    ACTION_CHOICES = [
        (ACTION_HISTORICAL_RECONSTRUCTION, 'Historical Reconstruction'),
        (ACTION_ROLLBACK, 'Rollback'),
        (ACTION_CORRECTION, 'Correction'),
    ]

    topic_progress = models.ForeignKey(
        TopicProgress,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timing_audits',
        help_text="Reference to affected TopicProgress record, set to null if the record is deleted."
    )
    original_topic_progress_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Permanent ID of the TopicProgress record, preserved even if the record is deleted."
    )
    reverted_audit_entry = models.OneToOneField(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rollback_entry',
        help_text="Reference to the exact original audit entry that this rollback entry reverts."
    )
    repair_batch_id = models.UUIDField(
        default=uuid.uuid4,
        db_index=True,
        help_text="Identifier shared by all records in one repair or rollback operation."
    )
    action = models.CharField(
        max_length=32,
        choices=ACTION_CHOICES,
        help_text="Audit action: historical_reconstruction, rollback, or correction."
    )
    reason = models.TextField(
        help_text="Explanation and justification for the historical timing change or rollback."
    )
    before_expected_end_day_snapshot = models.PositiveIntegerField(null=True, blank=True)
    after_expected_end_day_snapshot = models.PositiveIntegerField(null=True, blank=True)
    before_frozen_deadline = models.DateField(null=True, blank=True)
    after_frozen_deadline = models.DateField(null=True, blank=True)
    before_timing_provenance = models.CharField(
        max_length=32,
        choices=TopicProgress.PROVENANCE_CHOICES,
        null=True,
        blank=True
    )
    after_timing_provenance = models.CharField(
        max_length=32,
        choices=TopicProgress.PROVENANCE_CHOICES,
        null=True,
        blank=True
    )
    before_status = models.CharField(
        max_length=20,
        choices=TopicProgress.STATUS_CHOICES,
        null=True,
        blank=True
    )
    after_status = models.CharField(
        max_length=20,
        choices=TopicProgress.STATUS_CHOICES,
        null=True,
        blank=True
    )
    before_completed_at = models.DateTimeField(null=True, blank=True)
    after_completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timing_audits_initiated',
        help_text="Authenticated user/admin who initiated the operation, if available."
    )

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = "Topic Progress Timing Audit"
        verbose_name_plural = "Topic Progress Timing Audits"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("TopicProgressTimingAudit entries are append-only and cannot be modified.")
        if self.topic_progress_id and not self.original_topic_progress_id:
            self.original_topic_progress_id = self.topic_progress_id
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("TopicProgressTimingAudit entries are append-only and cannot be deleted.")

    def __str__(self):
        tp_id = self.original_topic_progress_id or (self.topic_progress_id if self.topic_progress else 'unknown')
        return f"Audit [{self.get_action_display()}] TopicProgress #{tp_id} (Batch {self.repair_batch_id})"
