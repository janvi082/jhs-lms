from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from content.models import SiteConfig, Subject, Topic
from quizzes.models import Question, Choice, QuizAttempt

class QuizSubmissionFlowTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.default_required_question_count = 5
        self.site_config.save()

        self.learner = User.objects.create_user(
            username='learner1',
            password='password123',
            role=User.ROLE_LEARNER
        )

        self.subject = Subject.objects.create(
            name="Excel Mastery",
            slug="excel",
            description="Excel course",
            order=1,
            is_active=True
        )

        self.topic = Topic.objects.create(
            subject=self.subject,
            name="Formulas 101",
            summary="Basic formulas",
            order=1,
            is_active=True
        )

        # Create 5 questions with 1 correct choice each
        self.questions = []
        for i in range(1, 6):
            q = Question.objects.create(topic=self.topic, text=f"Question {i}", order=i)
            c1 = Choice.objects.create(question=q, text=f"Correct Choice {i}", is_correct=True)
            c2 = Choice.objects.create(question=q, text=f"Wrong Choice {i}", is_correct=False)
            self.questions.append((q, c1, c2))

        self.client = Client()
        self.client.login(username='learner1', password='password123')
        self.quiz_url = reverse('quiz_modal', kwargs={'slug': self.subject.slug, 'topic_id': self.topic.id})

    def test_quiz_get_loads_assessment_page_without_answers_in_url(self):
        response = self.client.get(self.quiz_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<form method="post"')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, 'Submit Assessment')

    def test_quiz_post_creates_attempt_and_redirects_to_result_page(self):
        # Answer all 5 questions correctly
        post_data = {str(q.id): corr.id for q, corr, wrong in self.questions}
        
        response = self.client.post(self.quiz_url, post_data)
        self.assertEqual(response.status_code, 302)
        
        attempts = QuizAttempt.objects.filter(user=self.learner, topic=self.topic)
        self.assertEqual(attempts.count(), 1)
        attempt = attempts.first()
        self.assertEqual(attempt.score, 100)
        self.assertEqual(attempt.correct_count, 5)
        self.assertEqual(attempt.total_questions, 5)
        self.assertTrue(attempt.passed)

        # Follow redirect to result page
        result_url = response.url
        res_page = self.client.get(result_url)
        self.assertEqual(res_page.status_code, 200)
        self.assertContains(res_page, '100%')
        self.assertContains(res_page, 'PASS')
        self.assertContains(res_page, 'Question Review')
        self.assertContains(res_page, 'Previous Question')
        self.assertContains(res_page, 'Next Question')

    def test_refreshing_result_page_does_not_create_duplicate_attempt(self):
        post_data = {str(q.id): corr.id for q, corr, wrong in self.questions}
        response = self.client.post(self.quiz_url, post_data)
        result_url = response.url

        self.assertEqual(QuizAttempt.objects.count(), 1)

        # Refresh result page multiple times
        self.client.get(result_url)
        self.client.get(result_url)
        self.assertEqual(QuizAttempt.objects.count(), 1)

    def test_empty_post_handled_safely(self):
        response = self.client.post(self.quiz_url, {})
        self.assertEqual(response.status_code, 302)
        attempt = QuizAttempt.objects.first()
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.score, 0)
        self.assertFalse(attempt.passed)
        self.assertEqual(attempt.correct_count, 0)
