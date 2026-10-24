import unittest
from datetime import date
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from django.db import IntegrityError
from accounts.models import User
from content.models import Subject, Topic, SiteConfig
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from progress.models import TopicProgress
from progress.services import submit_quiz_attempt


class QuizSubmissionTransactionSafetyTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.save()

        self.learner = User.objects.create_user(
            username='trans_learner', password='password123', role=User.ROLE_LEARNER
        )
        self.subject = Subject.objects.create(name='Trans Subject', slug='trans-sub', is_active=True)
        self.topic = Topic.objects.create(
            subject=self.subject, name='Trans Topic', is_active=True, assessment_required=True
        )

        # Create 2 questions: one single choice, one paragraph
        self.q1 = Question.objects.create(
            topic=self.topic,
            text='What is 2+2?',
            question_type=Question.TYPE_SINGLE_CHOICE,
            order=1,
            is_active=True,
        )
        self.c1_correct = Choice.objects.create(question=self.q1, text='4', is_correct=True)
        self.c1_wrong = Choice.objects.create(question=self.q1, text='5', is_correct=False)

        self.q2 = Question.objects.create(
            topic=self.topic,
            text='Explain gravity',
            question_type=Question.TYPE_PARAGRAPH,
            order=2,
            is_active=True,
        )

    def test_successful_quiz_submission_persists_all_data(self):
        """Standard submission cleanly writes attempt, responses, and reconciles progress."""
        answers = {
            self.q1.id: self.c1_correct.id,
            self.q2.id: 'Gravity is the curvature of spacetime.',
        }
        attempt, tp = submit_quiz_attempt(self.learner, self.topic, answers)

        self.assertIsNotNone(attempt.id)
        self.assertEqual(QuizAttempt.objects.filter(user=self.learner, topic=self.topic).count(), 1)
        self.assertEqual(QuizResponse.objects.filter(attempt=attempt).count(), 2)

        resp_q1 = QuizResponse.objects.get(attempt=attempt, question=self.q1)
        self.assertTrue(resp_q1.is_correct)
        self.assertIn(self.c1_correct, resp_q1.selected_choices.all())

        resp_q2 = QuizResponse.objects.get(attempt=attempt, question=self.q2)
        self.assertIsNone(resp_q2.is_correct)
        self.assertEqual(resp_q2.text_response, 'Gravity is the curvature of spacetime.')

        tp.refresh_from_db()
        self.assertEqual(tp.attempts_count, 1)

    def test_rollback_when_reconciliation_fails(self):
        """If progress reconciliation throws an exception, attempt and responses must roll back."""
        answers = {
            self.q1.id: self.c1_correct.id,
            self.q2.id: 'Answer text',
        }

        # TopicProgress exists before submission
        initial_tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic,
            status=TopicProgress.STATUS_NOT_STARTED,
            attempts_count=0,
        )

        with patch('progress.services.reconcile_topic_progress', side_effect=RuntimeError("Simulated reconciliation crash")):
            with self.assertRaises(RuntimeError):
                submit_quiz_attempt(self.learner, self.topic, answers)

        # Verify nothing was persisted from the failed attempt
        self.assertEqual(QuizAttempt.objects.filter(user=self.learner, topic=self.topic).count(), 0)
        self.assertEqual(QuizResponse.objects.count(), 0)

        # Verify initial TopicProgress is untouched
        initial_tp.refresh_from_db()
        self.assertEqual(initial_tp.status, TopicProgress.STATUS_NOT_STARTED)
        self.assertEqual(initial_tp.attempts_count, 0)

    def test_rollback_when_response_creation_fails(self):
        """If response persistence fails midway, the attempt record must roll back cleanly."""
        answers = {
            self.q1.id: self.c1_correct.id,
            self.q2.id: 'Answer text',
        }

        with patch.object(QuizResponse.objects, 'create', side_effect=IntegrityError("DB constraint error")):
            with self.assertRaises(IntegrityError):
                submit_quiz_attempt(self.learner, self.topic, answers)

        # Verify the QuizAttempt created before QuizResponse.create was rolled back
        self.assertEqual(QuizAttempt.objects.filter(user=self.learner, topic=self.topic).count(), 0)
        self.assertEqual(QuizResponse.objects.count(), 0)


class AdminAttemptReviewTransactionSafetyTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.save()

        self.admin = User.objects.create_user(
            username='review_admin', password='password123', role=User.ROLE_ADMIN, is_staff=True
        )
        self.learner = User.objects.create_user(
            username='review_learner', password='password123', role=User.ROLE_LEARNER,
            date_of_joining=date(2026, 1, 10)
        )
        self.subject = Subject.objects.create(name='Review Subj', slug='rev-sub', is_active=True)
        self.topic = Topic.objects.create(
            subject=self.subject, name='Review Topic', is_active=True, assessment_required=True
        )

        # 1 Single choice + 1 Paragraph
        self.q_sc = Question.objects.create(
            topic=self.topic, text='Q1', question_type=Question.TYPE_SINGLE_CHOICE, order=1, is_active=True
        )
        self.c_correct = Choice.objects.create(question=self.q_sc, text='Yes', is_correct=True)
        self.c_wrong = Choice.objects.create(question=self.q_sc, text='No', is_correct=False)

        self.q_para = Question.objects.create(
            topic=self.topic, text='Q2', question_type=Question.TYPE_PARAGRAPH, order=2, is_active=True
        )

        # Learner submits attempt: single choice is correct (100% of gradable so far), paragraph is pending
        submission = {
            self.q_sc.id: self.c_correct.id,
            self.q_para.id: 'Essay answer',
        }
        self.attempt, self.tp = submit_quiz_attempt(self.learner, self.topic, submission)
        self.pending_response = self.attempt.responses.get(question=self.q_para)
        self.assertIsNone(self.pending_response.is_correct)

        self.client = Client()
        self.client.login(username='review_admin', password='password123')

    def test_approve_updates_score_and_progress_atomically(self):
        """Approving a pending response updates response, attempt score, and progress."""
        url = reverse('admin_attempt_review', args=[self.attempt.id, self.pending_response.id])
        resp = self.client.post(url, {'action': 'approve'}, follow=True)
        self.assertEqual(resp.status_code, 200)

        self.pending_response.refresh_from_db()
        self.assertTrue(self.pending_response.is_correct)

        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.correct_count, 2)
        self.assertEqual(self.attempt.score, 100)
        self.assertTrue(self.attempt.passed)

        self.tp.refresh_from_db()
        self.assertEqual(self.tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(self.tp.best_score, 100)

    def test_reject_updates_score_and_progress_atomically(self):
        """Rejecting a pending response marks it incorrect and recalculates attempt."""
        url = reverse('admin_attempt_review', args=[self.attempt.id, self.pending_response.id])
        resp = self.client.post(url, {'action': 'reject'}, follow=True)
        self.assertEqual(resp.status_code, 200)

        self.pending_response.refresh_from_db()
        self.assertFalse(self.pending_response.is_correct)

        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.correct_count, 1)
        self.assertEqual(self.attempt.score, 50)
        self.assertFalse(self.attempt.passed)

    def test_rollback_when_recalculation_fails(self):
        """If recalculate_attempt_score fails during review, response change must roll back."""
        url = reverse('admin_attempt_review', args=[self.attempt.id, self.pending_response.id])

        with patch('portal.views.recalculate_attempt_score', side_effect=RuntimeError("Simulated recalculate failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(url, {'action': 'approve'})

        # Response must remain ungraded (None), NOT True
        self.pending_response.refresh_from_db()
        self.assertIsNone(self.pending_response.is_correct)

        # Attempt score must remain unchanged
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.correct_count, 1)

    def test_already_graded_response_cannot_be_regraded(self):
        """A response that has already been graded cannot be modified by a second review."""
        # First approve
        url = reverse('admin_attempt_review', args=[self.attempt.id, self.pending_response.id])
        self.client.post(url, {'action': 'approve'})
        self.pending_response.refresh_from_db()
        self.assertTrue(self.pending_response.is_correct)

        # Attempt to reject the already approved response
        resp = self.client.post(url, {'action': 'reject'}, follow=True)
        self.assertContains(resp, "already been graded")

        # Response remains True (was not overwritten to False)
        self.pending_response.refresh_from_db()
        self.assertTrue(self.pending_response.is_correct)

    def test_invalid_action_rejected_without_db_changes(self):
        """Invalid review action is rejected without changing response status."""
        url = reverse('admin_attempt_review', args=[self.attempt.id, self.pending_response.id])
        resp = self.client.post(url, {'action': 'unknown_action'}, follow=True)
        self.assertContains(resp, "Invalid review action.")

        self.pending_response.refresh_from_db()
        self.assertIsNone(self.pending_response.is_correct)

    def test_approve_with_missing_doj_blocks_topic_completion(self):
        """Manual approval when learner has missing DOJ records response and score, but blocks topic completion."""
        learner_no_doj = User.objects.create_user(
            username='no_doj_learner', password='password123', role=User.ROLE_LEARNER,
            date_of_joining=None
        )
        submission = {
            self.q_sc.id: self.c_correct.id,
            self.q_para.id: 'Essay answer',
        }
        attempt, tp = submit_quiz_attempt(learner_no_doj, self.topic, submission)
        pending_resp = attempt.responses.get(question=self.q_para)
        self.assertIsNone(pending_resp.is_correct)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)

        url = reverse('admin_attempt_review', args=[attempt.id, pending_resp.id])
        resp = self.client.post(url, {'action': 'approve'}, follow=True)
        self.assertEqual(resp.status_code, 200)

        # Response and attempt score are persisted normally
        pending_resp.refresh_from_db()
        self.assertTrue(pending_resp.is_correct)
        attempt.refresh_from_db()
        self.assertEqual(attempt.correct_count, 2)
        self.assertEqual(attempt.score, 100)
        self.assertTrue(attempt.passed)

        # TopicProgress completion remains blocked due to missing DOJ
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)
        self.assertIsNone(tp.frozen_deadline)

        # Informative admin warning is displayed
        self.assertContains(resp, "Topic completion remains blocked until learner Date of Joining (DOJ) is set")

