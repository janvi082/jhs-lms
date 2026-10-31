from django.contrib.contenttypes.models import ContentType
from access.models import LearnerAccess
from progress.services import submit_quiz_attempt
from datetime import date, datetime, timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from content.models import Subject, Topic, SiteConfig, LearningPhase
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from progress.models import TopicProgress

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

    def test_unauthenticated_cannot_access_portal(self):
        response = self.client.get(reverse('portal_subjects_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

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
        }
        response = self.client.post(reverse('portal_topic_add'), post_data)
        self.assertEqual(response.status_code, 302)

        # DB assertion
        self.assertEqual(Topic.objects.count(), initial_count + 1)
        created_topic = Topic.objects.get(name='New Topic Without Status In Form')
        self.assertEqual(created_topic.status, Topic.STATUS_DRAFT)
        self.assertFalse(created_topic.is_active)
        self.assertEqual(created_topic.order, 2)
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


class LearnerProgressDetailUITests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.save()

        self.admin = User.objects.create_user(
            username='portaladmin_ui',
            password='password123',
            role=User.ROLE_ADMIN,
            is_staff=True
        )

        self.doj = date(2026, 1, 10)
        self.learner = User.objects.create_user(
            username='learner_ui',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.doj
        )

        self.subject = Subject.objects.create(name='UI Subject', order=1, is_active=True)
        self.phase = LearningPhase.objects.create(
            subject=self.subject,
            name='Phase UI',
            start_day=1,
            end_day=15
        )

        self.topic1 = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase,
            name='Topic Assessed UI',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        self.q1 = Question.objects.create(topic=self.topic1, text='Q1', is_active=True)
        Choice.objects.create(question=self.q1, text='Correct', is_correct=True)

        self.topic2 = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase,
            name='Topic Non-Assessed UI',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )

        self.client = Client()
        self.client.login(username='portaladmin_ui', password='password123')

    def test_learner_progress_detail_success_and_context(self):
        url = reverse('portal_learner_progress_detail', args=[self.learner.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('subjects_breakdown', response.context)
        self.assertIn('overall_progress', response.context)
        self.assertIn('summary_stats', response.context)
        self.assertIn('status_strip', response.context)
        self.assertIn('learner_day', response.context)
        self.assertEqual(response.context['learner'], self.learner)
        self.assertContains(response, 'Overdue')
        self.assertContains(response, 'Due soon')
        self.assertContains(response, 'Pending review')
        self.assertContains(response, 'Phases complete')
        self.assertNotContains(response, 'Completed Topics')
        self.assertNotContains(response, 'Latest Score')

    def test_phase_performance_badge_completed_on_time_rendered(self):
        # Complete all topics on time with frozen deadlines
        completion_time = timezone.make_aware(datetime(2026, 1, 20, 10, 0))
        frozen_dl = date(2026, 1, 24)
        TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=completion_time,
            frozen_deadline=frozen_dl
        )
        TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic2,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=completion_time,
            frozen_deadline=frozen_dl
        )

        url = reverse('portal_learner_progress_detail', args=[self.learner.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Simplified phase presentation shows 'Completed' with restrained green styling
        self.assertContains(response, 'Completed')
        self.assertContains(response, 'text-success')
        self.assertContains(response, 'bg-success-subtle')
        self.assertNotContains(response, 'Completed On Time')
        self.assertEqual(response.context['status_strip']['phases_completed'], 1)

    def test_phase_performance_badge_overdue_rendered(self):
        # Learner DOJ was long ago, no topics completed -> overdue
        self.learner.date_of_joining = timezone.localdate() - timedelta(days=60)
        self.learner.save()

        url = reverse('portal_learner_progress_detail', args=[self.learner.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Overdue')
        self.assertContains(response, 'bg-danger')

    def test_topic_frozen_deadline_display_and_null_fallback(self):
        # Topic 1: Completed with valid frozen deadline
        TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now(),
            frozen_deadline=date(2026, 1, 24)
        )
        # Topic 2: Legacy completed topic with null frozen deadline
        TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic2,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now(),
            frozen_deadline=None
        )

        # Verify underlying model / backend field remains intact
        prog1 = TopicProgress.objects.get(user=self.learner, topic=self.topic1)
        self.assertEqual(prog1.frozen_deadline, date(2026, 1, 24))
        prog2 = TopicProgress.objects.get(user=self.learner, topic=self.topic2)
        self.assertIsNone(prog2.frozen_deadline)

        url = reverse('portal_learner_progress_detail', args=[self.learner.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Frozen Deadline column and per-row unavailable pills are removed from the UI
        self.assertNotContains(response, 'Frozen Deadline')
        self.assertNotContains(response, 'Unavailable')

    def test_established_completion_preserved_when_retake_pending_review(self):
        # Established completion
        TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now() - timedelta(days=5),
            frozen_deadline=date(2026, 1, 24),
            attempts_count=1,
            best_score=100,
            latest_score=100
        )
        att1 = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att1, question=self.q1, is_correct=True)

        # Retake attempt with ungraded paragraph question
        att2 = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=2,
            correct_count=0,
            total_questions=1,
            score=0,
            passed=False,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att2, question=self.q1, is_correct=None)

        url = reverse('portal_learner_progress_detail', args=[self.learner.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Established completion is preserved as 'Completed'
        self.assertContains(response, 'Completed')
        # Retake pending review indicator is rendered with unified 'Pending review' wording
        self.assertContains(response, 'Pending review')
        self.assertNotContains(response, 'Retake Pending')
        self.assertEqual(response.context['status_strip']['pending_review_topics'], 1)


class PortalProgressOverviewTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.save()

        # Admin user
        self.admin = User.objects.create_user(
            username='cohort_admin',
            password='password123',
            role=User.ROLE_ADMIN,
            is_staff=True
        )

        # Active Subjects and Non-overlapping Phases
        self.subject = Subject.objects.create(name='Cohort Subject', order=1, is_active=True)

        self.phase1 = LearningPhase.objects.create(
            subject=self.subject,
            name='Phase 1 Intro',
            start_day=1,
            end_day=7
        )

        self.phase2 = LearningPhase.objects.create(
            subject=self.subject,
            name='Phase 2 Advanced',
            start_day=8,
            end_day=14
        )

        # Published active non-assessed topics
        self.topic1 = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase1,
            name='Topic 1',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )

        self.topic2 = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase2,
            name='Topic 2',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )

        # Future subject with future phase for 'upcoming' status test
        self.future_subject = Subject.objects.create(name='Future Subject', order=2, is_active=True)
        self.future_phase = LearningPhase.objects.create(
            subject=self.future_subject,
            name='Future Phase',
            start_day=50,
            end_day=60
        )
        self.future_topic = Topic.objects.create(
            subject=self.future_subject,
            learning_phase=self.future_phase,
            name='Future Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )

        self.today = timezone.localdate()

        # 1. On-track learner: joined 2 days ago (Day 3), in Phase 1 (Day 1-7)
        self.learner_on_track = User.objects.create_user(
            username='learner_ontrack',
            first_name='Alice',
            last_name='OnTrack',
            email='alice@example.com',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.today - timedelta(days=2)
        )
        LearnerAccess.objects.create(
            learner=self.learner_on_track,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.future_subject.id,
            is_allowed=False
        )

        # 2. Overdue learner: joined 20 days ago (Day 21), Phase 1 (Day 1-7) incomplete
        self.learner_overdue = User.objects.create_user(
            username='learner_overdue',
            first_name='Bob',
            last_name='Overdue',
            email='bob@example.com',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.today - timedelta(days=20)
        )
        LearnerAccess.objects.create(
            learner=self.learner_overdue,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.future_subject.id,
            is_allowed=False
        )

        # 3. Due soon learner: joined 6 days ago (Day 7, due today for Phase 1 Day 1-7)
        self.learner_due_soon = User.objects.create_user(
            username='learner_duesoon',
            first_name='Charlie',
            last_name='DueSoon',
            email='charlie@example.com',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.today - timedelta(days=6)
        )
        LearnerAccess.objects.create(
            learner=self.learner_due_soon,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.future_subject.id,
            is_allowed=False
        )

        # 4. Completed learner: both topics completed on time
        self.learner_completed = User.objects.create_user(
            username='learner_completed',
            first_name='Diana',
            last_name='Completed',
            email='diana@example.com',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.today - timedelta(days=30)
        )
        LearnerAccess.objects.create(
            learner=self.learner_completed,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.future_subject.id,
            is_allowed=False
        )
        TopicProgress.objects.create(
            user=self.learner_completed,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now() - timedelta(days=25),
            frozen_deadline=self.today - timedelta(days=24),
            expected_end_day_snapshot=7
        )
        TopicProgress.objects.create(
            user=self.learner_completed,
            topic=self.topic2,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now() - timedelta(days=18),
            frozen_deadline=self.today - timedelta(days=17),
            expected_end_day_snapshot=14
        )

        # 5. Missing DOJ learner
        self.learner_no_doj = User.objects.create_user(
            username='learner_nodoj',
            first_name='Evan',
            last_name='NoDOJ',
            email='evan@example.com',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=None
        )
        LearnerAccess.objects.create(
            learner=self.learner_no_doj,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.future_subject.id,
            is_allowed=False
        )

        # 6. Timing unavailable learner: joined 2 days ago (Day 3).
        # Phase 1 is completed timing unavailable (missing timestamps).
        # Phase 2 (Day 8-14) is upcoming.
        # Learner is neither all-completed nor all-upcoming -> primary_status = 'timing_unavailable'.
        self.learner_timing_unavail = User.objects.create_user(
            username='learner_timing',
            first_name='Fiona',
            last_name='Timing',
            email='fiona@example.com',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.today - timedelta(days=2)
        )
        LearnerAccess.objects.create(
            learner=self.learner_timing_unavail,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.future_subject.id,
            is_allowed=False
        )
        TopicProgress.objects.create(
            user=self.learner_timing_unavail,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=None,
            frozen_deadline=None,
            expected_end_day_snapshot=None
        )

        # 7. No assigned phases learner (denied access to all subjects with phases)
        self.learner_no_phase = User.objects.create_user(
            username='learner_nophase',
            first_name='George',
            last_name='NoPhase',
            email='george@example.com',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.today
        )
        LearnerAccess.objects.create(
            learner=self.learner_no_phase,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.subject.id,
            is_allowed=False
        )
        LearnerAccess.objects.create(
            learner=self.learner_no_phase,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.future_subject.id,
            is_allowed=False
        )

        # 8. Upcoming learner (only assigned future subject; joined today Day 1 < 50)
        self.learner_upcoming = User.objects.create_user(
            username='learner_upcoming',
            first_name='Hannah',
            last_name='Upcoming',
            email='hannah@example.com',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.today
        )
        LearnerAccess.objects.create(
            learner=self.learner_upcoming,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.subject.id,
            is_allowed=False
        )

        self.client = Client()
        self.client.login(username='cohort_admin', password='password123')

    def test_admin_only_access_and_authorization(self):
        client_anon = Client()
        url = reverse('portal_progress')
        res_anon = client_anon.get(url)
        self.assertEqual(res_anon.status_code, 302)
        self.assertIn(reverse('login'), res_anon['Location'])

        client_learner = Client()
        client_learner.login(username='learner_ontrack', password='password123')
        res_learner = client_learner.get(url)
        self.assertEqual(res_learner.status_code, 302)
        self.assertIn(reverse('learner_dashboard'), res_learner['Location'])

        res_admin = self.client.get(url)
        self.assertEqual(res_admin.status_code, 200)
        self.assertTemplateUsed(res_admin, 'portal/progress_list.html')
        self.assertContains(res_admin, 'Learner Progress')

    def test_cohort_kpi_totals(self):
        url = reverse('portal_progress')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        summary = res.context['summary']
        # Total learners count: exactly 8 learners
        self.assertEqual(summary['total_learners_count'], 8)
        self.assertEqual(summary['learner_kpis']['on_track'], 1)
        self.assertEqual(summary['learner_kpis']['overdue'], 1)
        self.assertEqual(summary['learner_kpis']['due_soon'], 1)
        self.assertEqual(summary['learner_kpis']['completed_all'], 1)
        self.assertEqual(summary['learner_kpis']['upcoming'], 1)
        self.assertEqual(summary['learner_kpis']['timing_unavailable'], 1)
        self.assertEqual(summary['learner_kpis']['no_phase'], 1)
        self.assertEqual(summary['learner_kpis']['no_doj'], 1)

        # On-time rate display and corrected subtitle are rendered
        self.assertIn('verified_on_time_rate_display', summary['phase_kpis'])
        self.assertContains(res, summary['phase_kpis']['verified_on_time_rate_display'])
        self.assertContains(res, 'On-Time Rate')
        self.assertContains(res, f"{summary['phase_kpis']['verified_numerator']} of {summary['phase_kpis']['verified_denominator']} eligible phases completed on time")
        self.assertNotContains(res, 'verified phases')

        # Completed phases with unverified timing metric removed
        self.assertNotContains(res, 'Completed phases with unverified timing:')
        self.assertNotContains(res, '0 learners (6 phases)')

    def test_kpis_remain_unchanged_when_table_filters_active(self):
        url = reverse('portal_progress')
        res_filtered = self.client.get(url, {'status': 'overdue'})
        self.assertEqual(res_filtered.status_code, 200)

        # Table is filtered to only the overdue learner
        self.assertEqual(len(res_filtered.context['learners_data']), 1)
        self.assertEqual(res_filtered.context['learners_data'][0]['learner'].username, 'learner_overdue')

        # Cohort summary values remain unaffected by table filter
        summary = res_filtered.context['summary']
        self.assertEqual(summary['total_learners_count'], 8)
        self.assertEqual(summary['learner_kpis']['on_track'], 1)
        self.assertEqual(summary['learner_kpis']['overdue'], 1)
        self.assertEqual(summary['learner_kpis']['completed_all'], 1)

    def test_search_filters_name_username_and_email(self):
        url = reverse('portal_progress')

        # By First Name
        res_name = self.client.get(url, {'q': 'Alice'})
        self.assertEqual(len(res_name.context['learners_data']), 1)
        self.assertEqual(res_name.context['learners_data'][0]['learner'].username, 'learner_ontrack')

        # By Username
        res_user = self.client.get(url, {'q': 'learner_overdue'})
        self.assertEqual(len(res_user.context['learners_data']), 1)
        self.assertEqual(res_user.context['learners_data'][0]['learner'].username, 'learner_overdue')

        # By Email
        res_email = self.client.get(url, {'q': 'charlie@example.com'})
        self.assertEqual(len(res_email.context['learners_data']), 1)
        self.assertEqual(res_email.context['learners_data'][0]['learner'].username, 'learner_duesoon')

        # Case-insensitive
        res_ci = self.client.get(url, {'q': 'DIANA'})
        self.assertEqual(len(res_ci.context['learners_data']), 1)
        self.assertEqual(res_ci.context['learners_data'][0]['learner'].username, 'learner_completed')

    def test_every_supported_status_filter(self):
        url = reverse('portal_progress')

        status_expectations = {
            'overdue': 'learner_overdue',
            'due_soon': 'learner_duesoon',
            'on_track': 'learner_ontrack',
            'completed': 'learner_completed',
            'completed_all': 'learner_completed',
            'upcoming': 'learner_upcoming',
            'missing_joining_date': 'learner_nodoj',
            'no_doj': 'learner_nodoj',
            'no_assigned_phases': 'learner_nophase',
            'no_phase': 'learner_nophase',
            'timing_unavailable': 'learner_timing',
        }

        for param_val, expected_username in status_expectations.items():
            res = self.client.get(url, {'status': param_val})
            self.assertEqual(res.status_code, 200, f"Failed on status filter: {param_val}")
            usernames = [x['learner'].username for x in res.context['learners_data']]
            self.assertIn(expected_username, usernames, f"Expected {expected_username} in {usernames} for status={param_val}")

        # Status=all returns all learners
        res_all = self.client.get(url, {'status': 'all'})
        self.assertEqual(len(res_all.context['learners_data']), 8)

    def test_learning_phase_filter(self):
        url = reverse('portal_progress')

        # Filter by phase1: learners who have phase1 assigned
        res = self.client.get(url, {'phase': self.phase1.id})
        self.assertEqual(res.status_code, 200)
        usernames = [x['learner'].username for x in res.context['learners_data']]
        self.assertIn('learner_ontrack', usernames)
        self.assertNotIn('learner_nophase', usernames)
        self.assertNotIn('learner_upcoming', usernames)

        # Invalid phase id safely handled
        res_invalid = self.client.get(url, {'phase': 999999})
        self.assertEqual(res_invalid.status_code, 200)
        self.assertEqual(res_invalid.context['phase_filter'], '')

        # Non-numeric phase safely handled
        res_str = self.client.get(url, {'phase': 'invalid_string'})
        self.assertEqual(res_str.status_code, 200)
        self.assertEqual(res_str.context['phase_filter'], '')

    def test_sorting_options(self):
        url = reverse('portal_progress')

        # Default needs_attention: overdue learner is first
        res_attn = self.client.get(url, {'sort': 'needs_attention'})
        self.assertEqual(res_attn.status_code, 200)
        self.assertEqual(res_attn.context['learners_data'][0]['learner'].username, 'learner_overdue')

        # Name A-Z
        res_name = self.client.get(url, {'sort': 'name_asc'})
        self.assertEqual(res_name.status_code, 200)
        names = [(x['learner'].get_full_name() or x['learner'].username).lower() for x in res_name.context['learners_data']]
        self.assertEqual(names, sorted(names))

        # Progress high to low
        res_prog = self.client.get(url, {'sort': 'progress_desc'})
        self.assertEqual(res_prog.status_code, 200)
        progs = [x['overall_progress'] for x in res_prog.context['learners_data']]
        self.assertEqual(progs, sorted(progs, reverse=True))

        # Invalid sort falls back to needs_attention
        res_inv = self.client.get(url, {'sort': 'unexpected_sort_key'})
        self.assertEqual(res_inv.status_code, 200)
        self.assertEqual(res_inv.context['sort_by'], 'needs_attention')

    def test_empty_results_and_clear_filters(self):
        url = reverse('portal_progress')
        res = self.client.get(url, {'q': 'completely_nonexistent_search_query_123'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.context['learners_data']), 0)
        self.assertContains(res, 'No learners match your criteria')
        self.assertContains(res, 'Clear all filters')

    def test_edge_cases_no_doj_no_phase_timing_unavailable(self):
        url = reverse('portal_progress')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        # Primary status badges are rendered
        self.assertContains(res, 'DOJ Not Set')
        self.assertContains(res, 'No Phases Assigned')
        self.assertContains(res, 'Timing Unavailable')

        # Alice joined 2 days ago: learning day is 3
        self.assertContains(res, 'Day 3')

        # Fiona (learner_timing_unavail) completed Phase 1 without timestamps
        # Verified counted in completed count and displayed with clear completed badge
        self.assertContains(res, '1 of 2 phases completed')
        self.assertContains(res, '1 completed — timing unverified')
        self.assertNotContains(res, '1 unverified')

    def test_learner_with_unverified_completed_phase_remains_completed_and_not_displayed_as_incomplete(self):
        """
        Verify that a learner with a completed phase lacking frozen deadline evidence:
        1. Keeps that phase counted as completed in 'X of Y phases completed'.
        2. Renders the badge as '1 completed — timing unverified' (not an ambiguous '1 unverified').
        3. Does not display the completed phase as an additional incomplete or overdue phase.
        """
        learner_mixed = User.objects.create_user(
            username='learner_mixed_unverified',
            first_name='Maya',
            last_name='Mixed',
            email='maya@example.com',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.today - timedelta(days=20)
        )
        LearnerAccess.objects.create(
            learner=learner_mixed,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.future_subject.id,
            is_allowed=False
        )
        # Completed Phase 1 topic with missing timestamps / frozen_deadline
        TopicProgress.objects.create(
            user=learner_mixed,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=None,
            frozen_deadline=None,
            expected_end_day_snapshot=None
        )

        url = reverse('portal_progress')
        res = self.client.get(url, {'q': 'Maya'})
        self.assertEqual(res.status_code, 200)

        # Context assertions
        learners = res.context['learners_data']
        self.assertEqual(len(learners), 1)
        target = learners[0]
        self.assertEqual(target['learner'].username, 'learner_mixed_unverified')
        self.assertEqual(target['primary_status'], 'overdue')
        self.assertEqual(target['completed_phases_count'], 1)
        self.assertEqual(target['total_phases_count'], 2)
        self.assertEqual(target['phase_counts']['completed_timing_unavailable'], 1)
        self.assertEqual(target['phase_counts']['overdue'], 1)
        self.assertEqual(target['phase_counts']['incomplete_timing_unavailable'], 0)

        # HTML assertions
        self.assertContains(res, '1 of 2 phases completed')
        self.assertContains(res, '1 overdue')
        self.assertContains(res, '1 completed — timing unverified')
        self.assertNotContains(res, '2 overdue')
        self.assertNotContains(res, '1 unverified')

    def test_view_progress_navigation_link(self):
        url = reverse('portal_progress')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        expected_detail_url = reverse('portal_learner_progress_detail', args=[self.learner_on_track.id])
        self.assertContains(res, expected_detail_url)

        # Verify navigating to that URL returns 200 OK
        detail_res = self.client.get(expected_detail_url)
        self.assertEqual(detail_res.status_code, 200)

    def test_completed_learner_remains_completed_after_later_failed_retake(self):
        # Assessable topic
        topic_assessed = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase1,
            name='Assessed Topic',
            order=10,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q = Question.objects.create(topic=topic_assessed, text='Q1', is_active=True, required=True)
        c_right = Choice.objects.create(question=q, text='Correct', is_correct=True)
        c_wrong = Choice.objects.create(question=q, text='Wrong', is_correct=False)

        # Passing attempt
        att1, tp1 = submit_quiz_attempt(self.learner_completed, topic_assessed, {q.id: c_right.id})
        self.assertTrue(att1.passed)
        self.assertEqual(tp1.status, TopicProgress.STATUS_COMPLETED)

        # Failing retake
        att2, tp2 = submit_quiz_attempt(self.learner_completed, topic_assessed, {q.id: c_wrong.id})
        self.assertFalse(att2.passed)
        self.assertEqual(tp2.status, TopicProgress.STATUS_COMPLETED)

        # Overview page continues to show completed
        url = reverse('portal_progress')
        res = self.client.get(url, {'q': 'Diana'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['learners_data'][0]['primary_status'], 'completed_all')

    def test_query_count_constant_guard_against_n_plus_one(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        url = reverse('portal_progress')

        # Warm up query cache / session
        self.client.get(url)

        # Baseline query count with existing learners
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(url)
        baseline_queries = len(baseline)

        # Add 5 more learners with progress records
        for i in range(5):
            u = User.objects.create_user(
                username=f'extra_learner_{i}',
                password='password123',
                role=User.ROLE_LEARNER,
                date_of_joining=self.today - timedelta(days=i)
            )
            TopicProgress.objects.create(
                user=u,
                topic=self.topic1,
                status=TopicProgress.STATUS_COMPLETED,
                completed_at=timezone.now(),
                frozen_deadline=self.today,
                expected_end_day_snapshot=7
            )

        # Query count MUST REMAIN IDENTICAL (Zero queries per learner loop)
        with self.assertNumQueries(baseline_queries):
            self.client.get(url)

    def _create_verified_completed_and_upcoming_learner(self, username='learner_clean_mixed'):
        """
        Helper fixture: creates a learner with:
        - Phase 1 (Day 1-7): Completed on time with verified timestamps and frozen deadline.
        - Phase 2 (Day 8-14): Upcoming (joined 2 days ago, currently on Day 3 < 8).
        - Future subject denied access.
        """
        learner = User.objects.create_user(
            username=username,
            first_name='Kiran',
            last_name='CleanMixed',
            email=f'{username}@example.com',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.today - timedelta(days=2)
        )
        LearnerAccess.objects.create(
            learner=learner,
            content_type=ContentType.objects.get_for_model(Subject),
            object_id=self.future_subject.id,
            is_allowed=False
        )
        TopicProgress.objects.create(
            user=learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now() - timedelta(days=1),
            frozen_deadline=self.today + timedelta(days=4),
            expected_end_day_snapshot=7,
            timing_provenance=TopicProgress.PROVENANCE_SYSTEM_CAPTURED
        )
        return learner

    def test_verified_completed_phase_with_upcoming_phase_classified_as_upcoming(self):
        """
        1. Verified completed phase(s) + Upcoming phase:
        primary status must be upcoming, not timing_unavailable.
        """
        learner = self._create_verified_completed_and_upcoming_learner('learner_verified_mixed')
        url = reverse('portal_progress')
        res = self.client.get(url, {'q': learner.username})
        self.assertEqual(res.status_code, 200)

        learners = res.context['learners_data']
        self.assertEqual(len(learners), 1)
        target = learners[0]
        self.assertEqual(target['learner'].username, learner.username)
        self.assertEqual(target['primary_status'], 'upcoming')
        self.assertEqual(target['primary_status_label'], 'Upcoming')
        self.assertEqual(target['completed_phases_count'], 1)
        self.assertEqual(target['total_phases_count'], 2)
        self.assertEqual(target['phase_counts']['completed_on_time'], 1)
        self.assertEqual(target['phase_counts']['upcoming'], 1)
        self.assertEqual(target['phase_counts']['completed_timing_unavailable'], 0)
        self.assertEqual(target['phase_counts']['incomplete_timing_unavailable'], 0)

        # HTML assertions
        self.assertContains(res, '1 of 2 phases completed')
        self.assertContains(res, 'Upcoming')
        self.assertNotEqual(target['primary_status'], 'timing_unavailable')

    def test_unverified_completed_phase_with_upcoming_phase_remains_timing_unavailable(self):
        """
        2. Unverified completed phase + Upcoming phase:
        primary status must remain timing_unavailable.
        """
        url = reverse('portal_progress')
        res = self.client.get(url, {'q': self.learner_timing_unavail.username})
        self.assertEqual(res.status_code, 200)

        learners = res.context['learners_data']
        self.assertEqual(len(learners), 1)
        target = learners[0]
        self.assertEqual(target['learner'].username, self.learner_timing_unavail.username)
        self.assertEqual(target['primary_status'], 'timing_unavailable')
        self.assertEqual(target['primary_status_label'], 'Timing Unavailable')
        self.assertEqual(target['completed_phases_count'], 1)
        self.assertEqual(target['total_phases_count'], 2)
        self.assertEqual(target['phase_counts']['completed_timing_unavailable'], 1)
        self.assertEqual(target['phase_counts']['upcoming'], 1)

    def test_upcoming_status_filter_includes_verified_completed_and_upcoming_learner(self):
        """
        3. Upcoming status filter:
        The mixed verified-completed/upcoming learner must appear.
        """
        learner = self._create_verified_completed_and_upcoming_learner('learner_filter_upcoming')
        url = reverse('portal_progress')
        res = self.client.get(url, {'status': 'upcoming'})
        self.assertEqual(res.status_code, 200)

        usernames = [x['learner'].username for x in res.context['learners_data']]
        self.assertIn(learner.username, usernames)
        self.assertIn(self.learner_upcoming.username, usernames)
        self.assertNotIn(self.learner_timing_unavail.username, usernames)

    def test_timing_unavailable_status_filter_excludes_clean_mixed_portfolio_learner(self):
        """
        4. Timing Unavailable status filter:
        The clean mixed portfolio learner must not appear.
        """
        learner = self._create_verified_completed_and_upcoming_learner('learner_filter_clean')
        url = reverse('portal_progress')
        res = self.client.get(url, {'status': 'timing_unavailable'})
        self.assertEqual(res.status_code, 200)

        usernames = [x['learner'].username for x in res.context['learners_data']]
        self.assertNotIn(learner.username, usernames)
        self.assertIn(self.learner_timing_unavail.username, usernames)

    def test_needs_attention_sorting_places_clean_mixed_learner_below_urgent_learners(self):
        """
        5. Needs-attention sorting:
        The clean mixed portfolio learner must not be treated as a timing-unavailable
        attention case and must remain below genuinely urgent learners.
        """
        learner = self._create_verified_completed_and_upcoming_learner('learner_sort_clean')
        url = reverse('portal_progress')
        res = self.client.get(url, {'sort': 'needs_attention'})
        self.assertEqual(res.status_code, 200)

        usernames = [x['learner'].username for x in res.context['learners_data']]
        self.assertIn(learner.username, usernames)
        self.assertIn(self.learner_overdue.username, usernames)
        self.assertIn(self.learner_due_soon.username, usernames)
        self.assertIn(self.learner_timing_unavail.username, usernames)

        idx_clean = usernames.index(learner.username)
        idx_overdue = usernames.index(self.learner_overdue.username)
        idx_due_soon = usernames.index(self.learner_due_soon.username)
        idx_timing_unavail = usernames.index(self.learner_timing_unavail.username)

        # Urgent learners (overdue: priority 0, due_soon: priority 1, timing_unavail: priority 2)
        # must appear ahead of clean upcoming learner (upcoming: priority 6)
        self.assertLess(idx_overdue, idx_clean)
        self.assertLess(idx_due_soon, idx_clean)
        self.assertLess(idx_timing_unavail, idx_clean)
