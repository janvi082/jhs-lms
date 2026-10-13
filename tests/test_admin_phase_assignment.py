from django.test import TestCase
from django.urls import reverse
from content.models import Subject, LearningPhase, Topic
from accounts.models import User

class AdminPhaseAssignmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="password", role=User.ROLE_ADMIN)
        self.learner = User.objects.create_user(username="learner", password="password", role=User.ROLE_LEARNER)

        self.subject1 = Subject.objects.create(name="Subject 1", order=1, is_active=True)
        self.subject2 = Subject.objects.create(name="Subject 2", order=2, is_active=True)

        self.phase1 = LearningPhase.objects.create(subject=self.subject1, name="Phase 1", start_day=1, end_day=10)
        self.phase2 = LearningPhase.objects.create(subject=self.subject1, name="Phase 2", start_day=11, end_day=20)
        self.phase_other = LearningPhase.objects.create(subject=self.subject2, name="Phase Other", start_day=1, end_day=10)

        self.topic1 = Topic.objects.create(subject=self.subject1, name="Topic 1", order=1)
        self.topic2 = Topic.objects.create(subject=self.subject1, name="Topic 2", order=2)
        self.topic3 = Topic.objects.create(subject=self.subject1, name="Topic 3", order=3, learning_phase=self.phase1)
        self.topic_other = Topic.objects.create(subject=self.subject2, name="Topic Other", order=1)

    def test_assign_topics_access_denied_for_learner(self):
        self.client.login(username="learner", password="password")
        response = self.client.get(reverse('portal_phase_assign_topics', args=[self.phase1.id]))
        self.assertEqual(response.status_code, 302)

    def test_assign_topics_get(self):
        self.client.login(username="admin", password="password")
        response = self.client.get(reverse('portal_phase_assign_topics', args=[self.phase1.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Topic 1")
        self.assertContains(response, "Topic 2")
        self.assertNotContains(response, "Topic Other")

    def test_assign_unassigned_topics_success(self):
        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_phase_assign_topics', args=[self.phase1.id]), {
            'topic_ids': [self.topic1.id, self.topic2.id, self.topic3.id],
            'assignment_form_submitted': '1'
        })
        self.assertRedirects(response, reverse('portal_learning_phases_list'))
        self.topic1.refresh_from_db()
        self.topic2.refresh_from_db()
        self.assertEqual(self.topic1.learning_phase, self.phase1)
        self.assertEqual(self.topic2.learning_phase, self.phase1)

    def test_reassignment_triggers_confirmation(self):
        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_phase_assign_topics', args=[self.phase2.id]), {
            'topic_ids': [self.topic3.id],
            'assignment_form_submitted': '1'
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portal/phase_assign_topics_confirm.html')
        self.assertContains(response, "Confirm Reassignment")
        self.topic3.refresh_from_db()
        self.assertEqual(self.topic3.learning_phase, self.phase1)

    def test_explicit_confirmation_moves_topic(self):
        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_phase_assign_topics', args=[self.phase2.id]), {
            'topic_ids': [self.topic3.id],
            'confirm': 'true',
            f'expected_phase_{self.topic3.id}': str(self.phase1.id)
        })
        self.assertRedirects(response, reverse('portal_learning_phases_list'))
        self.topic3.refresh_from_db()
        self.assertEqual(self.topic3.learning_phase, self.phase2)

    def test_unassignment_triggers_confirmation(self):
        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_phase_assign_topics', args=[self.phase1.id]), {
            'topic_ids': [],
            'assignment_form_submitted': '1'
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portal/phase_assign_topics_confirm.html')

    def test_cross_subject_assignment_fails(self):
        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_phase_assign_topics', args=[self.phase1.id]), {
            'topic_ids': [self.topic_other.id],
            'assignment_form_submitted': '1'
        })
        self.assertRedirects(response, reverse('portal_phase_assign_topics', args=[self.phase1.id]))
        self.topic_other.refresh_from_db()
        self.assertIsNone(self.topic_other.learning_phase)

    def test_learning_phases_list_filters(self):
        self.client.login(username="admin", password="password")
        response = self.client.get(reverse('portal_learning_phases_list') + f'?subject={self.subject1.id}')
        self.assertEqual(response.status_code, 200)

        response2 = self.client.get(reverse('portal_learning_phases_list') + f'?subject={self.subject1.id}&phase={self.phase1.id}')
        self.assertEqual(response2.status_code, 200)

    def test_missing_marker_rejected(self):
        self.client.login(username="admin", password="password")
        response = self.client.post(reverse('portal_phase_assign_topics', args=[self.phase1.id]), {
            # Intentionally omit assignment_form_submitted to simulate malformed request
            'topic_ids': []
        })
        # Should redirect back with error, not go to confirmation
        self.assertRedirects(response, reverse('portal_phase_assign_topics', args=[self.phase1.id]))

    def test_stale_confirmation_triggers_warning(self):
        self.client.login(username="admin", password="password")

        # Admin A tries to assign topic3 to phase2 (expected phase is phase1)
        # Meanwhile, Admin B moves it to None
        self.phase3 = LearningPhase.objects.create(subject=self.subject1, name="Phase 3", start_day=21, end_day=30); self.topic3.learning_phase = self.phase3
        self.topic3.save()

        response = self.client.post(reverse('portal_phase_assign_topics', args=[self.phase2.id]), {
            'topic_ids': [self.topic3.id],
            'confirm': 'true',
            f'expected_phase_{self.topic3.id}': str(self.phase1.id) # Sending stale state
        })

        # Should not redirect (save), but render confirmation template again
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portal/phase_assign_topics_confirm.html')
        self.assertContains(response, "changed since your last review")

        # Now submit again with the correctly refreshed expected phase
        response2 = self.client.post(reverse('portal_phase_assign_topics', args=[self.phase2.id]), {
            'topic_ids': [self.topic3.id],
            'confirm': 'true',
            f'expected_phase_{self.topic3.id}': str(self.phase3.id) # Sending fresh state
        })
        self.assertRedirects(response2, reverse('portal_learning_phases_list'))

        self.topic3.refresh_from_db()
        self.assertEqual(self.topic3.learning_phase, self.phase2)
