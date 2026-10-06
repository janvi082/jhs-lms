from django.test import TestCase
from django.contrib.auth import get_user_model
from content.models import Subject, Topic
from quizzes.models import Question, QuizAttempt, QuizResponse
from progress.models import TopicProgress
from progress.services import get_subject_progress_summary, get_topic_display_status, annotate_progress_with_review_status

User = get_user_model()

class PendingReviewDashboardTests(TestCase):
    def setUp(self):
        self.learner = User.objects.create_user(username='learner', password='pw')
        self.subject = Subject.objects.create(name='Test Subject', is_active=True)
        self.topic1 = Topic.objects.create(subject=self.subject, name='Topic 1', is_active=True, order=1)
        self.topic2 = Topic.objects.create(subject=self.subject, name='Topic 2', is_active=True, order=2)
        
        self.q1 = Question.objects.create(required=False, topic=self.topic1, text='Q1', question_type=Question.TYPE_PARAGRAPH)
        self.q2 = Question.objects.create(required=False, topic=self.topic2, text='Q2', question_type=Question.TYPE_PARAGRAPH)

    def _create_attempt(self, topic, score=0, passed=False):
        return QuizAttempt.objects.create(
            user=self.learner, topic=topic, attempt_number=1,
            correct_count=0, total_questions=1,
            score=score, passed=passed, passing_score_used=70
        )

    def test_topic_with_only_pending_attempt(self):
        a1 = self._create_attempt(self.topic1)
        QuizResponse.objects.create(attempt=a1, question=self.q1, is_correct=None)
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, attempts_count=1, best_score=0, status=TopicProgress.STATUS_IN_PROGRESS)
        
        # Dashboard summary
        summary = get_subject_progress_summary(self.learner, self.subject)
        # 0 finalized scores out of 2 topics. 
        self.assertIsNone(summary['understanding_percent'])
        
        # Topic display status
        annotated_tps = annotate_progress_with_review_status(TopicProgress.objects.filter(id=tp.id))
        prog = annotated_tps.first()
        
        self.assertTrue(prog.has_pending_review)
        self.assertFalse(prog.has_finalized_score)
        
        status = get_topic_display_status(prog)
        self.assertEqual(status['code'], 'pending_review')

    def test_topic_with_earlier_finalized_score_and_newer_pending(self):
        # Earlier attempt: 50%
        a1 = self._create_attempt(self.topic1, score=50)
        QuizResponse.objects.create(attempt=a1, question=self.q1, is_correct=False)
        
        # Newer attempt: pending
        a2 = self._create_attempt(self.topic1, score=0)
        a2.attempt_number = 2
        a2.save()
        QuizResponse.objects.create(attempt=a2, question=self.q1, is_correct=None)
        
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, attempts_count=2, best_score=50, status=TopicProgress.STATUS_IN_PROGRESS)
        
        # Dashboard summary
        summary = get_subject_progress_summary(self.learner, self.subject)
        self.assertEqual(summary['understanding_percent'], 50)
        
        annotated_tps = annotate_progress_with_review_status(TopicProgress.objects.filter(id=tp.id))
        prog = annotated_tps.first()
        self.assertTrue(prog.has_pending_review)
        self.assertTrue(prog.has_finalized_score)
        
        status = get_topic_display_status(prog)
        self.assertEqual(status['code'], 'pending_review')

    def test_genuine_finalized_zero_is_included(self):
        a1 = self._create_attempt(self.topic1, score=0)
        QuizResponse.objects.create(attempt=a1, question=self.q1, is_correct=False)
        
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, attempts_count=1, best_score=0, status=TopicProgress.STATUS_IN_PROGRESS)
        
        summary = get_subject_progress_summary(self.learner, self.subject)
        self.assertEqual(summary['understanding_percent'], 0)
        
        annotated_tps = annotate_progress_with_review_status(TopicProgress.objects.filter(id=tp.id))
        prog = annotated_tps.first()
        self.assertFalse(prog.has_pending_review)
        self.assertTrue(prog.has_finalized_score)
        
        status = get_topic_display_status(prog)
        self.assertEqual(status['code'], 'needs_improvement')

    def test_empty_attempt_is_finalized_zero(self):
        a1 = self._create_attempt(self.topic1, score=0)
        # No QuizResponse rows!
        
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, attempts_count=1, best_score=0, status=TopicProgress.STATUS_IN_PROGRESS)
        
        summary = get_subject_progress_summary(self.learner, self.subject)
        self.assertEqual(summary['understanding_percent'], 0)
        
        annotated_tps = annotate_progress_with_review_status(TopicProgress.objects.filter(id=tp.id))
        prog = annotated_tps.first()
        self.assertFalse(prog.has_pending_review)
        self.assertTrue(prog.has_finalized_score)
        
        status = get_topic_display_status(prog)
        self.assertEqual(status['code'], 'needs_improvement')
