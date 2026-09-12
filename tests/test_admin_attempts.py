
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from content.models import Subject, Topic, SiteConfig
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from progress.services import submit_quiz_attempt, recalculate_attempt_score

class AdminAttemptTests(TestCase):
    def setUp(self):
        # Site config for required question count
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.default_required_question_count = 5
        self.site_config.save()

        # Users
        self.admin = User.objects.create_user(
            username='admin', password='adminpass', role=User.ROLE_ADMIN, is_staff=True
        )
        self.learner = User.objects.create_user(
            username='learner', password='learnpass', role=User.ROLE_LEARNER
        )

        # Subject & Topic
        self.subject = Subject.objects.create(name='Test Subject', slug='test', is_active=True)
        self.topic = Topic.objects.create(subject=self.subject, name='Test Topic', is_active=True)

        self.client = Client()

    def _create_questions(self, include_paragraph=False, multiple_choice=False, short_answer=False):
        """Utility to create a set of questions on self.topic.
        Returns a dict mapping question types to (question, correct_choice(s), wrong_choice(s)).
        """
        qmap = {}
        # Single choice
        q_sc = Question.objects.create(topic=self.topic, text='Single Choice?', question_type=Question.TYPE_SINGLE_CHOICE, order=1)
        c_sc_correct = Choice.objects.create(question=q_sc, text='Correct SC', is_correct=True)
        c_sc_wrong = Choice.objects.create(question=q_sc, text='Wrong SC', is_correct=False)
        qmap['single'] = (q_sc, [c_sc_correct.id], [c_sc_wrong.id])
        # Multiple choice (if requested)
        if multiple_choice:
            q_mc = Question.objects.create(topic=self.topic, text='Multiple Choice?', question_type=Question.TYPE_MULTIPLE_CHOICE, order=2)
            c_mc_correct1 = Choice.objects.create(question=q_mc, text='Correct MC 1', is_correct=True)
            c_mc_correct2 = Choice.objects.create(question=q_mc, text='Correct MC 2', is_correct=True)
            c_mc_wrong = Choice.objects.create(question=q_mc, text='Wrong MC', is_correct=False)
            qmap['multiple'] = (q_mc, [c_mc_correct1.id, c_mc_correct2.id], [c_mc_wrong.id])
        # True/False
        q_tf = Question.objects.create(topic=self.topic, text='True or False?', question_type=Question.TYPE_TRUE_FALSE, order=3)
        c_tf_true = Choice.objects.create(question=q_tf, text='True', is_correct=True)
        c_tf_false = Choice.objects.create(question=q_tf, text='False', is_correct=False)
        qmap['tf'] = (q_tf, [c_tf_true.id], [c_tf_false.id])
        # Short answer (if requested)
        if short_answer:
            q_sa = Question.objects.create(
                topic=self.topic,
                text='Short Answer?',
                question_type=Question.TYPE_SHORT_ANSWER,
                accepted_answers='VAT\nValue Added Tax\nvalue-added tax',
                order=4,
            )
            qmap['short'] = (q_sa, None, None)
        # Paragraph (optional)
        if include_paragraph:
            q_para = Question.objects.create(topic=self.topic, text='Explain...', question_type=Question.TYPE_PARAGRAPH, order=5)
            qmap['paragraph'] = (q_para, None, None)
        return qmap

    def test_admin_attempt_list_access_and_display(self):
        qmap = self._create_questions(include_paragraph=True)
        submission = {
            qmap['single'][0].id: qmap['single'][1][0],
            qmap['tf'][0].id: qmap['tf'][1][0],
            qmap['paragraph'][0].id: 'My paragraph answer',
        }
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        self.client.login(username='admin', password='adminpass')
        resp = self.client.get(reverse('admin_attempts_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, attempt.user.username)
        self.assertContains(resp, 'Requires Review')
        self.assertContains(resp, '100%')
        self.client.logout()
        self.client.login(username='learner', password='learnpass')
        resp2 = self.client.get(reverse('admin_attempts_list'))
        self.assertNotEqual(resp2.status_code, 200)
        self.assertTrue(resp2.status_code in (302, 403))

    def test_admin_attempt_detail_and_status_display(self):
        qmap = self._create_questions(include_paragraph=True)
        submission = {
            qmap['single'][0].id: qmap['single'][1][0],
            qmap['tf'][0].id: qmap['tf'][2][0],
            qmap['paragraph'][0].id: 'Paragraph answer',
        }
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        self.client.login(username='admin', password='adminpass')
        url = reverse('admin_attempt_detail', args=[attempt.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Correct')
        self.assertContains(resp, 'Wrong')
        self.assertContains(resp, 'Requires Review')
        self.assertContains(resp, 'action="approve"')
        self.assertContains(resp, 'action="reject"')
        self.assertEqual(resp.content.decode().count('action="approve"'), 1)
        self.assertEqual(resp.content.decode().count('action="reject"'), 1)

    def test_approve_flow_updates_score_and_counts(self):
        qmap = self._create_questions(include_paragraph=True)
        submission = {
            qmap['single'][0].id: qmap['single'][1][0],
            qmap['tf'][0].id: qmap['tf'][2][0],
            qmap['paragraph'][0].id: 'Answer',
        }
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        self.assertEqual(attempt.correct_count, 1)
        self.assertFalse(attempt.passed)
        self.assertEqual(attempt.score, 50)
        self.client.login(username='admin', password='adminpass')
        review_url = reverse('admin_attempt_review', args=[attempt.id, attempt.responses.get(is_correct__isnull=True).id])
        resp = self.client.post(review_url, {'action': 'approve'}, follow=True)
        self.assertEqual(resp.status_code, 200)
        attempt.refresh_from_db()
        self.assertEqual(attempt.correct_count, 2)
        self.assertFalse(attempt.passed)
        self.assertEqual(attempt.score, 67)
        paragraph_resp = attempt.responses.get(question__question_type=Question.TYPE_PARAGRAPH)
        self.assertEqual(paragraph_resp.text_response, 'Answer')
        self.assertContains(resp, 'Response approved as correct')
        self.assertEqual(
            resp.redirect_chain[-1][0],
            reverse('admin_attempt_detail', kwargs={'attempt_id': attempt.id})
        )
        # self.assertEqual(
        #     resp.status_code,
        #     302
        # )
        # self.assertEqual(
        #  resp.url,
        #  reverse('admin_attempt_detail', kwargs={'attempt_id': attempt.id})
        # )
        # self.assertIn(reverse('admin_learner_topic_attempts', kwargs={'learner_id': attempt.user.id, 'topic_id': attempt.topic.id}), resp.request['PATH_INFO'])

    def test_reject_flow_updates_score_and_counts(self):
        qmap = self._create_questions(include_paragraph=True)
        submission = {
            qmap['single'][0].id: qmap['single'][1][0],
            qmap['tf'][0].id: qmap['tf'][2][0],
            qmap['paragraph'][0].id: 'Answer',
        }
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        self.client.login(username='admin', password='adminpass')
        review_url = reverse('admin_attempt_review', args=[attempt.id, attempt.responses.get(is_correct__isnull=True).id])
        resp = self.client.post(review_url, {'action': 'reject'}, follow=True)
        attempt.refresh_from_db()
        self.assertEqual(attempt.correct_count, 1)
        self.assertFalse(attempt.passed)
        self.assertEqual(attempt.score, 33)
        self.assertContains(resp, 'Response marked as incorrect')

    def test_review_safety_prevents_modifying_graded_responses(self):
        qmap = self._create_questions()
        submission = {qmap['single'][0].id: qmap['single'][1][0]}
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        response = attempt.responses.get()
        self.assertTrue(response.is_correct)
        self.client.login(username='admin', password='adminpass')
        review_url = reverse('admin_attempt_review', args=[attempt.id, response.id])
        resp = self.client.post(review_url, {'action': 'approve'}, follow=True)
        self.assertContains(resp, 'already been graded')
        response.refresh_from_db()
        self.assertTrue(response.is_correct)

    def test_all_paragraph_questions_display_NA_and_no_fail(self):
        q_para = Question.objects.create(topic=self.topic, text='Explain X', question_type=Question.TYPE_PARAGRAPH, order=1)
        submission = {q_para.id: 'My explanation'}
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        self.client.login(username='admin', password='adminpass')
        list_resp = self.client.get(reverse('admin_attempts_list'))
        self.assertContains(list_resp, 'N/A')
        self.assertNotContains(list_resp, '0%')
        detail_resp = self.client.get(reverse('admin_attempt_detail', args=[attempt.id]))
        self.assertContains(detail_resp, 'N/A')
        self.assertContains(detail_resp, 'Requires Review')

    def test_protected_question_deletion_shows_friendly_message(self):
        qmap = self._create_questions()
        submission = {qmap['single'][0].id: qmap['single'][1][0]}
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        self.client.login(username='admin', password='adminpass')
        delete_url = reverse('portal_question_delete', args=[qmap['single'][0].id])
        resp = self.client.post(delete_url, follow=True)
        self.assertContains(resp, 'Cannot delete this question because it is referenced by existing quiz attempts')
        self.assertTrue(Question.objects.filter(id=qmap['single'][0].id).exists())

    def test_existing_grading_edge_cases(self):
        qmap = self._create_questions(multiple_choice=True)
        q_mc = qmap['multiple'][0]
        submission = {q_mc.id: [qmap['multiple'][1][0]]}
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        resp = attempt.responses.get(question=q_mc)
        self.assertFalse(resp.is_correct)
        qmap_sa = self._create_questions(short_answer=True)
        q_sa = qmap_sa['short'][0]
        for inp in ['VAT', 'vat', ' VAT ', 'Value Added Tax', 'value-added tax']:
            att, _ = submit_quiz_attempt(self.learner, self.topic, {q_sa.id: inp})
            self.assertTrue(att.responses.get(question=q_sa).is_correct)
        self.assertIsInstance(attempt.score, int)
