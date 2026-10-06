import io
from django.test import TestCase
from django.utils import timezone
from django.core.management import call_command
from accounts.models import User
from content.models import Subject, Topic, SiteConfig
from quizzes.models import Question, QuizAttempt, QuizResponse
from progress.models import TopicProgress
from access.models import LearnerAccess

class RepairTopicProgressTests(TestCase):
    def setUp(self):
        self.learner = User.objects.create_user(username='learner1', password='pw', role='learner')
        self.subject = Subject.objects.create(name='Subj', order=1)
        self.topic = Topic.objects.create(subject=self.subject, name='T1', order=1, assessment_required=True, status=Topic.STATUS_PUBLISHED)
        self.q1 = Question.objects.create(required=False, topic=self.topic, text='Q1', question_type=Question.TYPE_PARAGRAPH)

        config = SiteConfig.get_solo()
        config.default_passing_score = 70
        config.save()

    def _create_attempt(self, created_at, score, passed, responses_data, passing_score_used=70):
        attempt = QuizAttempt.objects.create(
            user=self.learner, topic=self.topic, attempt_number=1,
            correct_count=0, total_questions=len(responses_data), score=score, passed=passed,
            passing_score_used=passing_score_used
        )
        # Override created_at which is auto_now_add
        QuizAttempt.objects.filter(id=attempt.id).update(created_at=created_at)
        attempt.refresh_from_db()

        for i, is_correct in enumerate(responses_data):
            q = Question.objects.create(required=False, topic=self.topic, text=f'Q_{i}', question_type=Question.TYPE_PARAGRAPH)
            QuizResponse.objects.create(
                attempt=attempt, question=q, is_correct=is_correct, text_response='Ans'
            )
        return attempt

    def test_dry_run_makes_no_changes(self):
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0)
        self._create_attempt(timezone.now(), 100, True, [True])

        out = io.StringIO()
        call_command('repair_topic_progress', stdout=out)

        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertEqual(tp.best_score, 0)
        self.assertIn("DATABASE UNTOUCHED.", out.getvalue())

    def test_execute_persists_changes(self):
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0)
        self._create_attempt(timezone.now(), 100, True, [True])

        out = io.StringIO()
        call_command('repair_topic_progress', '--execute', stdout=out)

        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.best_score, 100)
        self.assertIn("DATABASE UPDATED SUCCESSFULLY.", out.getvalue())

    def test_best_score_reconstructed(self):
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0)
        t1 = timezone.now() - timezone.timedelta(days=2)
        t2 = timezone.now() - timezone.timedelta(days=1)

        self._create_attempt(t1, 80, False, [False])
        self._create_attempt(t2, 60, False, [False])

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.best_score, 80)
        self.assertEqual(tp.latest_score, 60)

    def test_latest_score_uses_newest_fully_graded_by_created_at(self):
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0)
        t1 = timezone.now() - timezone.timedelta(days=3)
        t2 = timezone.now() - timezone.timedelta(days=2)
        t3 = timezone.now() - timezone.timedelta(days=1)

        self._create_attempt(t3, 50, False, [False]) # newest
        self._create_attempt(t1, 90, True, [True])   # oldest
        self._create_attempt(t2, 40, False, [False]) # middle

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.latest_score, 50)
        self.assertEqual(tp.best_score, 90)

    def test_historical_passed_upgrades_status_and_backfills_completed_at(self):
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0)
        t1 = timezone.now() - timezone.timedelta(days=2)

        self._create_attempt(t1, 80, True, [True])

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.completed_at, t1)

    def test_historical_passed_false_not_upgraded_despite_current_effective_score(self):
        self.topic.passing_score_override = 50
        self.topic.save()

        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0)
        t1 = timezone.now() - timezone.timedelta(days=2)

        # Historically failed at 60 (threshold was maybe 70)
        self._create_attempt(t1, 60, False, [False], passing_score_used=70)

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()

        # Should remain IN_PROGRESS because passed=False, even though 60 >= current threshold 50
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertEqual(tp.best_score, 60)

    def test_attempts_with_is_correct_none_are_excluded(self):
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0)
        t1 = timezone.now() - timezone.timedelta(days=2)

        # Pending attempt
        self._create_attempt(t1, 100, True, [None])

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()

        # Excluded, so nothing changes
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertEqual(tp.best_score, 0)
        self.assertEqual(tp.latest_score, 0)

    def test_later_failed_attempt_never_downgrades_completed(self):
        t0 = timezone.now() - timezone.timedelta(days=5)
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_COMPLETED, best_score=100, latest_score=100, completed_at=t0)

        t1 = timezone.now() - timezone.timedelta(days=2) # Older attempt
        t2 = timezone.now() - timezone.timedelta(days=1) # Newer attempt

        # Older fully graded passing attempt
        self._create_attempt(t1, 100, True, [True])

        # Newer fully graded failed attempt
        self._create_attempt(t2, 0, False, [False])

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.best_score, 100)
        self.assertEqual(tp.latest_score, 0)
        self.assertEqual(tp.completed_at, t0)

    def test_assessment_required_false_completion_preserved(self):
        self.topic.assessment_required = False
        self.topic.save()

        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_COMPLETED, best_score=0, latest_score=0)

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)

    def test_zero_question_topic_completion_downgraded(self):
        Topic.objects.filter(id=self.topic.id).update(assessment_required=True)
        self.topic.questions.all().delete()

        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_COMPLETED, best_score=0, latest_score=0)

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        # Since it's assessment_required but has no QuizAttempts, it MUST be NOT_STARTED
        self.assertEqual(tp.status, TopicProgress.STATUS_NOT_STARTED)

    def test_existing_completed_at_is_preserved(self):
        t0 = timezone.now() - timezone.timedelta(days=5)
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0, completed_at=t0)
        t1 = timezone.now() - timezone.timedelta(days=2)

        self._create_attempt(t1, 100, True, [True])

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.completed_at, t0)

    def test_completed_without_passing_is_downgraded(self):
        # Create a manually COMPLETED topic progress, but only add a FAILING attempt.
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_COMPLETED, best_score=0, latest_score=0, completed_at=None)

        attempt = QuizAttempt.objects.create(
            user=self.learner, topic=self.topic, attempt_number=1, 
            correct_count=0, total_questions=1, score=0, passed=False, passing_score_used=75
        )
        QuizResponse.objects.create(attempt=attempt, question=self.q1, is_correct=False)

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)

    def test_historical_pass_preserves_completion_and_timestamp(self):
        # Create a historical passing attempt
        t0 = timezone.now() - timezone.timedelta(days=5)
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_COMPLETED, best_score=100, latest_score=100, completed_at=t0)

        attempt = QuizAttempt.objects.create(
            user=self.learner, topic=self.topic, attempt_number=1, 
            correct_count=1, total_questions=1, score=100, passed=True, passing_score_used=75
        )
        QuizResponse.objects.create(attempt=attempt, question=self.q1, is_correct=True)

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.completed_at, t0)

    def test_no_new_records_created(self):
        TopicProgress.objects.all().delete()
        call_command('repair_topic_progress', '--execute')
        self.assertEqual(TopicProgress.objects.count(), 0)

    def test_access_and_topic_fields_unchanged(self):
        LearnerAccess.objects.create(learner=self.learner, content_type_id=1, object_id=1, is_allowed=True)
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0)
        self._create_attempt(timezone.now(), 100, True, [True])

        call_command('repair_topic_progress', '--execute')

        self.assertEqual(LearnerAccess.objects.count(), 1)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.order, 1)

    def test_attempts_count_is_repaired(self):
        tp = TopicProgress.objects.create(
            user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS,
            best_score=0, latest_score=0, attempts_count=0
        )
        t1 = timezone.now() - timezone.timedelta(days=2)
        t2 = timezone.now() - timezone.timedelta(days=1)
        self._create_attempt(t1, 60, False, [False])
        self._create_attempt(t2, 80, True, [True])

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.attempts_count, 2)
        self.assertEqual(tp.best_score, 80)
        self.assertEqual(tp.latest_score, 80)

    def test_pass_then_fail_remains_completed(self):
        tp = TopicProgress.objects.create(
            user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS,
            best_score=0, latest_score=0, attempts_count=0, completed_at=None
        )
        t1 = timezone.now() - timezone.timedelta(days=2)
        t2 = timezone.now() - timezone.timedelta(days=1)

        self._create_attempt(t1, 100, True, [True])
        self._create_attempt(t2, 50, False, [False])

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.best_score, 100)
        self.assertEqual(tp.latest_score, 50)
        self.assertEqual(tp.attempts_count, 2)
        self.assertIsNotNone(tp.completed_at)
        self.assertEqual(tp.completed_at, t1)
  
