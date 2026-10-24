
from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from content.models import Subject, Topic, SiteConfig
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from django.utils import timezone
from progress.models import TopicProgress
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
        q_sc = Question.objects.create(required=False, topic=self.topic, text='Single Choice?', question_type=Question.TYPE_SINGLE_CHOICE, order=1)
        c_sc_correct = Choice.objects.create(question=q_sc, text='Correct SC', is_correct=True)
        c_sc_wrong = Choice.objects.create(question=q_sc, text='Wrong SC', is_correct=False)
        qmap['single'] = (q_sc, [c_sc_correct.id], [c_sc_wrong.id])
        # Multiple choice (if requested)
        if multiple_choice:
            q_mc = Question.objects.create(required=False, topic=self.topic, text='Multiple Choice?', question_type=Question.TYPE_MULTIPLE_CHOICE, order=2)
            c_mc_correct1 = Choice.objects.create(question=q_mc, text='Correct MC 1', is_correct=True)
            c_mc_correct2 = Choice.objects.create(question=q_mc, text='Correct MC 2', is_correct=True)
            c_mc_wrong = Choice.objects.create(question=q_mc, text='Wrong MC', is_correct=False)
            qmap['multiple'] = (q_mc, [c_mc_correct1.id, c_mc_correct2.id], [c_mc_wrong.id])
        # True/False
        q_tf = Question.objects.create(required=False, topic=self.topic, text='True or False?', question_type=Question.TYPE_TRUE_FALSE, order=3)
        c_tf_true = Choice.objects.create(question=q_tf, text='True', is_correct=True)
        c_tf_false = Choice.objects.create(question=q_tf, text='False', is_correct=False)
        qmap['tf'] = (q_tf, [c_tf_true.id], [c_tf_false.id])
        # Short answer (if requested)
        if short_answer:
            q_sa = Question.objects.create(required=False, 
                topic=self.topic,
                text='Short Answer?',
                question_type=Question.TYPE_SHORT_ANSWER,
                accepted_answers='VAT\nValue Added Tax\nvalue-added tax',
                order=4,
            )
            qmap['short'] = (q_sa, None, None)
        # Paragraph (optional)
        if include_paragraph:
            q_para = Question.objects.create(required=False, topic=self.topic, text='Explain...', question_type=Question.TYPE_PARAGRAPH, order=5)
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
        self.assertContains(resp, 'value="approve"')
        self.assertContains(resp, 'value="reject"')
        self.assertEqual(resp.content.decode().count('value="approve"'), 1)
        self.assertEqual(resp.content.decode().count('value="reject"'), 1)

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
        q_para = Question.objects.create(required=False, topic=self.topic, text='Explain X', question_type=Question.TYPE_PARAGRAPH, order=1)
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
        used_q = qmap['single'][0]
        unused_q = qmap['tf'][0]
        
        submission = {used_q.id: qmap['single'][1][0]}
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        
        self.client.login(username='admin', password='adminpass')
        
        # 1. Used question should be soft-deleted (retired)
        delete_url = reverse('portal_question_delete', args=[used_q.id])
        resp = self.client.post(delete_url, follow=True)
        self.assertContains(resp, 'Question retired to preserve historical attempts.')
        
        used_q.refresh_from_db()
        self.assertFalse(used_q.is_active)
        self.assertTrue(attempt.responses.filter(question=used_q).exists())
        
        # 2. Unused question should be hard-deleted
        delete_unused_url = reverse('portal_question_delete', args=[unused_q.id])
        resp2 = self.client.post(delete_unused_url, follow=True)
        self.assertContains(resp2, 'Question permanently deleted.')
        self.assertFalse(Question.objects.filter(id=unused_q.id).exists())

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

    def test_manual_review_first_completion_without_doj_warns_and_blocks_completion(self):
        """
        When a learner without DOJ passes an attempt via manual review,
        admin receives a warning and topic completion remains blocked.
        """
        self.assertIsNone(self.learner.date_of_joining)
        self.topic.passing_score_override = 60
        self.topic.save(update_fields=['passing_score_override'])

        qmap = self._create_questions(include_paragraph=True)
        submission = {
            qmap['single'][0].id: qmap['single'][1][0],
            qmap['tf'][0].id: qmap['tf'][2][0],
            qmap['paragraph'][0].id: 'Learner explanation',
        }
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        self.assertFalse(attempt.passed)

        self.client.login(username='admin', password='adminpass')
        review_url = reverse('admin_attempt_review', args=[attempt.id, attempt.responses.get(is_correct__isnull=True).id])
        resp = self.client.post(review_url, {'action': 'approve'}, follow=True)
        self.assertEqual(resp.status_code, 200)

        attempt.refresh_from_db()
        self.assertTrue(attempt.passed)
        self.assertEqual(attempt.score, 67)

        # Warning is shown
        self.assertContains(
            resp,
            "Response approved as correct. Topic completion remains blocked until learner Date of Joining (DOJ) is set."
        )

        tp = TopicProgress.objects.get(user=self.learner, topic=self.topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)
        self.assertIsNone(tp.frozen_deadline)

    def test_manual_review_retake_of_completed_topic_without_doj_shows_normal_success_no_warning(self):
        """
        When a topic was already completed and the learner (without DOJ) passes a retake
        via manual review, admin sees normal success message and NO warning.
        """
        self.assertIsNone(self.learner.date_of_joining)
        self.topic.passing_score_override = 60
        self.topic.save(update_fields=['passing_score_override'])

        completed_time = timezone.now() - timezone.timedelta(days=2)
        frozen_deadline = timezone.now().date()
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=completed_time,
            frozen_deadline=frozen_deadline,
            attempts_count=1,
            best_score=100,
            latest_score=100,
        )

        qmap = self._create_questions(include_paragraph=True)
        submission = {
            qmap['single'][0].id: qmap['single'][1][0],
            qmap['tf'][0].id: qmap['tf'][2][0],
            qmap['paragraph'][0].id: 'Retake explanation',
        }
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        self.assertFalse(attempt.passed)

        self.client.login(username='admin', password='adminpass')
        review_url = reverse('admin_attempt_review', args=[attempt.id, attempt.responses.get(is_correct__isnull=True).id])
        resp = self.client.post(review_url, {'action': 'approve'}, follow=True)
        self.assertEqual(resp.status_code, 200)

        attempt.refresh_from_db()
        self.assertTrue(attempt.passed)

        # Normal success message displayed, no warning
        self.assertContains(resp, "Response approved as correct.")
        self.assertNotContains(resp, "Topic completion remains blocked")

        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.completed_at, completed_time)
        self.assertEqual(tp.frozen_deadline, frozen_deadline)

    def test_manual_review_passing_attempt_with_doj_shows_normal_success_no_warning(self):
        """
        When learner has a valid DOJ, manual review approval of a passing attempt
        finalizes completion and displays normal success message with NO warning.
        """
        self.learner.date_of_joining = timezone.now().date()
        self.learner.save(update_fields=['date_of_joining'])
        self.topic.passing_score_override = 60
        self.topic.save(update_fields=['passing_score_override'])

        qmap = self._create_questions(include_paragraph=True)
        submission = {
            qmap['single'][0].id: qmap['single'][1][0],
            qmap['tf'][0].id: qmap['tf'][2][0],
            qmap['paragraph'][0].id: 'Passing explanation',
        }
        attempt, _ = submit_quiz_attempt(self.learner, self.topic, submission)
        self.assertFalse(attempt.passed)

        self.client.login(username='admin', password='adminpass')
        review_url = reverse('admin_attempt_review', args=[attempt.id, attempt.responses.get(is_correct__isnull=True).id])
        resp = self.client.post(review_url, {'action': 'approve'}, follow=True)
        self.assertEqual(resp.status_code, 200)

        attempt.refresh_from_db()
        self.assertTrue(attempt.passed)

        # Normal success message displayed, no warning
        self.assertContains(resp, "Response approved as correct.")
        self.assertNotContains(resp, "Topic completion remains blocked")

        tp = TopicProgress.objects.get(user=self.learner, topic=self.topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)

    def test_manual_review_failed_attempt_without_doj_shows_no_warning(self):
        """
        When manual review leaves an attempt failed (rejected or approved with score below passing),
        no DOJ completion-blocked warning is displayed.
        """
        self.assertIsNone(self.learner.date_of_joining)
        self.topic.passing_score_override = 75
        self.topic.save(update_fields=['passing_score_override'])
        qmap = self._create_questions(include_paragraph=True)

        # Case 1: Rejected response
        submission_fail = {
            qmap['single'][0].id: qmap['single'][1][0],
            qmap['tf'][0].id: qmap['tf'][2][0],  # wrong
            qmap['paragraph'][0].id: 'Rejected explanation',
        }
        attempt1, _ = submit_quiz_attempt(self.learner, self.topic, submission_fail)

        self.client.login(username='admin', password='adminpass')
        review_url1 = reverse('admin_attempt_review', args=[attempt1.id, attempt1.responses.get(is_correct__isnull=True).id])
        resp1 = self.client.post(review_url1, {'action': 'reject'}, follow=True)
        self.assertEqual(resp1.status_code, 200)

        attempt1.refresh_from_db()
        self.assertFalse(attempt1.passed)
        self.assertContains(resp1, "Response marked as incorrect.")
        self.assertNotContains(resp1, "Topic completion remains blocked")

        # Case 2: Approved response, but attempt still failed due to other wrong answers (score 67% < 75%)
        submission_low_score = {
            qmap['single'][0].id: qmap['single'][1][0],  # correct
            qmap['tf'][0].id: qmap['tf'][2][0],          # wrong
            qmap['paragraph'][0].id: 'Good paragraph answer',
        }
        attempt2, _ = submit_quiz_attempt(self.learner, self.topic, submission_low_score)

        review_url2 = reverse('admin_attempt_review', args=[attempt2.id, attempt2.responses.get(is_correct__isnull=True).id])
        resp2 = self.client.post(review_url2, {'action': 'approve'}, follow=True)
        self.assertEqual(resp2.status_code, 200)

        attempt2.refresh_from_db()
        self.assertFalse(attempt2.passed)
        self.assertEqual(attempt2.score, 67)
        self.assertContains(resp2, "Response approved as correct.")
        self.assertNotContains(resp2, "Topic completion remains blocked")
