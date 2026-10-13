from datetime import date
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from content.models import Subject, Topic, LearningPhase, Video, Resource
from progress.models import TopicProgress
from access.models import LearnerAccess

class Stage2HAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='test_learner',
            password='password123',
            email='test@example.com',
            role=User.ROLE_LEARNER,
            date_of_joining=date(2026, 1, 1)
        )
        self.subject = Subject.objects.create(name="Math", slug="math", is_active=True)
        self.phase1 = LearningPhase.objects.create(subject=self.subject, name="Phase 1", start_day=5, end_day=10)
        self.phase2 = LearningPhase.objects.create(subject=self.subject, name="Phase 2", start_day=15, end_day=20)

        self.topic1 = Topic.objects.create(subject=self.subject, name="T1", order=1, is_active=True, learning_phase=self.phase1, assessment_required=True)
        self.topic2 = Topic.objects.create(subject=self.subject, name="T2", order=2, is_active=True, learning_phase=self.phase2, assessment_required=True)

        self.video = Video.objects.create(topic=self.topic2, title="T2 Video", url="https://youtube.com/watch?v=123")
        self.resource = Resource.objects.create(topic=self.topic2, title="T2 Resource", url="https://example.com/file.pdf", resource_type="pdf")

        self.original_localdate = timezone.localdate
        self.client.login(username='test_learner', password='password123')

    def tearDown(self):
        timezone.localdate = self.original_localdate

    def set_today(self, year, month, day):
        test_date = date(year, month, day)
        timezone.localdate = lambda: test_date

    def test_upcoming_returns_403(self):
        self.set_today(2026, 1, 2)
        response = self.client.get(reverse('topic_detail', args=[self.subject.slug, self.topic1.id]))
        self.assertEqual(response.status_code, 403)

        quiz_resp = self.client.get(reverse('quiz_modal', args=[self.subject.slug, self.topic1.id]))
        self.assertEqual(quiz_resp.status_code, 403)

    def test_locked_sequential_behavior(self):
        # Day 16: Phase 2 started, but T1 not complete
        self.set_today(2026, 1, 16)

        response = self.client.get(reverse('topic_detail', args=[self.subject.slug, self.topic2.id]))
        self.assertEqual(response.status_code, 200)

        videos = list(response.context['videos'])
        resources = list(response.context['resources'])
        self.assertEqual(len(videos), 1)
        self.assertEqual(len(resources), 1)
        self.assertEqual(videos[0].url, "")
        self.assertEqual(resources[0].url, "")

        # Ensure progress not implicitly created
        self.assertFalse(TopicProgress.objects.filter(user=self.user, topic=self.topic2).exists())

        # Ensure no URL leakage and explicitly locked UI
        html = response.content.decode('utf-8')
        self.assertNotIn("https://youtube.com/watch?v=123", html)
        self.assertNotIn("https://example.com/file.pdf", html)
        self.assertIn("Complete previous topic", html)
        self.assertIn("Complete the previous topic to unlock", html)
        self.assertIn('<button type="button" class="btn btn-secondary w-100 py-2 fw-semibold"', html)
        self.assertIn('<i class="bi bi-lock-fill me-1"></i> Take Assessment', html)
        self.assertNotIn(reverse('quiz_modal', args=[self.subject.slug, self.topic2.id]), html)

        quiz_resp = self.client.get(reverse('quiz_modal', args=[self.subject.slug, self.topic2.id]))
        self.assertEqual(quiz_resp.status_code, 403)

    def test_available_behavior(self):
        # Day 16: Phase 2 started. Complete T1 so T2 is AVAILABLE
        TopicProgress.objects.create(user=self.user, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED)
        self.set_today(2026, 1, 16)

        response = self.client.get(reverse('topic_detail', args=[self.subject.slug, self.topic2.id]))
        self.assertEqual(response.status_code, 200)

        html = response.content.decode('utf-8')
        # URLs should be present now (Django escapejs escapes =)
        self.assertIn("youtube.com/watch", html)
        self.assertIn("https://example.com/file.pdf", html)
        # Should not have locked messages
        self.assertNotIn("Videos are locked until you complete the previous topic.", html)

        quiz_resp = self.client.get(reverse('quiz_modal', args=[self.subject.slug, self.topic2.id]))
        # 400 because topic.assessment_ready is false (no questions), which proves it wasn't 403 denied.
        self.assertIn(quiz_resp.status_code, [400, 200])

    def test_completed_behavior(self):
        # Explicitly verify COMPLETED topics remain accessible
        TopicProgress.objects.create(user=self.user, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED)
        self.set_today(2026, 1, 16)

        # topic1 is completed. Check detail page.
        response = self.client.get(reverse('topic_detail', args=[self.subject.slug, self.topic1.id]))
        self.assertEqual(response.status_code, 200)

        html = response.content.decode('utf-8')
        # Shouldn't show locked messages for completed topics
        self.assertNotIn("Assessment Locked", html)

        # Check quiz access
        quiz_resp = self.client.get(reverse('quiz_modal', args=[self.subject.slug, self.topic1.id]))
        # 400 because topic.assessment_ready is false (no questions). If it was 403, we'd fail.
        self.assertIn(quiz_resp.status_code, [400, 200])

    def test_subject_card_context(self):
        self.set_today(2026, 1, 2) # T1 UPCOMING
        response = self.client.get(reverse('subject_detail', args=[self.subject.slug]))
        self.assertEqual(response.status_code, 200)

        topics_data = response.context['topics_data']
        t1_data = next(t for t in topics_data if t['topic'].id == self.topic1.id)
        self.assertEqual(t1_data['access_state'], 'UPCOMING')

        self.set_today(2026, 1, 16) # T2 LOCKED_SEQUENTIAL
        response2 = self.client.get(reverse('subject_detail', args=[self.subject.slug]))
        t2_data = next(t for t in response2.context['topics_data'] if t['topic'].id == self.topic2.id)
        self.assertEqual(t2_data['access_state'], 'LOCKED_SEQUENTIAL')
