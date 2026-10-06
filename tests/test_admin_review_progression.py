from django.test import TestCase
from django.utils import timezone
from accounts.models import User
from content.models import Subject, Topic, SiteConfig
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from progress.models import TopicProgress
from progress.services import recalculate_attempt_score

class AdminReviewProgressionTests(TestCase):
    def setUp(self):
        self.learner = User.objects.create_user(username='learner1', password='pw', role='learner')
        self.subject = Subject.objects.create(name='Subj', order=1)
        self.topic1 = Topic.objects.create(subject=self.subject, name='T1', order=1, assessment_required=True, status=Topic.STATUS_PUBLISHED)
        self.topic2 = Topic.objects.create(subject=self.subject, name='T2', order=2, assessment_required=True, status=Topic.STATUS_PUBLISHED)
        
        self.q1 = Question.objects.create(required=False, topic=self.topic1, text='Q1', question_type=Question.TYPE_PARAGRAPH)
        
    def _create_attempt(self, score, passed, responses_data, passing_score_used=70):
        attempt = QuizAttempt.objects.create(
            user=self.learner, topic=self.topic1, attempt_number=1,
            correct_count=0, total_questions=len(responses_data), score=score, passed=passed,
            passing_score_used=passing_score_used
        )
        for i, is_correct in enumerate(responses_data):
            q = Question.objects.create(required=False, topic=self.topic1, text=f'Q_{i}', question_type=Question.TYPE_PARAGRAPH)
            QuizResponse.objects.create(
                attempt=attempt, question=q, is_correct=is_correct, text_response='Ans'
            )
        return attempt
        
    def test_admin_review_changes_failing_to_passing(self):
        # Initial failing attempt
        attempt = self._create_attempt(0, False, [None])
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0, attempts_count=1)
        
        # Admin reviews and marks correct
        resp = attempt.responses.first()
        resp.is_correct = True
        resp.save()
        
        recalculate_attempt_score(attempt)
        
        attempt.refresh_from_db()
        tp.refresh_from_db()
        
        self.assertTrue(attempt.passed)
        self.assertEqual(tp.best_score, 100)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)
        
    def test_admin_review_remains_failing(self):
        # Initial failing attempt
        attempt = self._create_attempt(0, False, [None])
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0, attempts_count=1)
        
        # Admin reviews and marks incorrect
        resp = attempt.responses.first()
        resp.is_correct = False
        resp.save()
        
        recalculate_attempt_score(attempt)
        
        attempt.refresh_from_db()
        tp.refresh_from_db()
        
        self.assertFalse(attempt.passed)
        self.assertEqual(tp.best_score, 0)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)

    def test_admin_review_exact_threshold(self):
        # Passing score is 70. 10 questions. Need exactly 7 correct.
        config = SiteConfig.get_solo()
        config.default_passing_score = 70
        config.save()
        
        # Attempt with 10 questions. 6 correct, 4 False initially.
        initial_responses = [True]*6 + [False]*4
        attempt = self._create_attempt(60, False, initial_responses)
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_IN_PROGRESS, best_score=60, latest_score=60, attempts_count=1)
        
        # Admin marks 1 more correct -> exactly 7 correct (70%)
        resp = attempt.responses.filter(is_correct=False).first()
        resp.is_correct = True
        resp.save()
        
        recalculate_attempt_score(attempt)
        
        attempt.refresh_from_db()
        tp.refresh_from_db()
        
        self.assertTrue(attempt.passed)
        self.assertEqual(attempt.score, 70)
        self.assertEqual(tp.best_score, 70)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)

    def test_already_completed_receives_lower_failed_attempt(self):
        # Topic is already completed with 100
        completed_time = timezone.now() - timezone.timedelta(days=1)
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED, best_score=100, latest_score=100, attempts_count=1, completed_at=completed_time)
        
        # Add a passing attempt to back the best_score
        QuizAttempt.objects.create(
            user=self.learner, topic=self.topic1, attempt_number=1,
            correct_count=1, total_questions=1, score=100, passed=True,
            passing_score_used=70
        ).responses.create(question=self.q1, is_correct=True)
        
        # New failed attempt
        attempt2 = self._create_attempt(0, False, [None])
        attempt2.attempt_number = 2
        attempt2.save()
        
        # Admin reviews attempt2 and marks incorrect
        resp = attempt2.responses.first()
        resp.is_correct = False
        resp.save()
        
        recalculate_attempt_score(attempt2)
        
        tp.refresh_from_db()
        self.assertEqual(tp.best_score, 100)
        self.assertEqual(tp.latest_score, 0)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.completed_at, completed_time)

    def test_partial_review_safety(self):
        config = SiteConfig.get_solo()
        config.default_passing_score = 70
        config.save()
        
        # 10 questions. 6 correct, 1 wrong, 3 pending.
        initial_responses = [True]*6 + [False]*1 + [None]*3
        attempt = self._create_attempt(60, False, initial_responses)
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_IN_PROGRESS, best_score=60, latest_score=60, attempts_count=1)
        
        # Admin reviews one pending as correct
        pending_resp1 = attempt.responses.filter(is_correct__isnull=True).first()
        pending_resp1.is_correct = True
        pending_resp1.save()
        
        recalculate_attempt_score(attempt)
        
        # Now 7 correct, 1 wrong, 2 pending. Gradable=8. Score = 7/8 = 88%.
        # Passes threshold (70). BUT there are pending reviews.
        # So status should remain IN_PROGRESS, and provisional best_score should be 0.
        tp.refresh_from_db()
        self.assertEqual(tp.best_score, 0)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        
        # Admin reviews another pending as incorrect
        pending_resp2 = attempt.responses.filter(is_correct__isnull=True).first()
        pending_resp2.is_correct = False
        pending_resp2.save()
        
        recalculate_attempt_score(attempt)
        
        # Now 7 correct, 2 wrong, 1 pending. Gradable=9. Score = 7/9 = 78%.
        tp.refresh_from_db()
        self.assertEqual(tp.best_score, 0)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        
        # Admin reviews final pending as incorrect
        pending_resp3 = attempt.responses.filter(is_correct__isnull=True).first()
        pending_resp3.is_correct = False
        pending_resp3.save()
        
        recalculate_attempt_score(attempt)
        
        # Now 7 correct, 3 wrong, 0 pending. Gradable=10. Score = 7/10 = 70%.
        # No pending left. Passes threshold (70 == 70).
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)

    def test_partial_review_fails_ultimately(self):
        config = SiteConfig.get_solo()
        config.default_passing_score = 80
        config.save()
        
        # 10 questions. 6 correct, 1 wrong, 3 pending.
        initial_responses = [True]*6 + [False]*1 + [None]*3
        attempt = self._create_attempt(60, False, initial_responses, passing_score_used=80)
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_IN_PROGRESS, best_score=60, latest_score=60, attempts_count=1)
        
        # Admin reviews one pending as correct
        pending_resp1 = attempt.responses.filter(is_correct__isnull=True).first()
        pending_resp1.is_correct = True
        pending_resp1.save()
        
        recalculate_attempt_score(attempt)
        
        # 7 correct, 1 wrong, 2 pending. Gradable=8. Score = 7/8 = 88%.
        # Passes threshold (80). BUT pending reviews exist.
        tp.refresh_from_db()
        self.assertEqual(tp.best_score, 0)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        
        # Admin reviews both remaining as incorrect
        for resp in attempt.responses.filter(is_correct__isnull=True):
            resp.is_correct = False
            resp.save()
            
        recalculate_attempt_score(attempt)
        
        # 7 correct, 3 wrong, 0 pending. Gradable=10. Score = 7/10 = 70%.
        # Final review falls below 80.
        tp.refresh_from_db()
        self.assertEqual(tp.best_score, 70)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)

    def test_passing_score_hierarchy_respected(self):
        # 1. Topic/subject/default hierarchy applies when a NEW attempt is created
        config = SiteConfig.get_solo()
        config.default_passing_score = 70
        config.save()
        
        self.topic1.passing_score_override = 75
        self.topic1.save()
        
        # New attempt should use the topic override (75)
        # We manually simulate _create_attempt but enforce passing_score_used=75
        attempt = QuizAttempt.objects.create(
            user=self.learner, topic=self.topic1, attempt_number=1,
            correct_count=0, total_questions=10, score=70, passed=False,
            passing_score_used=75
        )
        for i, is_correct in enumerate([True]*7 + [False]*3):
            q = Question.objects.create(required=False, topic=self.topic1, text=f'Q_{i}', question_type=Question.TYPE_PARAGRAPH)
            QuizResponse.objects.create(
                attempt=attempt, question=q, is_correct=is_correct, text_response='Ans'
            )
            
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_IN_PROGRESS, best_score=70, latest_score=70, attempts_count=1)
        
        # 2. Topic threshold changes to 80%
        self.topic1.passing_score_override = 80
        self.topic1.save()
        
        # 3. Admin reviews and marks 1 more correct -> 8 correct (80%)
        resp = attempt.responses.filter(is_correct=False).first()
        resp.is_correct = True
        resp.save()
        
        # 4. Manual review recalculates the score and pass/fail result using the saved 75% threshold
        # It should NOT use the new 80% threshold. Thus, 80% score >= 75% threshold -> passes.
        recalculate_attempt_score(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.passing_score_used, 75)
        self.assertEqual(attempt.score, 80)
        self.assertTrue(attempt.passed)
        
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)
        
        # 5. Changing or removing a topic override after submission does not change the saved attempt's result
        self.topic1.passing_score_override = None
        self.topic1.save()
        
        # Recalculate again, should still use 75 and remain passed
        recalculate_attempt_score(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.passing_score_used, 75)
        self.assertTrue(attempt.passed)

    def test_normal_progression_after_admin_review(self):
        # T1 fails initially. T2 is locked.
        attempt = self._create_attempt(0, False, [None])
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0, attempts_count=1)
        
        from progress.services import is_topic_unlocked
        self.assertTrue(is_topic_unlocked(self.learner, self.topic1))
        self.assertFalse(is_topic_unlocked(self.learner, self.topic2))
        
        # Admin reviews T1 -> pass
        resp = attempt.responses.first()
        resp.is_correct = True
        resp.save()
        recalculate_attempt_score(attempt)
        
        self.assertTrue(is_topic_unlocked(self.learner, self.topic2))

    def test_access_control_still_wins(self):
        from access.models import LearnerAccess
        from django.contrib.contenttypes.models import ContentType
        
        # Access denied to T1 explicitly
        topic_ct = ContentType.objects.get_for_model(Topic)
        LearnerAccess.objects.create(learner=self.learner, content_type=topic_ct, object_id=self.topic1.id, is_allowed=False)
        
        from access.services import has_topic_access
        self.assertFalse(has_topic_access(self.learner, self.topic1))
        
        # Even if they somehow had a failing attempt that an admin grades as passed
        attempt = self._create_attempt(0, False, [None])
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0, attempts_count=1)
        
        resp = attempt.responses.first()
        resp.is_correct = True
        resp.save()
        recalculate_attempt_score(attempt)
        
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        
        # Access is STILL denied regardless of progress completion
        self.assertFalse(has_topic_access(self.learner, self.topic1))
        
    def test_best_score_never_decreases(self):
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED, best_score=100, latest_score=100)
        QuizAttempt.objects.create(
            user=self.learner, topic=self.topic1, attempt_number=1,
            correct_count=1, total_questions=1, score=100, passed=True,
            passing_score_used=70
        ).responses.create(question=self.q1, is_correct=True)
        
        # New attempt with 0
        attempt2 = self._create_attempt(0, False, [None])
        attempt2.attempt_number = 2
        attempt2.save()
        resp = attempt2.responses.first()
        resp.is_correct = False
        resp.save()
        
        recalculate_attempt_score(attempt2)
        
        tp.refresh_from_db()
        self.assertEqual(tp.best_score, 100)
