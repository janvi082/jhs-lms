# access/services.py
"""Permission helper functions for per‑learner access control.

Each function returns ``True`` if the user is allowed to view the given
object, ``False`` otherwise.

* Admin users (``user.is_admin_user``) bypass all checks.
* Absence of a ``LearnerAccess`` row implies allowed (default‑allow).
* Hierarchical checks inherit denial from parent objects – a denied
  ``Subject`` denies all its ``Topic``/``Video``/``Resource`` objects.
"""

from django.contrib.contenttypes.models import ContentType

from .models import LearnerAccess
from content.models import Subject, Topic, Video, Resource


def _is_allowed(user, obj) -> bool:
    """Return ``True`` if ``user`` may access ``obj``.

    Admin users are always allowed. For learners the function looks for a
    ``LearnerAccess`` entry. If none exists, access is allowed. If an entry
    exists, its ``is_allowed`` flag determines the result.
    """
    if getattr(user, "is_admin_user", False):
        return True
        
    if not hasattr(user, '_learner_access_cache'):
        # Preload all explicit denies for this learner to prevent N+1 queries
        denies = LearnerAccess.objects.filter(learner=user, is_allowed=False)
        user._learner_access_cache = set(
            (ct_id, obj_id) for ct_id, obj_id in denies.values_list('content_type_id', 'object_id')
        )
        
    ct_id = ContentType.objects.get_for_model(obj).id
    return (ct_id, obj.pk) not in user._learner_access_cache


def has_subject_access(user, subject: Subject) -> bool:
    return _is_allowed(user, subject)


def has_topic_access(user, topic: Topic) -> bool:
    # Denied if subject denied first
    if not has_subject_access(user, topic.subject):
        return False
    return _is_allowed(user, topic)


def has_video_access(user, video: Video) -> bool:
    if not has_topic_access(user, video.topic):
        return False
    return _is_allowed(user, video)


def has_resource_access(user, resource: Resource) -> bool:
    if not has_topic_access(user, resource.topic):
        return False
    return _is_allowed(user, resource)
