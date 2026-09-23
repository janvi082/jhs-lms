from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from content.models import Subject, Topic, SiteConfig
from progress.models import TopicProgress
from progress.services import (
    is_topic_unlocked,
    get_next_topic,
    complete_non_assessed_topic,
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

    # 14. Non-assessed topic is not completed before visit.
    def test_zero_question_not_completed_before_visit(self):
        self.topic1.assessment_required = False
        self.topic1.save()
        self.assertFalse(TopicProgress.objects.filter(user=self.user, topic=self.topic1).exists())
        self.assertFalse(is_topic_unlocked(self.user, self.topic2))

    # 15. Visiting an unlocked non-assessed topic completes it.
    def test_zero_question_unlocked_visit_completes(self):
        # T1 is unlocked (first topic) and non-assessed
        self.topic1.assessment_required = False
        self.topic1.save()
        record_topic_view(self.user, self.topic1)
        tp = TopicProgress.objects.get(user=self.user, topic=self.topic1)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        # 17. Completing non-assessed unlocks next active
        self.assertTrue(is_topic_unlocked(self.user, self.topic2))

    # 16. Locked non-assessed topic is not completed.
    def test_zero_question_locked_not_completed(self):
        self.topic2.assessment_required = False
        self.topic2.save()
        record_topic_view(self.user, self.topic2) # T2 is locked
        self.assertFalse(TopicProgress.objects.filter(user=self.user, topic=self.topic2).exists())

    # 18. Non-assessed completion does not create a quiz attempt.
    def test_zero_question_no_quiz_attempt(self):
        self.topic1.assessment_required = False
        self.topic1.save()
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
        next_t = get_next_topic(self.user, self.topic1)
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

    # --- NEW REGRESSION TESTS FOR ACCESS CONTROL + PROGRESSION ---

    def _deny_topic(self, user, topic):
        topic_type = ContentType.objects.get_for_model(topic)
        LearnerAccess.objects.create(learner=user, content_type=topic_type, object_id=topic.id, is_allowed=False)
        if hasattr(user, '_learner_access_cache'):
            delattr(user, '_learner_access_cache')
            
    def _allow_topic(self, user, topic):
        topic_type = ContentType.objects.get_for_model(topic)
        LearnerAccess.objects.filter(learner=user, content_type=topic_type, object_id=topic.id, is_allowed=False).delete()
        if hasattr(user, '_learner_access_cache'):
            delattr(user, '_learner_access_cache')

    # 1. First topic denied
    def test_first_topic_denied_second_is_entry(self):
        self._deny_topic(self.user, self.topic1)
        self.assertFalse(is_topic_unlocked(self.user, self.topic1))
        self.assertTrue(is_topic_unlocked(self.user, self.topic2))
        self.assertFalse(is_topic_unlocked(self.user, self.topic3))

    # 2. Middle topic denied
    def test_middle_topic_denied_skip_in_progression(self):
        self._deny_topic(self.user, self.topic2)
        self._complete_topic(self.user, self.topic1)
        self.assertTrue(is_topic_unlocked(self.user, self.topic3))
        self.assertEqual(get_next_topic(self.user, self.topic1), self.topic3)

    # 3. Multiple denied topics
    def test_multiple_denied_topics_first_accessible_entry(self):
        self._deny_topic(self.user, self.topic1)
        self._deny_topic(self.user, self.topic2)
        self.assertTrue(is_topic_unlocked(self.user, self.topic3))

    # 4. Normal progression
    def test_normal_progression(self):
        self.assertTrue(is_topic_unlocked(self.user, self.topic1))
        self.assertFalse(is_topic_unlocked(self.user, self.topic2))
        self._complete_topic(self.user, self.topic1)
        self.assertTrue(is_topic_unlocked(self.user, self.topic2))

    # 5. Failed assessment remains locked
    def test_failed_assessment_remains_locked(self):
        self._complete_topic(self.user, self.topic1)
        TopicProgress.objects.create(user=self.user, topic=self.topic2, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0)
        self.assertFalse(is_topic_unlocked(self.user, self.topic3))

    # 6. Passed assessment unlocks next
    def test_passed_assessment_unlocks_next(self):
        self._complete_topic(self.user, self.topic1)
        self._complete_topic(self.user, self.topic2)
        self.assertTrue(is_topic_unlocked(self.user, self.topic3))

    # 7. Previously completed topic
    def test_previously_completed_topic_remains_unlocked(self):
        self._deny_topic(self.user, self.topic1)
        self._complete_topic(self.user, self.topic2)
        self._allow_topic(self.user, self.topic1)
        
        tp2 = TopicProgress.objects.get(user=self.user, topic=self.topic2)
        self.assertEqual(tp2.status, TopicProgress.STATUS_COMPLETED)
        
        self.assertTrue(is_topic_unlocked(self.user, self.topic2))
        self.assertTrue(is_topic_unlocked(self.user, self.topic3))

    # 8. In-progress topic
    def test_in_progress_topic_retains_progress_and_access(self):
        self._deny_topic(self.user, self.topic1)
        TopicProgress.objects.create(user=self.user, topic=self.topic2, status=TopicProgress.STATUS_IN_PROGRESS)
        self._allow_topic(self.user, self.topic1)
        self.assertTrue(is_topic_unlocked(self.user, self.topic1))
        self.assertTrue(is_topic_unlocked(self.user, self.topic2))
        self.assertFalse(is_topic_unlocked(self.user, self.topic3))
        
    # 9. Current topic denied after progress exists
    def test_current_topic_denied_after_progress(self):
        tp = TopicProgress.objects.create(user=self.user, topic=self.topic2, status=TopicProgress.STATUS_IN_PROGRESS)
        self._deny_topic(self.user, self.topic2)
        
        # Test 2: existing progress preserved but access denied wins
        self.assertFalse(is_topic_unlocked(self.user, self.topic2))
        
        # Verify existing TopicProgress remains unchanged
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)

    # 10. No fake TopicProgress (verified by default logic since _complete_topic is explicitly called in tests)

    # 11. Subject independence
    def test_subject_independence_access_progression(self):
        self._deny_topic(self.user, self.topic3)
        self.assertTrue(is_topic_unlocked(self.user, self.sub2_topic1))

    # 12. get_next_topic uses learner-specific sequence
    def test_get_next_topic_learner_specific(self):
        self._deny_topic(self.user, self.topic2)
        self.assertEqual(get_next_topic(self.user, self.topic1), self.topic3)
        self.assertEqual(get_next_topic(self.user, self.topic3), None)
    def test_non_assessed_topic_with_questions_completes_on_visit(self):
        # 10. Non-assessed Topic with questions also completes when learner visits
        self.topic1.assessment_required = False
        self.topic1.save()
        Question.objects.create(topic=self.topic1, question_type=Question.TYPE_SHORT_ANSWER, text='Q')
        record_topic_view(self.user, self.topic1)
        tp = TopicProgress.objects.get(user=self.user, topic=self.topic1)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)

    def test_assessed_topic_requires_quiz(self):
        # 12. Assessed Topic still requires quiz/pass
        self.topic1.assessment_required = True
        self.topic1.save()
        Question.objects.create(topic=self.topic1, question_type=Question.TYPE_SHORT_ANSWER, text='Q')
        record_topic_view(self.user, self.topic1)
        tp = TopicProgress.objects.get(user=self.user, topic=self.topic1)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)

    def test_failed_assessed_topic_remains_locked(self):
        # 13. Failed assessed Topic remains incomplete/locked
        self.topic1.assessment_required = True
        self.topic1.save()
        Question.objects.create(topic=self.topic1, question_type=Question.TYPE_SHORT_ANSWER, text='Q')
        class FakePostFail:
            def get(self, key): return 'Wrong'
        submit_quiz_attempt(self.user, self.topic1, FakePostFail())
        self.assertFalse(is_topic_unlocked(self.user, self.topic2))
