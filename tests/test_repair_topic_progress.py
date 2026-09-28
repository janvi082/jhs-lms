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
        self.q1 = Question.objects.create(topic=self.topic, text='Q1', question_type=Question.TYPE_PARAGRAPH)

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
            q = Question.objects.create(topic=self.topic, text=f'Q_{i}', question_type=Question.TYPE_PARAGRAPH)
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

    def test_zero_question_topic_completion_preserved(self):
        Topic.objects.filter(id=self.topic.id).update(assessment_required=True)
        self.topic.questions.all().delete()

        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_COMPLETED, best_score=0, latest_score=0)

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)

    def test_existing_completed_at_is_preserved(self):
        t0 = timezone.now() - timezone.timedelta(days=5)
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_IN_PROGRESS, best_score=0, latest_score=0, completed_at=t0)
        t1 = timezone.now() - timezone.timedelta(days=2)

        self._create_attempt(t1, 100, True, [True])

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.completed_at, t0)

    def test_completed_without_passing_never_downgraded_or_invented_timestamp(self):
        tp = TopicProgress.objects.create(user=self.learner, topic=self.topic, status=TopicProgress.STATUS_COMPLETED, best_score=0, latest_score=0, completed_at=None)

        call_command('repair_topic_progress', '--execute')
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNone(tp.completed_at)

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
