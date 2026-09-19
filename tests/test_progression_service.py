from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from content.models import Subject, Topic, SiteConfig
from progress.models import TopicProgress
from progress.services import (
    is_topic_unlocked,
    get_next_topic,
    complete_zero_question_topic,
    record_topic_view,
    get_effective_passing_score,
    submit_quiz_attempt,
)
from quizzes.models import Question, QuizAttempt, Choice, QuizResponse
from access.models import LearnerAccess
from django.contrib.contenttypes.models import ContentType

User = get_user_model()

class ProgressionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='learner', password='pw', role=User.ROLE_LEARNER)
        self.admin = User.objects.create_user(username='admin', password='pw', role=User.ROLE_ADMIN, is_staff=True)

        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.save()

        self.subject1 = Subject.objects.create(name='Sub 1', slug='sub1', is_active=True, order=1)
        self.subject2 = Subject.objects.create(name='Sub 2', slug='sub2', is_active=True, order=2)

        self.topic1 = Topic.objects.create(subject=self.subject1, name='T1', order=1, is_active=True)
        self.topic2 = Topic.objects.create(subject=self.subject1, name='T2', order=2, is_active=True)
        self.topic3 = Topic.objects.create(subject=self.subject1, name='T3', order=3, is_active=True)

        self.sub2_topic1 = Topic.objects.create(subject=self.subject2, name='S2T1', order=1, is_active=True)

    def _complete_topic(self, user, topic):
        TopicProgress.objects.create(user=user, topic=topic, status=TopicProgress.STATUS_COMPLETED, best_score=100)

    # 1. First active topic unlocked.
    def test_first_active_topic_unlocked(self):
        self.assertTrue(is_topic_unlocked(self.user, self.topic1))

    # 4. Incomplete topic remains locked when predecessor is incomplete.
    def test_incomplete_topic_locked(self):
        self.assertFalse(is_topic_unlocked(self.user, self.topic2))

    # 5. Topic unlocks when previous active topic is completed.
    def test_topic_unlocks_when_previous_completed(self):
        self._complete_topic(self.user, self.topic1)
        self.assertTrue(is_topic_unlocked(self.user, self.topic2))
        self.assertFalse(is_topic_unlocked(self.user, self.topic3))

    # 6. Inactive topics do not block progression.
    def test_inactive_topics_dont_block(self):
        self.topic2.status = Topic.STATUS_ARCHIVED
        self.topic2.is_active = False
        self.topic2.save()
        self._complete_topic(self.user, self.topic1)
        # T3 should be unlocked because T2 is inactive
        self.assertTrue(is_topic_unlocked(self.user, self.topic3))

    # 7. Progression is scoped to the same Subject.
    def test_progression_scoped_to_subject(self):
        self._complete_topic(self.user, self.topic3) # Completing last topic in Sub 1
        self.assertTrue(is_topic_unlocked(self.user, self.sub2_topic1)) # S2T1 is first in Sub 2, always unlocked

    # 8. Completed topic remains accessible.
    def test_completed_topic_accessible(self):
        self._complete_topic(self.user, self.topic2)
        # Even though T1 is not completed, T2 is completed, so it should be unlocked
        self.assertTrue(is_topic_unlocked(self.user, self.topic2))

    # 9. Completed topic remains accessible after topic reordering.
    def test_completed_accessible_after_reorder(self):
        self._complete_topic(self.user, self.topic1)
        self._complete_topic(self.user, self.topic2)
        # Reorder T3 to be before T1
        self.topic3.order = 0
        self.topic3.save()
        self.assertTrue(is_topic_unlocked(self.user, self.topic1))
        self.assertTrue(is_topic_unlocked(self.user, self.topic2))

    # 13. Best score never decreases after a lower retake.
    def test_best_score_never_decreases(self):
        # We need a question to test quiz submission
        q = Question.objects.create(topic=self.topic1, question_type=Question.TYPE_SHORT_ANSWER, text='Q')

        class FakePost:
            def get(self, key): return 'Right'
        submit_quiz_attempt(self.user, self.topic1, FakePost())
        tp = TopicProgress.objects.get(user=self.user, topic=self.topic1)
        tp.best_score = 100
        tp.status = TopicProgress.STATUS_COMPLETED
        tp.save()

        # Failed attempt
        class FakePostFail:
            def get(self, key): return 'Wrong'
        submit_quiz_attempt(self.user, self.topic1, FakePostFail())

        tp.refresh_from_db()
        self.assertEqual(tp.best_score, 100)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED) # #12 Lower-scoring retake does not relock

    # 14. Zero-question topic is not completed before visit.
    def test_zero_question_not_completed_before_visit(self):
        self.assertFalse(TopicProgress.objects.filter(user=self.user, topic=self.topic1).exists())
        self.assertFalse(is_topic_unlocked(self.user, self.topic2))

    # 15. Visiting an unlocked zero-question topic completes it.
    def test_zero_question_unlocked_visit_completes(self):
        # T1 is unlocked (first topic) and 0 questions
        record_topic_view(self.user, self.topic1)
        tp = TopicProgress.objects.get(user=self.user, topic=self.topic1)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        # 17. Completing zero-question unlocks next active
        self.assertTrue(is_topic_unlocked(self.user, self.topic2))

    # 16. Locked zero-question topic is not completed.
    def test_zero_question_locked_not_completed(self):
        record_topic_view(self.user, self.topic2) # T2 is locked
        tp = TopicProgress.objects.get(user=self.user, topic=self.topic2)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)

    # 18. Zero-question completion does not create a quiz attempt.
    def test_zero_question_no_quiz_attempt(self):
        record_topic_view(self.user, self.topic1)
        self.assertEqual(QuizAttempt.objects.filter(user=self.user, topic=self.topic1).count(), 0)

    # 22. Progression never creates LearnerAccess records.
    def test_progression_no_learner_access_records(self):
        count_before = LearnerAccess.objects.count()
        is_topic_unlocked(self.user, self.topic1)
        record_topic_view(self.user, self.topic1)
        self.assertEqual(LearnerAccess.objects.count(), count_before)

    # 23. Duplicate/bad order values produce deterministic next-topic behavior.
    def test_duplicate_order_deterministic(self):
        self.topic2.order = 1
        self.topic2.save()
        # topic1 and topic2 both have order 1. id of topic1 < topic2.
        next_t = get_next_topic(self.topic1)
        self.assertEqual(next_t, self.topic2)

    # 24. Topic -> Subject -> LMS passing score hierarchy remains correct.
    def test_passing_score_hierarchy(self):
        self.assertEqual(get_effective_passing_score(self.topic1), 75)
        self.subject1.passing_score_override = 80
        self.subject1.save()
        self.assertEqual(get_effective_passing_score(self.topic1), 80)
        self.topic1.passing_score_override = 90
        self.topic1.save()
        self.assertEqual(get_effective_passing_score(self.topic1), 90)
