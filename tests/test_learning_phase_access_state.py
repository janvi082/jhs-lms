from datetime import date
from django.test import TestCase
from django.utils import timezone
from accounts.models import User
from content.models import Subject, Topic, LearningPhase
from progress.models import TopicProgress
from progress.services import get_topic_access_state, TopicAccessState
from access.models import LearnerAccess
from django.contrib.contenttypes.models import ContentType

class LearningPhaseAccessStateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username='test_learner',
            email='test@example.com',
            role=User.ROLE_LEARNER,
            date_of_joining=date(2026, 1, 1)
        )
        self.subject = Subject.objects.create(name="Math", slug="math", is_active=True)
        self.phase1 = LearningPhase.objects.create(subject=self.subject, name="Phase 1", start_day=5, end_day=10)
        self.phase2 = LearningPhase.objects.create(subject=self.subject, name="Phase 2", start_day=15, end_day=20)
        
        self.topic1 = Topic.objects.create(subject=self.subject, name="T1", order=1, is_active=True, learning_phase=self.phase1)
        self.topic2 = Topic.objects.create(subject=self.subject, name="T2", order=2, is_active=True, learning_phase=self.phase2)
        self.topic3 = Topic.objects.create(subject=self.subject, name="T3", order=3, is_active=True, learning_phase=None)
        
        # We need to monkey-patch timezone.localdate for deterministic tests
        self.original_localdate = timezone.localdate
    
    def tearDown(self):
        timezone.localdate = self.original_localdate

    def set_today(self, year, month, day):
        # Helper to set the mocked current localdate
        test_date = date(year, month, day)
        timezone.localdate = lambda: test_date

    def test_admin_denied(self):
        # 1. ADMIN DENIED
        # Topic is denied through existing access rules
        LearnerAccess.objects.create(learner=self.user, content_type=ContentType.objects.get_for_model(Topic), object_id=self.topic1.id, is_allowed=False)
        self.assertEqual(get_topic_access_state(self.user, self.topic1), TopicAccessState.DENIED)

    def test_upcoming(self):
        # 2. UPCOMING
        # learner_day = 2, phase.start_day = 5
        self.set_today(2026, 1, 2)
        self.assertEqual(get_topic_access_state(self.user, self.topic1), TopicAccessState.UPCOMING)

    def test_phase_start_day(self):
        # 3. PHASE START DAY
        # learner_day = 5, phase.start_day = 5
        self.set_today(2026, 1, 5)
        # Should not return UPCOMING. Since it is the first topic (T1), it is sequentially available.
        self.assertEqual(get_topic_access_state(self.user, self.topic1), TopicAccessState.AVAILABLE)

    def test_timeline_active_sequential_lock(self):
        # 4. TIMELINE ACTIVE + SEQUENTIAL LOCK
        # learner_day = 16, Phase 2 start_day = 15. So timeline is active for T2.
        # But T1 is not complete, so T2 is sequentially locked.
        self.set_today(2026, 1, 16)
        self.assertEqual(get_topic_access_state(self.user, self.topic2), TopicAccessState.LOCKED_SEQUENTIAL)

    def test_timeline_active_sequence_complete(self):
        # 5. TIMELINE ACTIVE + SEQUENCE COMPLETE
        TopicProgress.objects.create(user=self.user, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED)
        self.set_today(2026, 1, 16)
        # T1 is completed. T2 timeline is active (Day 16 >= 15).
        self.assertEqual(get_topic_access_state(self.user, self.topic2), TopicAccessState.AVAILABLE)

    def test_no_learning_phase_sequential_available(self):
        # 6. NO LEARNING PHASE + SEQUENTIAL AVAILABLE
        TopicProgress.objects.create(user=self.user, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED)
        TopicProgress.objects.create(user=self.user, topic=self.topic2, status=TopicProgress.STATUS_COMPLETED)
        # T3 has no phase. Previous topics completed.
        self.assertEqual(get_topic_access_state(self.user, self.topic3), TopicAccessState.AVAILABLE)

    def test_no_learning_phase_sequential_locked(self):
        # 7. NO LEARNING PHASE + SEQUENTIAL LOCKED
        # T3 has no phase. T2 is NOT completed.
        self.assertEqual(get_topic_access_state(self.user, self.topic3), TopicAccessState.LOCKED_SEQUENTIAL)

    def test_in_progress_overrides_upcoming(self):
        # 8. IN_PROGRESS
        # TopicProgress status = IN_PROGRESS.
        TopicProgress.objects.create(user=self.user, topic=self.topic1, status=TopicProgress.STATUS_IN_PROGRESS)
        # Learner day = 2, Phase start day = 5 (so it would normally be UPCOMING).
        self.set_today(2026, 1, 2)
        # But since it's already IN_PROGRESS, it must be AVAILABLE.
        self.assertEqual(get_topic_access_state(self.user, self.topic1), TopicAccessState.AVAILABLE)

    def test_completed(self):
        # 9. COMPLETED
        TopicProgress.objects.create(user=self.user, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED)
        self.assertEqual(get_topic_access_state(self.user, self.topic1), TopicAccessState.COMPLETED)

    def test_completed_after_phase_date_change(self):
        # 10. COMPLETED AFTER PHASE DATE CHANGE
        TopicProgress.objects.create(user=self.user, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED)
        self.phase1.start_day = 100
        self.phase1.end_day = 110
        self.phase1.save()
        self.set_today(2026, 1, 2) # Day 2 < 100
        self.assertEqual(get_topic_access_state(self.user, self.topic1), TopicAccessState.COMPLETED)

    def test_in_progress_after_phase_date_change(self):
        # 11. IN_PROGRESS AFTER PHASE DATE CHANGE
        TopicProgress.objects.create(user=self.user, topic=self.topic1, status=TopicProgress.STATUS_IN_PROGRESS)
        self.phase1.start_day = 100
        self.phase1.end_day = 110
        self.phase1.save()
        self.set_today(2026, 1, 2) # Day 2 < 100
        self.assertEqual(get_topic_access_state(self.user, self.topic1), TopicAccessState.AVAILABLE)

    def test_past_end_day(self):
        # 12. PAST END DAY
        self.set_today(2026, 1, 30) # Day 30 > end_day 10
        # For T1, previous topic is None, so sequential passes.
        self.assertEqual(get_topic_access_state(self.user, self.topic1), TopicAccessState.AVAILABLE)

    def test_past_end_day_sequential_lock(self):
        # 13. PAST END DAY + SEQUENTIAL LOCK
        self.set_today(2026, 1, 30) # Day 30 > end_day 20 for T2
        # T1 is incomplete, so T2 is sequentially locked.
        self.assertEqual(get_topic_access_state(self.user, self.topic2), TopicAccessState.LOCKED_SEQUENTIAL)

    def test_admin_denied_previous_topic(self):
        # 14. ADMIN-DENIED PREVIOUS TOPIC
        # T1 is denied. T2 has phase2 (starts Day 15).
        LearnerAccess.objects.create(learner=self.user, content_type=ContentType.objects.get_for_model(Topic), object_id=self.topic1.id, is_allowed=False)
        self.set_today(2026, 1, 16) # Day 16 >= 15
        # Since T1 is denied, it should be excluded from prerequisites. T2 should be AVAILABLE.
        self.assertEqual(get_topic_access_state(self.user, self.topic2), TopicAccessState.AVAILABLE)

    def test_zero_question_previous_topic(self):
        # 15. ZERO-QUESTION PREVIOUS TOPIC
        # T1 has no questions but assessment_required=False (completed directly).
        self.topic1.assessment_required = False
        self.topic1.save()
        
        # If we complete T1, T2 should be unlocked.
        TopicProgress.objects.create(user=self.user, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED)
        self.set_today(2026, 1, 16)
        self.assertEqual(get_topic_access_state(self.user, self.topic2), TopicAccessState.AVAILABLE)

    def test_missing_date_of_joining(self):
        # 16. MISSING DATE OF JOINING
        # Learner has a phased Topic but date_of_joining is None
        self.user.date_of_joining = None
        self.user.save()
        
        from progress.services import is_topic_unlocked
        
        # State should be DENIED
        self.assertEqual(get_topic_access_state(self.user, self.topic1), TopicAccessState.DENIED)
        # is_topic_unlocked should return False
        self.assertFalse(is_topic_unlocked(self.user, self.topic1))
