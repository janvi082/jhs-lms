from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey

User = get_user_model()

class LearnerAccess(models.Model):
    """Explicit allow/deny rule for a learner on any content object.
    Absence of a row implies access is allowed (default‑allow semantics).
    """
    learner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='access_entries',
        help_text='Learner to which this rule applies.'
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        help_text='Content type of the protected object.'
    )
    object_id = models.PositiveBigIntegerField(
        help_text='Primary key of the protected object.'
    )
    content_object = GenericForeignKey('content_type', 'object_id')

    # Only explicit deny records are needed; default is allowed.
    is_allowed = models.BooleanField(
        default=True,
        help_text='True → allow, False → explicit deny.'
    )

    class Meta:
        unique_together = ('learner', 'content_type', 'object_id')
        indexes = [
            models.Index(fields=['learner', 'content_type', 'object_id']),
        ]

    def __str__(self):
        status = 'allow' if self.is_allowed else 'deny'
        return f"{self.learner.username} – {self.content_type.model} {self.object_id} – {status}"
