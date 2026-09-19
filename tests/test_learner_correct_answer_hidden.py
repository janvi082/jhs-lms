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

    def test_historical_order_preserved_after_question_reorder(self):
        # Create additional questions to establish a sequence: Q1, Q2, Q3
        # self.question is already order=1 (let's call it Q_A)
        q_b = Question.objects.create(topic=self.topic, text='Q_B', question_type=Question.TYPE_PARAGRAPH, order=2)
        q_c = Question.objects.create(topic=self.topic, text='Q_C', question_type=Question.TYPE_PARAGRAPH, order=3)
        
        # Learner submits attempt with the current order: Q_A, Q_B, Q_C
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, {
            self.question.id: self.correct_choice.id,
            q_b.id: 'ans_b',
            q_c.id: 'ans_c'
        })
        
        # Admin reorders questions in the database so the new order is Q_C, Q_A, Q_B
        q_c.order = 1
        q_c.save()
        self.question.order = 2
        self.question.save()
        q_b.order = 3
        q_b.save()
        
        # Fetch the historical result page
        result_url = reverse('quiz_result', kwargs={'slug': self.subject.slug, 'topic_id': self.topic.id, 'attempt_id': attempt.id})
        result_page = self.client.get(result_url)
        self.assertEqual(result_page.status_code, 200)
        
        # Verify the historical display order matches the ORIGINAL attempt creation order (Q_A, Q_B, Q_C)
        # by checking the context 'question_reviews' list order
        question_reviews = result_page.context['question_reviews']
        self.assertEqual(len(question_reviews), 3)
        
        # Index 0 should be Q_A (self.question)
        self.assertEqual(question_reviews[0]['question_id'], self.question.id)
        # Index 1 should be Q_B
        self.assertEqual(question_reviews[1]['question_id'], q_b.id)
        # Index 2 should be Q_C
        self.assertEqual(question_reviews[2]['question_id'], q_c.id)
