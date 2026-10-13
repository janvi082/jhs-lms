from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from content.models import Subject, LearningPhase, Topic

User = get_user_model()

class AdminLearningPhaseTests(TestCase):
    def setUp(self):
        # Create users
        self.admin = User.objects.create_user(username="admin", role=User.ROLE_ADMIN, password="password")
        self.learner = User.objects.create_user(username="learner", role=User.ROLE_LEARNER, password="password")

        # Create subjects
        self.subject1 = Subject.objects.create(name="Subject 1", order=1)
        self.subject2 = Subject.objects.create(name="Subject 2", order=2)

    def test_admin_can_create_valid_phase(self):
        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_learning_phase_add'), {
            'subject': self.subject1.id,
            'name': 'Phase 1',
            'start_day': 1,
            'end_day': 30
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LearningPhase.objects.count(), 1)
        phase = LearningPhase.objects.first()
        self.assertEqual(phase.name, 'Phase 1')

    def test_invalid_start_day_rejected(self):
        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_learning_phase_add'), {
            'subject': self.subject1.id,
            'name': 'Phase 1',
            'start_day': 0, # Invalid
            'end_day': 30
        })
        self.assertEqual(response.status_code, 200) # Form re-renders
        self.assertIn('Start day must be >= 1.', response.context['form'].errors['start_day'])
        self.assertEqual(LearningPhase.objects.count(), 0)

    def test_end_day_before_start_day_rejected(self):
        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_learning_phase_add'), {
            'subject': self.subject1.id,
            'name': 'Phase 1',
            'start_day': 30,
            'end_day': 15 # Invalid
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('End day must be >= start day.', response.context['form'].errors['end_day'])
        self.assertEqual(LearningPhase.objects.count(), 0)

    def test_overlapping_phases_same_subject_rejected(self):
        LearningPhase.objects.create(subject=self.subject1, name="Phase 1", start_day=1, end_day=30)

        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_learning_phase_add'), {
            'subject': self.subject1.id,
            'name': 'Phase 2',
            'start_day': 30, # Overlaps Day 30
            'end_day': 60
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('Overlapping phases within the same Subject are not allowed.', response.context['form'].errors.get('__all__', []))
        self.assertEqual(LearningPhase.objects.count(), 1)

    def test_adjacent_phases_allowed(self):
        LearningPhase.objects.create(subject=self.subject1, name="Phase 1", start_day=1, end_day=30)

        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_learning_phase_add'), {
            'subject': self.subject1.id,
            'name': 'Phase 2',
            'start_day': 31,
            'end_day': 60
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LearningPhase.objects.count(), 2)

    def test_same_day_range_different_subjects_allowed(self):
        LearningPhase.objects.create(subject=self.subject1, name="Phase 1", start_day=1, end_day=30)

        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_learning_phase_add'), {
            'subject': self.subject2.id, # Different subject
            'name': 'Phase 1 (Subj 2)',
            'start_day': 1,
            'end_day': 30
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LearningPhase.objects.count(), 2)

    def test_editing_preserves_valid_non_overlapping_behavior(self):
        phase = LearningPhase.objects.create(subject=self.subject1, name="Phase 1", start_day=1, end_day=30)

        self.client.login(username="admin", password="password")
        # Edit itself to a new valid range
        response = self.client.post(reverse('portal_learning_phase_edit', args=[phase.id]), {
            'subject': self.subject1.id,
            'name': 'Phase 1 Updated',
            'start_day': 2,
            'end_day': 31
        })
        self.assertEqual(response.status_code, 302)
        phase.refresh_from_db()
        self.assertEqual(phase.start_day, 2)
        self.assertEqual(phase.end_day, 31)

    def test_delete_sets_topic_learning_phase_to_null_and_preserves_history(self):
        phase = LearningPhase.objects.create(subject=self.subject1, name="Phase 1", start_day=1, end_day=30)
        topic = Topic.objects.create(subject=self.subject1, name="Topic 1", learning_phase=phase)

        from progress.models import TopicProgress
        from quizzes.models import QuizAttempt

        # Create history
        tp = TopicProgress.objects.create(user=self.learner, topic=topic, expected_end_day_snapshot=30)
        qa = QuizAttempt.objects.create(user=self.learner, topic=topic, score=100, correct_count=1, passed=True, total_questions=1, attempt_number=1, passing_score_used=75)

        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_learning_phase_delete', args=[phase.id]))
        self.assertEqual(response.status_code, 302)

        self.assertEqual(LearningPhase.objects.count(), 0)

        # Topic is not deleted, but learning_phase is NULL
        topic.refresh_from_db()
        self.assertIsNone(topic.learning_phase)

        # History is preserved
        self.assertTrue(TopicProgress.objects.filter(id=tp.id).exists())
        self.assertTrue(QuizAttempt.objects.filter(id=qa.id).exists())

    def test_non_admin_cannot_access_views(self):
        self.client.login(username="learner", password="password")
        response = self.client.get(reverse('portal_learning_phases_list'))
        self.assertNotEqual(response.status_code, 200) # Should be redirect or forbidden

        response = self.client.get(reverse('portal_learning_phase_add'))
        self.assertNotEqual(response.status_code, 200)

    def test_phase_list_chronological_ordering(self):
        # Create out of order
        LearningPhase.objects.create(subject=self.subject1, name="Phase 3", start_day=61, end_day=90)
        LearningPhase.objects.create(subject=self.subject1, name="Phase 1", start_day=1, end_day=30)
        LearningPhase.objects.create(subject=self.subject1, name="Phase 2", start_day=31, end_day=60)

        self.client.login(username="admin", password="password")
        response = self.client.get(reverse('portal_learning_phases_list'))
        self.assertEqual(response.status_code, 200)

        # Check context
        subjects = response.context['subjects']
        subj1 = [s for s in subjects if s.id == self.subject1.id][0]

        phases = subj1.sorted_phases
        self.assertEqual(phases[0].name, "Phase 1")
        self.assertEqual(phases[1].name, "Phase 2")
        self.assertEqual(phases[2].name, "Phase 3")
