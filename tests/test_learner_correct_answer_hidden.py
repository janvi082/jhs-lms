import re
from django.test import TestCase, Client

from django.urls import reverse
from accounts.models import User
from content.models import SiteConfig, Subject, Topic
from quizzes.models import Question, Choice, QuizAttempt
from progress.services import submit_quiz_attempt

class LearnerCorrectAnswerHiddenTests(TestCase):
    def setUp(self):
        # Site config
        cfg = SiteConfig.get_solo()
        cfg.default_passing_score = 75
        cfg.default_required_question_count = 1
        cfg.save()
        # Learner user
        self.learner = User.objects.create_user(username='learner2', password='password123', role=User.ROLE_LEARNER)
        self.client = Client()
        self.client.login(username='learner2', password='password123')
        # Subject and topic
        self.subject = Subject.objects.create(name='Math', slug='math', is_active=True)
        self.topic = Topic.objects.create(subject=self.subject, name='Algebra', is_active=True)
        # Create a single‑choice question with a known correct answer
        self.question = Question.objects.create(
            topic=self.topic,
            text='What is 3 + 4?',
            question_type=Question.TYPE_SINGLE_CHOICE,
            order=1,
            required=True,
        )
        self.correct_choice = Choice.objects.create(question=self.question, text='7', is_correct=True)
        self.wrong_choice = Choice.objects.create(question=self.question, text='8', is_correct=False)
        self.quiz_url = reverse('quiz_modal', kwargs={'slug': self.subject.slug, 'topic_id': self.topic.id})

    def test_incorrect_answer_hides_correct_choice(self):
        # Create attempt with wrong answer via service
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, {self.question.id: self.wrong_choice.id})
        result_url = reverse('quiz_result', kwargs={'slug': self.subject.slug, 'topic_id': self.topic.id, 'attempt_id': attempt.id})
        result_page = self.client.get(result_url)
        self.assertEqual(result_page.status_code, 200)
        # Learner's answer should be shown
        # Ensure the correct answer value itself is not rendered in the learner‑facing answer span
        self.assertNotContains(result_page, f'>{self.correct_choice.text}<')
        # Correct answer must NOT be present in HTML

        # Verify internal scoring is correct
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, 0)
        self.assertFalse(attempt.passed)
        self.assertEqual(attempt.correct_count, 0)

    def test_correct_answer_hides_correct_choice_text(self):
        # Create attempt with correct answer via service
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, {self.question.id: self.correct_choice.id})
        result_url = reverse(
            'quiz_result',
            kwargs={
                'slug': self.subject.slug,
                'topic_id': self.topic.id,
                'attempt_id': attempt.id,
            },
        )
        result_page = self.client.get(result_url)

        # 1️⃣ HTTP 200
        self.assertEqual(result_page.status_code, 200)

        # 2️⃣ Learner’s selected answer (the correct one) must be visible
        self.assertContains(result_page, self.correct_choice.text)

        # 3️⃣ Security‑level checks – ensure no admin‑only metadata is leaked to the learner view

        self.assertNotContains(result_page, 'correctChoice')          # inline script variable
        self.assertNotContains(result_page, 'data-correct-choice')   # data attribute
        self.assertNotContains(result_page, 'is_correct')            # any "is_correct" flag
        self.assertNotContains(result_page, 'correct_choice_text')   # context variable name

        # 4️⃣ Verify scoring information on the attempt object
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, 100)
        self.assertTrue(attempt.passed)
        self.assertEqual(attempt.correct_count, 1)

    def test_admin_can_view_correct_answer(self):
        admin = User.objects.create_user(username='admin2', password='adminpass', role=User.ROLE_ADMIN, is_staff=True)
        admin_client = Client()
        admin_client.login(username='admin2', password='adminpass')
        # Generate attempt as learner
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, {self.question.id: self.correct_choice.id})
        # Admin detail page should contain the correct answer text (admin view uses a different template)
        url = reverse('admin_attempt_detail', args=[attempt.id])
        resp = admin_client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.correct_choice.text)
        self.assertContains(resp, 'Correct')
