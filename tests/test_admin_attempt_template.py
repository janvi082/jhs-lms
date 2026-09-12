
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from content.models import Subject, Topic, SiteConfig
from quizzes.models import Question, Choice, QuizAttempt
from progress.services import submit_quiz_attempt

class AdminAttemptTemplateTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='adminpass', role=User.ROLE_ADMIN, is_staff=True)
        self.learner = User.objects.create_user(username='learner', password='learnpass', role=User.ROLE_LEARNER)
        self.client = Client()
        self.client.login(username='admin', password='adminpass')
        # Ensure site config
        cfg = SiteConfig.get_solo()
        cfg.default_passing_score = 75
        cfg.default_required_question_count = 5
        cfg.save()
        self.subject = Subject.objects.create(name='Test Subject', slug='test', is_active=True)
        self.topic = Topic.objects.create(subject=self.subject, name='Test Topic', is_active=True)
        # Create questions including paragraph
        self.q_sc = Question.objects.create(topic=self.topic, text='SC?', question_type=Question.TYPE_SINGLE_CHOICE, order=1)
        self.c_sc_corr = Choice.objects.create(question=self.q_sc, text='Correct', is_correct=True)
        self.c_sc_wrong = Choice.objects.create(question=self.q_sc, text='Wrong', is_correct=False)
        self.q_para = Question.objects.create(topic=self.topic, text='Explain', question_type=Question.TYPE_PARAGRAPH, order=2)
        # Submit attempt
        submission = {
            self.q_sc.id: self.c_sc_corr.id,
            self.q_para.id: 'My answer',
        }
        self.attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)

    def test_detail_page_shows_responses_and_review_controls(self):
        url = reverse('admin_attempt_detail', args=[self.attempt.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # Learner answer displayed
        self.assertContains(resp, 'My answer')
        # Requires Review badge for paragraph
        self.assertContains(resp, 'Requires Review')
        # Approve/Reject buttons present
        self.assertContains(resp, 'Approve')
        self.assertContains(resp, 'Reject')
        # Correct/Wrong badge for graded question
        self.assertContains(resp, 'Correct')

    def test_sidebar_contains_attempt_history_link(self):
        resp = self.client.get(reverse('admin_attempts_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Attempt History')
        self.assertContains(resp, reverse('admin_attempts_list'))
