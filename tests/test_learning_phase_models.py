from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from content.models import Subject, Topic, LearningPhase
from progress.models import TopicProgress
from django.contrib.auth import get_user_model

User = get_user_model()

class LearningPhaseModelTests(TestCase):
    def setUp(self):
        self.subject = Subject.objects.create(name="Phase Subj", order=1)
        self.subject2 = Subject.objects.create(name="Phase Subj 2", order=2)

    def test_learning_phase_creation(self):
        phase = LearningPhase.objects.create(
            subject=self.subject,
            name="Phase 1",
            start_day=1,
            end_day=30
        )
        self.assertEqual(phase.subject, self.subject)
        self.assertEqual(phase.start_day, 1)

    def test_start_day_gte_1(self):
        phase = LearningPhase(subject=self.subject, name="Zero Phase", start_day=0, end_day=10)
        with self.assertRaises(ValidationError) as e:
            phase.clean()
        self.assertIn("start_day", str(e.exception))

        # Test create() raises ValidationError now due to save() override
        with self.assertRaises(ValidationError):
            LearningPhase.objects.create(subject=self.subject, name="Zero Phase", start_day=0, end_day=10)

    def test_end_day_gte_start_day(self):
        phase = LearningPhase(subject=self.subject, name="Invalid Phase", start_day=20, end_day=10)
        with self.assertRaises(ValidationError) as e:
            phase.clean()
        self.assertIn("end_day", str(e.exception))

        # Test create() raises ValidationError now due to save() override
        with self.assertRaises(ValidationError):
            LearningPhase.objects.create(subject=self.subject, name="Invalid Phase", start_day=20, end_day=10)

    def test_overlapping_phases_same_subject_rejected(self):
        LearningPhase.objects.create(subject=self.subject, name="Phase 1", start_day=1, end_day=30)

        with self.assertRaises(ValidationError) as e:
            LearningPhase.objects.create(subject=self.subject, name="Phase 2", start_day=20, end_day=40)
        self.assertIn("Overlapping phases", str(e.exception))

    def test_adjacent_phases_same_subject_allowed(self):
        LearningPhase.objects.create(subject=self.subject, name="Phase 1", start_day=1, end_day=30)
        phase2 = LearningPhase(subject=self.subject, name="Phase 2", start_day=31, end_day=60)
        phase2.clean() # Should not raise
        phase2.save()
        self.assertEqual(LearningPhase.objects.count(), 2)

    def test_overlapping_phases_different_subjects_allowed(self):
        LearningPhase.objects.create(subject=self.subject, name="Phase 1", start_day=10, end_day=20)
        phase2 = LearningPhase(subject=self.subject2, name="Phase A", start_day=10, end_day=20)
        phase2.clean() # Should not raise
        phase2.save()
        self.assertEqual(LearningPhase.objects.count(), 2)

    def test_topic_learning_phase_assignment(self):
        # topic can have null phase
        topic = Topic.objects.create(subject=self.subject, name="No Phase Topic")
        self.assertIsNone(topic.learning_phase)

        # topic can be assigned phase in same subject
        phase = LearningPhase.objects.create(subject=self.subject, name="P1", start_day=1, end_day=10)
        topic.learning_phase = phase
        topic.save()
        self.assertEqual(topic.learning_phase, phase)

    def test_cross_subject_assignment_rejected(self):
        phase2 = LearningPhase.objects.create(subject=self.subject2, name="P2", start_day=1, end_day=10)
        topic = Topic(subject=self.subject, name="Bad Topic", learning_phase=phase2)
        with self.assertRaises(ValidationError) as e:
            topic.clean()
        self.assertIn("same Subject", str(e.exception))

    def test_delete_learning_phase_sets_null(self):
        phase = LearningPhase.objects.create(subject=self.subject, name="P1", start_day=1, end_day=10)
        topic = Topic.objects.create(subject=self.subject, name="T1", learning_phase=phase)

        self.assertEqual(topic.learning_phase, phase)
        phase.delete()

        topic.refresh_from_db()
        self.assertIsNone(topic.learning_phase)
        self.assertEqual(Topic.objects.count(), 1)

    def test_topic_progress_expected_end_day_snapshot(self):
        user = User.objects.create(username="testuser", role=User.ROLE_LEARNER)
        topic = Topic.objects.create(subject=self.subject, name="T1")

        tp = TopicProgress.objects.create(user=user, topic=topic, expected_end_day_snapshot=None)
        self.assertIsNone(tp.expected_end_day_snapshot)

        tp.expected_end_day_snapshot = 30
        tp.save()

        tp.refresh_from_db()
        self.assertEqual(tp.expected_end_day_snapshot, 30)
