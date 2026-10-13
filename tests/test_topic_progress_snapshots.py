from django.test import TestCase
from django.contrib.auth import get_user_model
from content.models import Subject, Topic, LearningPhase
from progress.models import TopicProgress
from quizzes.models import QuizAttempt, Question, Choice
from progress.services import record_topic_view, complete_non_assessed_topic, reconcile_topic_progress
from django.utils import timezone
from django.core.management import call_command

User = get_user_model()

class TopicProgressSnapshotTests(TestCase):
    def setUp(self):
        self.learner = User.objects.create(
            username="learner",
            role=User.ROLE_LEARNER,
            date_of_joining=timezone.localdate()
        )
        self.subject = Subject.objects.create(name="Subj 1", order=1)
        self.phase = LearningPhase.objects.create(subject=self.subject, name="Phase 1", start_day=1, end_day=30)
        self.topic_phased = Topic.objects.create(subject=self.subject, name="Phased Topic", learning_phase=self.phase)
        self.topic_unphased = Topic.objects.create(subject=self.subject, name="Unphased Topic")

    def test_live_phased_topic_creation(self):
        # Trigger normal live TopicProgress creation
        record_topic_view(self.learner, self.topic_phased)
        
        tp = TopicProgress.objects.get(user=self.learner, topic=self.topic_phased)
        self.assertEqual(tp.expected_end_day_snapshot, 30)

    def test_live_unassigned_topic(self):
        # Complete prerequisite so this topic is unlocked
        complete_non_assessed_topic(self.learner, self.topic_phased)
        
        # Topic.learning_phase = NULL
        record_topic_view(self.learner, self.topic_unphased)
        
        tp = TopicProgress.objects.get(user=self.learner, topic=self.topic_unphased)
        self.assertIsNone(tp.expected_end_day_snapshot)

    def test_snapshot_is_not_overwritten(self):
        # Create TopicProgress with snapshot = 30
        record_topic_view(self.learner, self.topic_phased)
        tp = TopicProgress.objects.get(user=self.learner, topic=self.topic_phased)
        self.assertEqual(tp.expected_end_day_snapshot, 30)

        # Change LearningPhase end_day to 45
        self.phase.end_day = 45
        self.phase.save()

        # Trigger another live path
        reconcile_topic_progress(self.learner, self.topic_phased)
        
        tp.refresh_from_db()
        # Verify it remains 30
        self.assertEqual(tp.expected_end_day_snapshot, 30)

    def test_different_live_creation_paths(self):
        # Use different subjects so they are the first topic and unlocked
        s1 = Subject.objects.create(name="S1", order=2)
        p1 = LearningPhase.objects.create(subject=s1, name="P1", start_day=1, end_day=30)
        s2 = Subject.objects.create(name="S2", order=3)
        p2 = LearningPhase.objects.create(subject=s2, name="P2", start_day=1, end_day=30)
        s3 = Subject.objects.create(name="S3", order=4)
        p3 = LearningPhase.objects.create(subject=s3, name="P3", start_day=1, end_day=30)
        
        # Test record_topic_view
        t1 = Topic.objects.create(subject=s1, name="T1", learning_phase=p1)
        record_topic_view(self.learner, t1)
        self.assertEqual(TopicProgress.objects.get(user=self.learner, topic=t1).expected_end_day_snapshot, 30)

        # Test complete_non_assessed_topic
        t2 = Topic.objects.create(subject=s2, name="T2", learning_phase=p2, assessment_required=False)
        complete_non_assessed_topic(self.learner, t2)
        self.assertEqual(TopicProgress.objects.get(user=self.learner, topic=t2).expected_end_day_snapshot, 30)

        # Test reconcile_topic_progress
        t3 = Topic.objects.create(subject=s3, name="T3", learning_phase=p3)
        reconcile_topic_progress(self.learner, t3)
        self.assertEqual(TopicProgress.objects.get(user=self.learner, topic=t3).expected_end_day_snapshot, 30)

    def test_historical_repair(self):
        # Create a historical QuizAttempt without a TopicProgress record
        t4 = Topic.objects.create(subject=self.subject, name="T4", learning_phase=self.phase)
        QuizAttempt.objects.create(
            user=self.learner,
            topic=t4,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        
        # Verify it is missing
        self.assertFalse(TopicProgress.objects.filter(user=self.learner, topic=t4).exists())
        
        # Run repair
        call_command('repair_topic_progress', execute=True)
        
        # Verify it was reconstructed
        tp = TopicProgress.objects.get(user=self.learner, topic=t4)
        # Even though the Topic currently has a Phase (end_day=30), it must be NULL
        self.assertIsNone(tp.expected_end_day_snapshot)