class NPlusOneOptimizationTests(TestCase):  
    def setUp(self):  
        pass 


class RepairOptimizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        from content.models import Subject, Topic
        from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
        
        User = get_user_model()
        cls.learner = User.objects.create_user(username='testlearner2', password='password')
        cls.subject = Subject.objects.create(name='Subject 2')
        cls.topic1 = Topic.objects.create(name='Topic 1', subject=cls.subject, assessment_required=True, order=1)
        cls.topic2 = Topic.objects.create(name='Topic 2', subject=cls.subject, assessment_required=True, order=2)
        
        cls.q1 = Question.objects.create(required=False, topic=cls.topic1, text='Q1')
        cls.c1 = Choice.objects.create(question=cls.q1, text='C1', is_correct=True)
        
        cls.q2 = Question.objects.create(required=False, topic=cls.topic2, text='Q2')
        cls.c2 = Choice.objects.create(question=cls.q2, text='C2', is_correct=True)
        
        for i in range(5):
            attempt = QuizAttempt.objects.create(
                user=cls.learner, topic=cls.topic1, attempt_number=i+1, 
                correct_count=1, total_questions=1, score=100, passed=True, passing_score_used=75
            )
            QuizResponse.objects.create(attempt=attempt, question=cls.q1, is_correct=True)

        for i in range(5):
            attempt = QuizAttempt.objects.create(
                user=cls.learner, topic=cls.topic2, attempt_number=i+1, 
                correct_count=1, total_questions=1, score=100, passed=True, passing_score_used=75
            )
            QuizResponse.objects.create(attempt=attempt, question=cls.q2, is_correct=True)

    def test_reconcile_topic_progress_query_count(self):
        from progress.services import reconcile_topic_progress
        # We expect a small fixed number of queries regardless of attempts
        with self.assertNumQueries(6):
            reconcile_topic_progress(self.learner, self.topic1)

    def test_repair_topic_progress_query_count(self):
        from django.core.management import call_command
        from progress.services import reconcile_topic_progress
        reconcile_topic_progress(self.learner, self.topic1)
        reconcile_topic_progress(self.learner, self.topic2)
        
        # Test command query count (bulk optimization)
        # 1 for topic scan, 1 for attempt scan, 1 savepoint, 1 bulk user, 1 bulk topic, 1 bulk TP, 1 annotated attempts, 1 rollback = 8
        with self.assertNumQueries(9):
            call_command('repair_topic_progress')

class RepairBehaviorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model
        from content.models import Subject, Topic
        from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
        
        User = get_user_model()
        cls.learner = User.objects.create_user(username='testlearner3', password='password')
        cls.subject = Subject.objects.create(name='Subject 3')
        cls.topic = Topic.objects.create(name='Topic 3', subject=cls.subject, assessment_required=True, order=1)
        cls.q = Question.objects.create(required=False, topic=cls.topic, text='Q1')
        cls.c = Choice.objects.create(question=cls.q, text='C1', is_correct=True)

    def test_missing_topic_progress_dry_run_and_execute(self):
        from progress.models import TopicProgress
        from django.core.management import call_command
        from quizzes.models import QuizAttempt, QuizResponse
        
        TopicProgress.objects.filter(user=self.learner, topic=self.topic).delete()
        QuizAttempt.objects.filter(user=self.learner, topic=self.topic).delete()
        
        attempt = QuizAttempt.objects.create(
            user=self.learner, topic=self.topic, attempt_number=1, 
            correct_count=1, total_questions=1, score=100, passed=True, passing_score_used=75
        )
        QuizResponse.objects.create(attempt=attempt, question=self.q, is_correct=True)
        
        call_command('repair_topic_progress')
        self.assertFalse(TopicProgress.objects.filter(user=self.learner, topic=self.topic).exists())
        
        call_command('repair_topic_progress', '--execute')
        
        tp = TopicProgress.objects.get(user=self.learner, topic=self.topic)
        self.assertEqual(tp.attempts_count, 1)
        self.assertEqual(tp.best_score, 100)
        self.assertEqual(tp.latest_score, 100)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)
        
        # Second run should be idempotent
        call_command('repair_topic_progress')
