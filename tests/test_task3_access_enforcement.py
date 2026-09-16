from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from content.models import Subject, Topic
from access.models import LearnerAccess
from quizzes.models import QuizAttempt

class Task3AccessEnforcementTests(TestCase):
    def setUp(self):
        # Create admin and learner users
        self.admin_user = User.objects.create_user(username='admin', password='adminpass', role=User.ROLE_ADMIN, is_staff=True)
        self.admin_user.save()
        self.learner_user = User.objects.create_user(username='learner', password='learnerpass', role=User.ROLE_LEARNER)
        self.client = Client()
        # Create a subject and topic
        self.subject = Subject.objects.create(name='Math', slug='math', is_active=True)
        self.topic = Topic.objects.create(subject=self.subject, name='Algebra', summary='Algebra basics', order=1)
        # URL helpers
        # Create enough dummy questions to satisfy the required question count (5)
        from quizzes.models import Question, Choice
        for i in range(5):
            q = Question.objects.create(
                topic=self.topic,
                text=f'Sample question {i+1}',
                question_type=Question.TYPE_SINGLE_CHOICE
            )
            Choice.objects.create(question=q, text='Option A', is_correct=True)
            Choice.objects.create(question=q, text='Option B', is_correct=False)
        self.dashboard_url = reverse('learner_dashboard')
        self.subject_url = reverse('subject_detail', args=[self.subject.slug])
        self.topic_url = reverse('topic_detail', args=[self.subject.slug, self.topic.id])
        self.quiz_url = reverse('quiz_modal', args=[self.subject.slug, self.topic.id])

    def _deny_subject(self):
        LearnerAccess.objects.create(learner=self.learner_user, content_object=self.subject, is_allowed=False)

    def _deny_topic(self):
        LearnerAccess.objects.create(learner=self.learner_user, content_object=self.topic, is_allowed=False)

    def test_A_denied_subject_dashboard_and_direct(self):
        self._deny_subject()
        self.client.login(username='learner', password='learnerpass')
        response = self.client.get(self.dashboard_url)
        self.assertNotContains(response, self.subject.name)
        response = self.client.get(self.subject_url)
        self.assertEqual(response.status_code, 403)

    def test_B_denied_topic_direct(self):
        self._deny_topic()
        self.client.login(username='learner', password='learnerpass')
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 403)

    def test_C_parent_denial_topic(self):
        self._deny_subject()
        self.client.login(username='learner', password='learnerpass')
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 403)

    def test_D_denied_topic_quiz_get(self):
        self._deny_topic()
        self.client.login(username='learner', password='learnerpass')
        response = self.client.get(self.quiz_url)
        self.assertEqual(response.status_code, 403)

    def test_E_denied_topic_quiz_result_after_attempt(self):
        self.client.login(username='learner', password='learnerpass')
        attempt = QuizAttempt.objects.create(user=self.learner_user, topic=self.topic, attempt_number=1, correct_count=0, total_questions=0, score=0, passed=False, passing_score_used=0)
        self._deny_topic()
        result_url = reverse('quiz_result', args=[self.subject.slug, self.topic.id, attempt.id])
        response = self.client.get(result_url)
        self.assertEqual(response.status_code, 403)

    def test_F_access_all_allowed(self):
        self.client.login(username='learner', password='learnerpass')
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, self.subject.name)
        response = self.client.get(self.subject_url)
        self.assertEqual(response.status_code, 200)
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 200)
        response = self.client.get(self.quiz_url)
        self.assertEqual(response.status_code, 200)
        attempt = QuizAttempt.objects.create(
    user=self.learner_user,
    topic=self.topic,
    attempt_number=1,
    correct_count=0,
    total_questions=5,
    score=0,
    passed=False,
    passing_score_used=0
)
        result_url = reverse('quiz_result', args=[self.subject.slug, self.topic.id, attempt.id])
        response = self.client.get(result_url)
        self.assertEqual(response.status_code, 200)

    def test_G_admin_sees_all(self):
        self._deny_subject()
        self._deny_topic()
        self.client.login(username='admin', password='adminpass')
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, self.subject.name)
        response = self.client.get(self.subject_url)
        self.assertEqual(response.status_code, 200)
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 200)
        response = self.client.get(self.quiz_url)
        self.assertEqual(response.status_code, 200)
        attempt = QuizAttempt.objects.create(
    user=self.admin_user,
    topic=self.topic,
    attempt_number=1,
    correct_count=0,
    total_questions=5,
    score=0,
    passed=False,
    passing_score_used=0
)
        result_url = reverse('quiz_result', args=[self.subject.slug, self.topic.id, attempt.id])
        response = self.client.get(result_url)
        self.assertEqual(response.status_code, 200)

    def test_H_subject_detail_filters_restricted_topics(self):
        # Create second active topic
        topic_b = Topic.objects.create(subject=self.subject, name='Calculus', summary='Advanced', order=2, status=Topic.STATUS_PUBLISHED)

        # Deny access to topic_b
        LearnerAccess.objects.create(learner=self.learner_user, content_object=topic_b, is_allowed=False)

        self.client.login(username='learner', password='learnerpass')
        response = self.client.get(self.subject_url)
        self.assertEqual(response.status_code, 200)

        # Topic A (self.topic) should be visible
        self.assertContains(response, self.topic.name)
        # Topic B (topic_b) should NOT be rendered
        self.assertNotContains(response, topic_b.name)

        # 2. Direct URL to Topic B still returns 403
        topic_b_url = reverse('topic_detail', args=[self.subject.slug, topic_b.id])
        response_direct = self.client.get(topic_b_url)
        self.assertEqual(response_direct.status_code, 403)

    def test_I_subject_detail_filters_previously_attempted_restricted_topic(self):
        topic_b = Topic.objects.create(subject=self.subject, name='Calculus', summary='Advanced', order=2, status=Topic.STATUS_PUBLISHED)

        # Create TopicProgress and QuizAttempt to simulate previous interaction
        from progress.models import TopicProgress
        TopicProgress.objects.create(user=self.learner_user, topic=topic_b, status=TopicProgress.STATUS_IN_PROGRESS)
        QuizAttempt.objects.create(user=self.learner_user, topic=topic_b, attempt_number=1, correct_count=0, total_questions=5, score=0, passed=False, passing_score_used=0)

        # Deny access
        LearnerAccess.objects.create(learner=self.learner_user, content_object=topic_b, is_allowed=False)

        self.client.login(username='learner', password='learnerpass')
        response = self.client.get(self.subject_url)
        self.assertEqual(response.status_code, 200)

        # Topic B should NOT be rendered, even with progress
        self.assertNotContains(response, topic_b.name)

    def test_J_subject_progress_excludes_restricted_topics(self):
        # By default, topic is active but not completed. So completed = 0 / 1.
        topic_b = Topic.objects.create(subject=self.subject, name='Calculus', summary='Advanced', order=2, status=Topic.STATUS_PUBLISHED)

        # Complete Topic A
        from progress.models import TopicProgress
        TopicProgress.objects.create(user=self.learner_user, topic=self.topic, status=TopicProgress.STATUS_COMPLETED, best_score=100, latest_score=100)

        # Learner now has 1/2 completed = 50%
        from progress.services import get_subject_progress_summary

        # Deny access to Topic B
        LearnerAccess.objects.create(learner=self.learner_user, content_object=topic_b, is_allowed=False)

        summary = get_subject_progress_summary(self.learner_user, self.subject)

        # Since Topic B is denied, total topics should be 1, completed = 1/1 = 100%
        self.assertEqual(summary['total_topics_count'], 1)
        self.assertEqual(summary['completed_topics_count'], 1)
        self.assertEqual(summary['progress_percent'], 100)
        self.assertEqual(summary['assessments_passed_display'], "1 / 1")

    def test_K_admin_sees_all_and_progress_is_unfiltered(self):
        topic_b = Topic.objects.create(subject=self.subject, name='Calculus', summary='Advanced', order=2, status=Topic.STATUS_PUBLISHED)
        LearnerAccess.objects.create(learner=self.learner_user, content_object=topic_b, is_allowed=False)

        # Complete Topic A for admin just to check
        from progress.models import TopicProgress
        TopicProgress.objects.create(user=self.admin_user, topic=self.topic, status=TopicProgress.STATUS_COMPLETED, best_score=100, latest_score=100)

        from progress.services import get_subject_progress_summary
        summary = get_subject_progress_summary(self.admin_user, self.subject)

        # Admin progress should count both active topics regardless of restrictions
        self.assertEqual(summary['total_topics_count'], 2)
        self.assertEqual(summary['completed_topics_count'], 1)
        self.assertEqual(summary['progress_percent'], 50)
        self.assertEqual(summary['assessments_passed_display'], "1 / 2")

        self.client.login(username='admin', password='adminpass')
        response = self.client.get(self.subject_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.topic.name)
        self.assertContains(response, topic_b.name)
