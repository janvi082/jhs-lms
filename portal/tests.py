from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from content.models import Subject, Topic, SiteConfig
from quizzes.models import Question, Choice

class PortalTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.default_required_question_count = 5
        self.site_config.save()

        self.admin = User.objects.create_user(
            username='portaladmin',
            password='password123',
            role=User.ROLE_ADMIN,
            is_staff=True
        )

        self.learner = User.objects.create_user(
            username='portallearner',
            password='password123',
            role=User.ROLE_LEARNER
        )

        self.subject = Subject.objects.create(
            name="Communication Skills",
            order=1,
            is_active=True
        )

        self.topic = Topic.objects.create(
            subject=self.subject,
            name="Email Etiquette",
            summary="Learn email tone.",
            order=1,
            status=Topic.STATUS_DRAFT
        )

        self.client = Client()

    def test_learner_cannot_access_portal(self):
        self.client.login(username='portallearner', password='password123')
        response = self.client.get(reverse('portal_subjects_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('learner_dashboard'), response.url)

    def test_admin_can_access_portal(self):
        self.client.login(username='portaladmin', password='password123')
        response = self.client.get(reverse('portal_subjects_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Communication Skills")

    def test_publish_gate_blocks_save_when_questions_short(self):
        self.client.login(username='portaladmin', password='password123')

        # Topic has 0 questions (< 5 required)
        post_data = {
            'subject': self.subject.id,
            'name': 'Updated Title',
            'summary': 'Updated summary.',
            'status': Topic.STATUS_PUBLISHED,
            'order': 2,
            'assessment_required': True,
        }
        response = self.client.post(reverse('portal_topic_edit', args=[self.topic.id]), post_data)
        # Should render 200 without redirecting/saving
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Topic was not published.")

        # Verify database was NOT modified at all on blocked publish
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, Topic.STATUS_DRAFT)
        self.assertFalse(self.topic.is_active)
        self.assertEqual(self.topic.name, 'Email Etiquette')
        self.assertEqual(self.topic.order, 1)

    def test_publish_gate_allows_publish_when_questions_met(self):
        self.client.login(username='portaladmin', password='password123')

        # Add 5 questions
        for i in range(1, 6):
            q = Question.objects.create(topic=self.topic, text=f"Question {i}", order=i)
            Choice.objects.create(question=q, text="Option A", is_correct=True)
            Choice.objects.create(question=q, text="Option B", is_correct=False)

        post_data = {
            'subject': self.subject.id,
            'name': 'Email Etiquette Published',
            'summary': 'Learn email tone.',
            'status': Topic.STATUS_PUBLISHED,
            'order': 1,
            'assessment_required': True,
        }
        response = self.client.post(reverse('portal_topic_edit', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 302)

        # Verify status updated to PUBLISHED in DB
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.name, 'Email Etiquette Published')
        self.assertEqual(self.topic.status, Topic.STATUS_PUBLISHED)
        self.assertTrue(self.topic.is_active)

    def test_topic_add_success_forces_draft_and_persists(self):
        self.client.login(username='portaladmin', password='password123')
        initial_count = Topic.objects.count()

        post_data = {
            'subject': self.subject.id,
            'name': 'New Topic Without Status In Form',
            'summary': 'Short description of new topic.',
            'order': 5,
        }
        response = self.client.post(reverse('portal_topic_add'), post_data)
        self.assertEqual(response.status_code, 302)

        # DB assertion
        self.assertEqual(Topic.objects.count(), initial_count + 1)
        created_topic = Topic.objects.get(name='New Topic Without Status In Form')
        self.assertEqual(created_topic.status, Topic.STATUS_DRAFT)
        self.assertFalse(created_topic.is_active)
        self.assertEqual(created_topic.order, 5)
        self.assertRedirects(response, reverse('portal_topic_edit', args=[created_topic.id]))

    def test_topic_add_invalid_returns_modal_flag_and_bound_errors(self):
        self.client.login(username='portaladmin', password='password123')
        initial_count = Topic.objects.count()

        # Missing required 'name' and 'summary'
        post_data = {
            'subject': self.subject.id,
            'name': '',
            'summary': '',
            'order': 1,
        }
        response = self.client.post(reverse('portal_topic_add'), post_data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['show_modal'], 'topic_add')
        self.assertIn('name', response.context['form'].errors)
        self.assertIn('summary', response.context['form'].errors)

        # Verify no database persistence
        self.assertEqual(Topic.objects.count(), initial_count)

    def test_topic_is_assessment_ready_method_behavior(self):
        # 1. Default required count is 5, topic has 0 questions -> False
        self.assertFalse(self.topic.is_assessment_ready())

        # 2. Add 5 questions -> True
        for i in range(1, 6):
            q = Question.objects.create(topic=self.topic, text=f"Question {i}", order=i)
            Choice.objects.create(question=q, text="Option A", is_correct=True)
            Choice.objects.create(question=q, text="Option B", is_correct=False)
        self.assertTrue(self.topic.is_assessment_ready())

        # 3. Test zero-required logic: required == 0 returns self.questions.exists()
        self.site_config.default_required_question_count = 0
        self.site_config.save()
        self.assertTrue(self.topic.is_assessment_ready())

        empty_topic = Topic.objects.create(
            subject=self.subject,
            name="Empty Topic",
            summary="No questions here.",
            order=2,
            status=Topic.STATUS_DRAFT
        )
        self.assertFalse(empty_topic.is_assessment_ready())

    def test_admin_can_add_video_to_any_topic(self):
        self.client.login(username='portaladmin', password='password123')
        post_data = {
            'title': 'Test Video Tutorial',
            'url': 'https://onedrive.live.com/test-video',
            'duration': '10:00',
            'description': 'A test video description'
        }
        response = self.client.post(reverse('portal_video_add', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 302)

        video = self.topic.videos.first()
        self.assertIsNotNone(video)
        self.assertEqual(video.title, 'Test Video Tutorial')
        self.assertEqual(video.url, 'https://onedrive.live.com/test-video')
        self.assertEqual(video.order, 1)

    def test_admin_can_add_material_to_any_topic(self):
        self.client.login(username='portaladmin', password='password123')
        post_data = {
            'title': 'Test PDF Material',
            'resource_type': 'pdf',
            'url': 'https://onedrive.live.com/test-pdf.pdf',
            'description': 'A test pdf description'
        }
        response = self.client.post(reverse('portal_material_add', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 302)

        resource = self.topic.resources.first()
        self.assertIsNotNone(resource)
        self.assertEqual(resource.title, 'Test PDF Material')
        self.assertEqual(resource.resource_type, 'pdf')
        self.assertEqual(resource.order, 1)

    def test_admin_can_add_video_url_without_https_scheme(self):
        self.client.login(username='portaladmin', password='password123')
        post_data = {
            'title': 'YouTube Video',
            'url': 'www.youtube.com/watch?v=test12345',
            'duration': '05:30',
            'description': 'URL without scheme'
        }
        response = self.client.post(reverse('portal_video_add', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 302)

        video = self.topic.videos.filter(title='YouTube Video').first()
        self.assertIsNotNone(video)
        self.assertEqual(video.url, 'https://www.youtube.com/watch?v=test12345')



    def test_assessment_required_true_0_questions_publish_rejected(self):
        self.client.login(username='portaladmin', password='password123')
        self.topic.assessment_required = True
        self.topic.status = Topic.STATUS_DRAFT
        self.topic.save()

        post_data = {
            'subject': self.subject.id,
            'name': 'Topic',
            'summary': 'Sum',
            'status': Topic.STATUS_PUBLISHED,
            'assessment_required': True,
        }
        response = self.client.post(reverse('portal_topic_edit', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 200) # Form rendered with error
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, Topic.STATUS_DRAFT)

    def test_assessment_required_true_insufficient_questions_publish_rejected(self):
        self.client.login(username='portaladmin', password='password123')
        self.topic.assessment_required = True
        self.topic.status = Topic.STATUS_DRAFT
        self.topic.save()
        from quizzes.models import Question
        Question.objects.create(topic=self.topic, question_type=Question.TYPE_SHORT_ANSWER, text='Q1')

        post_data = {
            'subject': self.subject.id,
            'name': 'Topic',
            'summary': 'Sum',
            'status': Topic.STATUS_PUBLISHED,
            'assessment_required': True,
        }
        response = self.client.post(reverse('portal_topic_edit', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 200)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, Topic.STATUS_DRAFT)

    def test_assessment_required_true_minimum_questions_publish_succeeds(self):
        self.client.login(username='portaladmin', password='password123')
        self.topic.assessment_required = True
        self.topic.status = Topic.STATUS_DRAFT
        self.topic.save()
        from quizzes.models import Question
        for i in range(self.site_config.default_required_question_count):
            Question.objects.create(topic=self.topic, question_type=Question.TYPE_SHORT_ANSWER, text=f'Q{i}')

        post_data = {
            'subject': self.subject.id,
            'name': 'Topic',
            'summary': 'Sum',
            'status': Topic.STATUS_PUBLISHED,
            'assessment_required': True,
        }
        response = self.client.post(reverse('portal_topic_edit', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 302)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, Topic.STATUS_PUBLISHED)

    def test_assessment_required_false_0_questions_publish_succeeds(self):
        self.client.login(username='portaladmin', password='password123')
        self.topic.assessment_required = False
        self.topic.status = Topic.STATUS_DRAFT
        self.topic.save()

        post_data = {
            'subject': self.subject.id,
            'name': 'Topic',
            'summary': 'Sum',
            'status': Topic.STATUS_PUBLISHED,
        }
        response = self.client.post(reverse('portal_topic_edit', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 302)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, Topic.STATUS_PUBLISHED)
        self.assertFalse(self.topic.assessment_required)

    def test_assessment_required_false_questions_publish_succeeds(self):
        self.client.login(username='portaladmin', password='password123')
        self.topic.assessment_required = False
        self.topic.status = Topic.STATUS_DRAFT
        self.topic.save()
        from quizzes.models import Question
        Question.objects.create(topic=self.topic, question_type=Question.TYPE_SHORT_ANSWER, text='Q1')

        post_data = {
            'subject': self.subject.id,
            'name': 'Topic',
            'summary': 'Sum',
            'status': Topic.STATUS_PUBLISHED,
        }
        response = self.client.post(reverse('portal_topic_edit', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 302)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, Topic.STATUS_PUBLISHED)
        self.assertFalse(self.topic.assessment_required)

    def test_assessment_required_true_to_false_publish_succeeds(self):
        self.client.login(username='portaladmin', password='password123')
        self.topic.assessment_required = True
        self.topic.status = Topic.STATUS_DRAFT
        self.topic.save()

        post_data = {
            'subject': self.subject.id,
            'name': 'Topic',
            'summary': 'Sum',
            'status': Topic.STATUS_PUBLISHED,
            # No 'assessment_required' key simulates unchecked
        }
        response = self.client.post(reverse('portal_topic_edit', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 302)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, Topic.STATUS_PUBLISHED)
        self.assertFalse(self.topic.assessment_required)

    def test_assessment_required_false_to_true_publish_rejected(self):
        self.client.login(username='portaladmin', password='password123')
        self.topic.assessment_required = False
        self.topic.status = Topic.STATUS_DRAFT
        self.topic.save()

        post_data = {
            'subject': self.subject.id,
            'name': 'Topic',
            'summary': 'Sum',
            'status': Topic.STATUS_PUBLISHED,
            'assessment_required': True,
        }
        response = self.client.post(reverse('portal_topic_edit', args=[self.topic.id]), post_data)
        self.assertEqual(response.status_code, 200)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.status, Topic.STATUS_DRAFT)
        self.assertFalse(self.topic.assessment_required)
