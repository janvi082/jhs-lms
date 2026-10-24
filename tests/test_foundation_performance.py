from datetime import date, datetime, timedelta, timezone as dt_timezone
from django.test import TestCase
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from content.models import Subject, LearningPhase, Topic, SiteConfig
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from progress.models import TopicProgress
from progress.services import (
    calculate_nominal_deadline,
    evaluate_timeline_status,
    calculate_topic_performance,
    calculate_phase_performance,
    calculate_reconciled_fields,
    reconcile_topic_progress,
    record_topic_view,
    complete_non_assessed_topic,
    get_effective_passing_score,
    is_topic_unlocked,
    PhasePerformanceStatus,
    submit_quiz_attempt,
    recalculate_attempt_score,
)

User = get_user_model()


class FoundationPerformanceMonitoringTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.default_required_question_count = 1
        self.site_config.save()

        self.doj = date(2026, 1, 10)
        self.learner = User.objects.create_user(
            username='foundation_learner',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.doj
        )

        self.subject = Subject.objects.create(name='Foundation Subject', slug='foundation-sub', order=1, is_active=True)
        self.phase = LearningPhase.objects.create(subject=self.subject, name='Phase 1', start_day=1, end_day=15)

        self.topic1 = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase,
            name='Topic 1 (Assessed)',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )

        # 1 active question for Topic 1
        self.q1 = Question.objects.create(topic=self.topic1, text='Q1', order=1, is_active=True)
        self.c1_correct = Choice.objects.create(question=self.q1, text='Correct', is_correct=True)
        self.c1_wrong = Choice.objects.create(question=self.q1, text='Wrong', is_correct=False)

    # --------------------------------------------------------------------------
    # GROUP 1: Timezone & Date Math (3 Tests)
    # --------------------------------------------------------------------------
    def test_settings_timezone_is_asia_kolkata(self):
        self.assertEqual(settings.TIME_ZONE, 'Asia/Kolkata')
        self.assertTrue(settings.USE_TZ)

    def test_calculate_nominal_deadline_ist_calendar_math(self):
        # Day 1 is DOJ (2026-01-10). End Day = 15.
        # Deadline = 2026-01-10 + (15 - 1) days = 2026-01-24.
        deadline = calculate_nominal_deadline(self.doj, 15)
        self.assertEqual(deadline, date(2026, 1, 24))

        self.assertIsNone(calculate_nominal_deadline(None, 15))
        self.assertIsNone(calculate_nominal_deadline(self.doj, 0))
        self.assertIsNone(calculate_nominal_deadline(self.doj, None))

    def test_localdate_evaluation_across_midnight_boundary(self):
        # 20:30 UTC on 2026-10-16 corresponds to 02:00 IST on 2026-10-17
        utc_dt = datetime(2026, 10, 16, 20, 30, tzinfo=dt_timezone.utc)
        ist_date = timezone.localdate(utc_dt)
        self.assertEqual(ist_date, date(2026, 10, 17))

    # --------------------------------------------------------------------------
    # GROUP 2: First-Completion & Deadline Freezing (4 Tests)
    # --------------------------------------------------------------------------
    def test_first_completion_assessed_topic_freezes_deadline(self):
        att = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att, question=self.q1, is_correct=True)

        tp = reconcile_topic_progress(self.learner, self.topic1)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.completed_at, att.created_at)
        expected_deadline = calculate_nominal_deadline(self.doj, 15)
        self.assertEqual(tp.frozen_deadline, expected_deadline)

    def test_first_completion_non_assessed_topic_freezes_deadline(self):
        topic_non_assessed = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase,
            name='Topic Non-Assessed',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )
        # Complete topic1 first to unlock topic_non_assessed
        TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED)

        tp = record_topic_view(self.learner, topic_non_assessed)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)
        expected_deadline = calculate_nominal_deadline(self.doj, 15)
        self.assertEqual(tp.frozen_deadline, expected_deadline)

    def test_first_completion_zero_question_topic_freezes_deadline(self):
        topic_zero_q = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase,
            name='Topic Zero Questions',
            order=3,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        # Unlock topic_zero_q
        TopicProgress.objects.create(user=self.learner, topic=self.topic1, status=TopicProgress.STATUS_COMPLETED)

        tp = record_topic_view(self.learner, topic_zero_q)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)
        expected_deadline = calculate_nominal_deadline(self.doj, 15)
        self.assertEqual(tp.frozen_deadline, expected_deadline)

    def test_first_completion_missing_doj_leaves_deadline_null(self):
        learner_no_doj = User.objects.create_user(
            username='no_doj_learner',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=None
        )
        sub2 = Subject.objects.create(name='Sub No DOJ', slug='sub-no-doj', order=2, is_active=True)
        topic_unassigned = Topic.objects.create(
            subject=sub2,
            learning_phase=None,
            name='Non-Assessed No Phase',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )
        tp = record_topic_view(learner_no_doj, topic_unassigned)
        self.assertIsNotNone(tp)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)
        self.assertIsNone(tp.frozen_deadline)

    # --------------------------------------------------------------------------
    # GROUP 3: Option A Stale-Metadata Resolution (3 Tests)
    # --------------------------------------------------------------------------
    def test_option_a_assessed_topic_replaces_stale_deadline(self):
        stale_deadline = date(2025, 5, 1)
        stale_timestamp = timezone.now() - timedelta(days=300)
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_IN_PROGRESS,
            completed_at=stale_timestamp,
            frozen_deadline=stale_deadline
        )

        att = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att, question=self.q1, is_correct=True)

        reconciled = reconcile_topic_progress(self.learner, self.topic1)
        self.assertEqual(reconciled.status, TopicProgress.STATUS_COMPLETED)
        # Option A: Stale timestamp and stale deadline replaced
        self.assertEqual(reconciled.completed_at, att.created_at)
        expected_deadline = calculate_nominal_deadline(self.doj, 15)
        self.assertEqual(reconciled.frozen_deadline, expected_deadline)
        self.assertNotEqual(reconciled.frozen_deadline, stale_deadline)

    def test_option_a_non_assessed_topic_replaces_stale_deadline(self):
        topic_na = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase,
            name='Topic NA Stale',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )
        stale_deadline = date(2025, 6, 1)
        stale_timestamp = timezone.now() - timedelta(days=200)
        TopicProgress.objects.create(
            user=self.learner,
            topic=topic_na,
            status=TopicProgress.STATUS_IN_PROGRESS,
            completed_at=stale_timestamp,
            frozen_deadline=stale_deadline
        )

        tp = record_topic_view(self.learner, topic_na)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        expected_deadline = calculate_nominal_deadline(self.doj, 15)
        self.assertEqual(tp.frozen_deadline, expected_deadline)
        self.assertNotEqual(tp.frozen_deadline, stale_deadline)

    def test_option_a_does_not_apply_to_already_completed_records(self):
        established_deadline = date(2026, 1, 24)
        established_time = timezone.now() - timedelta(days=5)
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=established_time,
            frozen_deadline=established_deadline
        )
        reconciled = reconcile_topic_progress(self.learner, self.topic1)
        self.assertEqual(reconciled.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(reconciled.completed_at, established_time)
        self.assertEqual(reconciled.frozen_deadline, established_deadline)

    # --------------------------------------------------------------------------
    # GROUP 4: Legacy Invariants & Zero Backfill (3 Tests)
    # --------------------------------------------------------------------------
    def test_legacy_completed_record_retains_null_deadline_on_reconcile(self):
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=None,
            frozen_deadline=None
        )
        reconciled = reconcile_topic_progress(self.learner, self.topic1)
        self.assertEqual(reconciled.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNone(reconciled.frozen_deadline)

    def test_legacy_timestamp_repair_does_not_backfill_deadline(self):
        att = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att, question=self.q1, is_correct=True)

        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=None,
            frozen_deadline=None
        )
        reconciled = reconcile_topic_progress(self.learner, self.topic1)
        # Timestamp is repaired from attempt
        self.assertEqual(reconciled.completed_at, att.created_at)
        # Deadline MUST remain None (Zero Backfill)
        self.assertIsNone(reconciled.frozen_deadline)

    def test_legacy_completed_record_timing_unavailable_in_phase(self):
        TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now(),
            frozen_deadline=None
        )
        progress_map = {self.topic1.id: TopicProgress.objects.get(user=self.learner, topic=self.topic1)}
        phase_eval = calculate_phase_performance(
            phase=self.phase,
            active_topics=[self.topic1],
            progress_map=progress_map,
            date_of_joining=self.doj
        )
        self.assertEqual(phase_eval['status'], PhasePerformanceStatus.COMPLETED_TIMING_UNAVAILABLE)
        self.assertFalse(phase_eval['is_eligible_for_on_time_rate'])

    # --------------------------------------------------------------------------
    # GROUP 5: Option 2B (Status-Trust) & Invalidation Safety (4 Tests)
    # --------------------------------------------------------------------------
    def test_failed_retake_does_not_revoke_completion_or_deadline(self):
        att1 = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att1, question=self.q1, is_correct=True)
        reconcile_topic_progress(self.learner, self.topic1)

        tp_before = TopicProgress.objects.get(user=self.learner, topic=self.topic1)
        original_completed_at = tp_before.completed_at
        original_deadline = tp_before.frozen_deadline

        # Retake fails
        att2 = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=2,
            correct_count=0,
            total_questions=1,
            score=0,
            passed=False,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att2, question=self.q1, is_correct=False)
        reconcile_topic_progress(self.learner, self.topic1)

        tp_after = TopicProgress.objects.get(user=self.learner, topic=self.topic1)
        self.assertEqual(tp_after.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp_after.completed_at, original_completed_at)
        self.assertEqual(tp_after.frozen_deadline, original_deadline)

    def test_pending_manual_review_does_not_revoke_earlier_pass(self):
        att1 = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att1, question=self.q1, is_correct=True)
        reconcile_topic_progress(self.learner, self.topic1)

        tp_before = TopicProgress.objects.get(user=self.learner, topic=self.topic1)
        original_deadline = tp_before.frozen_deadline

        # Retake with pending ungraded response
        att2 = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=2,
            correct_count=0,
            total_questions=1,
            score=0,
            passed=False,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att2, question=self.q1, is_correct=None)
        reconcile_topic_progress(self.learner, self.topic1)

        tp_after = TopicProgress.objects.get(user=self.learner, topic=self.topic1)
        self.assertEqual(tp_after.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp_after.frozen_deadline, original_deadline)

    def test_manual_review_approval_preserves_submission_timestamp(self):
        att = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=1,
            correct_count=0,
            total_questions=1,
            score=0,
            passed=False,
            passing_score_used=75
        )
        resp = QuizResponse.objects.create(attempt=att, question=self.q1, is_correct=None)

        tp = reconcile_topic_progress(self.learner, self.topic1)
        self.assertNotEqual(tp.status, TopicProgress.STATUS_COMPLETED)

        # Admin approves response
        resp.is_correct = True
        resp.save()
        att.correct_count = 1
        att.score = 100
        att.passed = True
        att.save()

        tp_graded = reconcile_topic_progress(self.learner, self.topic1)
        self.assertEqual(tp_graded.status, TopicProgress.STATUS_COMPLETED)
        # completed_at is the attempt submission time
        self.assertEqual(tp_graded.completed_at, att.created_at)
        expected_deadline = calculate_nominal_deadline(self.doj, 15)
        self.assertEqual(tp_graded.frozen_deadline, expected_deadline)

    def test_genuine_invalidation_clears_deadline(self):
        # Genuine invalidation: when calculate_reconciled_fields evaluates an in-progress record
        # with stale completion metadata, the absence of a passing attempt clears completed_at and frozen_deadline.
        stale_deadline = date(2026, 1, 24)
        stale_time = timezone.now() - timedelta(days=10)

        # Case 1: 0 attempts -> NOT_STARTED, completed_at=None, frozen_deadline=None
        res_zero = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=[],
            current_status=TopicProgress.STATUS_IN_PROGRESS,
            current_completed_at=stale_time,
            current_frozen_deadline=stale_deadline
        )
        self.assertEqual(res_zero['status'], TopicProgress.STATUS_NOT_STARTED)
        self.assertIsNone(res_zero['completed_at'])
        self.assertIsNone(res_zero['frozen_deadline'])

        # Case 2: Failing attempts only -> IN_PROGRESS, completed_at=None, frozen_deadline=None
        failing_attempts = [{'score': 30, 'passed': False, 'created_at': timezone.now(), 'is_fully_graded': True}]
        res_fail = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=failing_attempts,
            current_status=TopicProgress.STATUS_IN_PROGRESS,
            current_completed_at=stale_time,
            current_frozen_deadline=stale_deadline
        )
        self.assertEqual(res_fail['status'], TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(res_fail['completed_at'])
        self.assertIsNone(res_fail['frozen_deadline'])

    # --------------------------------------------------------------------------
    # GROUP 6: Schedule Drift & Topic Movement (3 Tests)
    # --------------------------------------------------------------------------
    def test_phase_schedule_change_does_not_alter_frozen_deadline(self):
        att = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att, question=self.q1, is_correct=True)
        tp = reconcile_topic_progress(self.learner, self.topic1)
        original_deadline = tp.frozen_deadline

        # Admin shifts phase schedule
        self.phase.end_day = 45
        self.phase.save()

        tp_reconciled = reconcile_topic_progress(self.learner, self.topic1)
        self.assertEqual(tp_reconciled.frozen_deadline, original_deadline)

    def test_learner_doj_change_does_not_alter_frozen_deadline(self):
        att = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att, question=self.q1, is_correct=True)
        tp = reconcile_topic_progress(self.learner, self.topic1)
        original_deadline = tp.frozen_deadline

        # Admin edits learner DOJ
        self.learner.date_of_joining = date(2026, 3, 1)
        self.learner.save()

        tp_reconciled = reconcile_topic_progress(self.learner, self.topic1)
        self.assertEqual(tp_reconciled.frozen_deadline, original_deadline)

    def test_topic_reassignment_preserves_completion_and_deadline(self):
        att = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic1,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att, question=self.q1, is_correct=True)
        tp = reconcile_topic_progress(self.learner, self.topic1)
        original_deadline = tp.frozen_deadline

        # Move topic to phase 2
        phase2 = LearningPhase.objects.create(subject=self.subject, name='Phase 2', start_day=16, end_day=30)
        self.topic1.learning_phase = phase2
        self.topic1.save()

        tp_reconciled = reconcile_topic_progress(self.learner, self.topic1)
        self.assertEqual(tp_reconciled.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp_reconciled.frozen_deadline, original_deadline)

    # --------------------------------------------------------------------------
    # GROUP 7: Phase Aggregation, Eligibility & KPIs (5 Tests)
    # --------------------------------------------------------------------------
    def test_empty_phase_and_no_accessible_topics_never_completed(self):
        empty_phase = LearningPhase.objects.create(subject=self.subject, name='Empty Phase', start_day=31, end_day=45)
        eval_result = calculate_phase_performance(empty_phase, [], {}, self.doj)
        self.assertEqual(eval_result['status'], PhasePerformanceStatus.EMPTY)
        self.assertTrue(eval_result['is_empty'])

    def test_missing_progress_row_counts_as_incomplete(self):
        topic2 = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase,
            name='Topic 2',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        # Topic 1 completed, topic 2 has no TopicProgress record
        TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now(),
            frozen_deadline=date(2026, 1, 24)
        )
        progress_map = {self.topic1.id: TopicProgress.objects.get(user=self.learner, topic=self.topic1)}
        phase_eval = calculate_phase_performance(
            phase=self.phase,
            active_topics=[self.topic1, topic2],
            progress_map=progress_map,
            date_of_joining=self.doj,
            as_of_date=date(2026, 1, 12)
        )
        self.assertNotEqual(phase_eval['status'], PhasePerformanceStatus.COMPLETED_ON_TIME)
        self.assertEqual(phase_eval['status'], PhasePerformanceStatus.ON_TRACK)

    def test_completed_phase_with_null_deadline_excluded_from_on_time_rate(self):
        # All required topics completed, but frozen_deadline is None
        TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now(),
            frozen_deadline=None
        )
        progress_map = {self.topic1.id: TopicProgress.objects.get(user=self.learner, topic=self.topic1)}
        phase_eval = calculate_phase_performance(
            phase=self.phase,
            active_topics=[self.topic1],
            progress_map=progress_map,
            date_of_joining=self.doj
        )
        self.assertEqual(phase_eval['status'], PhasePerformanceStatus.COMPLETED_TIMING_UNAVAILABLE)
        self.assertFalse(phase_eval['is_eligible_for_on_time_rate'])

    def test_on_time_and_overdue_days_calculation(self):
        # Nominal deadline = 2026-01-24
        # 1. On time completion
        res_ontime = evaluate_timeline_status(
            start_day=1,
            end_day=15,
            date_of_joining=self.doj,
            is_completed=True,
            completion_date=date(2026, 1, 24),
            is_timing_verified=True
        )
        self.assertEqual(res_ontime['status'], PhasePerformanceStatus.COMPLETED_ON_TIME)
        self.assertEqual(res_ontime['days_late'], 0)

        # 2. Late completion (+3 days)
        res_late = evaluate_timeline_status(
            start_day=1,
            end_day=15,
            date_of_joining=self.doj,
            is_completed=True,
            completion_date=date(2026, 1, 27),
            is_timing_verified=True
        )
        self.assertEqual(res_late['status'], PhasePerformanceStatus.COMPLETED_LATE)
        self.assertEqual(res_late['days_late'], 3)

        # 3. Overdue (as of 2026-01-28 -> +4 days overdue)
        res_overdue = evaluate_timeline_status(
            start_day=1,
            end_day=15,
            date_of_joining=self.doj,
            is_completed=False,
            as_of_date=date(2026, 1, 28),
            is_timing_verified=True
        )
        self.assertEqual(res_overdue['status'], PhasePerformanceStatus.OVERDUE)
        self.assertEqual(res_overdue['days_overdue'], 4)

    def test_existing_scoring_and_access_unaffected(self):
        # Verify passing score threshold inheritance
        self.assertEqual(get_effective_passing_score(self.topic1), 75)
        self.subject.passing_score_override = 85
        self.subject.save()
        self.assertEqual(get_effective_passing_score(self.topic1), 85)
        self.topic1.passing_score_override = 90
        self.topic1.save()
        self.assertEqual(get_effective_passing_score(self.topic1), 90)

        # Verify progression unlock
        self.assertTrue(is_topic_unlocked(self.learner, self.topic1))

    # --------------------------------------------------------------------------
    # GROUP 8: Stage 1 Topic-Level Frozen Deadline Immutability Tests
    # --------------------------------------------------------------------------
    def test_completed_topic_remains_on_time_when_doj_changes(self):
        """1. Completed topic with frozen deadline remains on-time when learner DOJ
        is subsequently shifted such that recalculating from current DOJ would appear late."""
        # Baseline: DOJ = 2026-01-10. Phase Day 1 to 15. Nominal deadline = 2026-01-24.
        # Learner completes on 2026-01-20 (on time). Frozen deadline is saved as 2026-01-24.
        completion_time = datetime(2026, 1, 20, 10, 0, tzinfo=dt_timezone.utc)
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=completion_time,
            frozen_deadline=date(2026, 1, 24)
        )

        # Verify initial evaluation is on time
        eval_before = calculate_topic_performance(self.topic1, tp, self.learner.date_of_joining)
        self.assertEqual(eval_before['status'], PhasePerformanceStatus.COMPLETED_ON_TIME)
        self.assertEqual(eval_before['days_late'], 0)
        self.assertEqual(eval_before['nominal_deadline'], date(2026, 1, 24))

        # Admin shifts learner's DOJ to 2026-01-01 (10 days earlier)
        # Recalculating from current DOJ (2026-01-01) + 14 days would yield 2026-01-15,
        # which would make 2026-01-20 appear 5 days late!
        self.learner.date_of_joining = date(2026, 1, 1)
        self.learner.save()

        # Authoritative frozen deadline MUST prevail
        eval_after = calculate_topic_performance(self.topic1, tp, self.learner.date_of_joining)
        self.assertEqual(eval_after['status'], PhasePerformanceStatus.COMPLETED_ON_TIME)
        self.assertEqual(eval_after['days_late'], 0)
        self.assertEqual(eval_after['nominal_deadline'], date(2026, 1, 24))
        self.assertTrue(eval_after['is_timing_verified'])

    def test_completed_topic_remains_late_when_phase_schedule_changes(self):
        """2. Completed topic with frozen deadline remains late when a subsequent
        phase schedule extension would otherwise make it appear on time."""
        # Baseline: DOJ = 2026-01-10. Phase Day 1 to 15. Nominal deadline = 2026-01-24.
        # Learner completes on 2026-01-28 (4 days late). Frozen deadline = 2026-01-24.
        completion_time = datetime(2026, 1, 28, 10, 0, tzinfo=dt_timezone.utc)
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=completion_time,
            frozen_deadline=date(2026, 1, 24)
        )

        eval_before = calculate_topic_performance(self.topic1, tp, self.learner.date_of_joining)
        self.assertEqual(eval_before['status'], PhasePerformanceStatus.COMPLETED_LATE)
        self.assertEqual(eval_before['days_late'], 4)
        self.assertEqual(eval_before['nominal_deadline'], date(2026, 1, 24))

        # Admin extends phase schedule to Day 30 (end_day = 30).
        # Recalculating from DOJ (2026-01-10) + 29 days would yield 2026-02-08,
        # which would otherwise make 2026-01-28 appear on time!
        self.phase.end_day = 30
        self.phase.save()

        # Authoritative frozen deadline MUST prevail: remains late (+4d)
        eval_after = calculate_topic_performance(self.topic1, tp, self.learner.date_of_joining)
        self.assertEqual(eval_after['status'], PhasePerformanceStatus.COMPLETED_LATE)
        self.assertEqual(eval_after['days_late'], 4)
        self.assertEqual(eval_after['nominal_deadline'], date(2026, 1, 24))
        self.assertTrue(eval_after['is_timing_verified'])

    def test_deadline_day_remains_inclusive_with_benchmark_deadline(self):
        """3. Completion exactly on frozen deadline is on time (days_late=0);
        completion one calendar day after is late (+1d)."""
        frozen_dl = date(2026, 1, 24)

        # Exact deadline day completion (2026-01-24 in Asia/Kolkata)
        # 12:00 UTC on Jan 24 is 17:30 IST on Jan 24 -> localdate = 2026-01-24
        on_time_dt = datetime(2026, 1, 24, 12, 0, tzinfo=dt_timezone.utc)
        tp_ontime = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=on_time_dt,
            frozen_deadline=frozen_dl
        )
        res_ontime = calculate_topic_performance(self.topic1, tp_ontime, self.learner.date_of_joining)
        self.assertEqual(res_ontime['status'], PhasePerformanceStatus.COMPLETED_ON_TIME)
        self.assertEqual(res_ontime['days_late'], 0)
        self.assertEqual(res_ontime['nominal_deadline'], frozen_dl)

        # One day late completion (2026-01-25 in Asia/Kolkata)
        late_dt = datetime(2026, 1, 25, 12, 0, tzinfo=dt_timezone.utc)
        topic_1b = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase,
            name='Topic 1b',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        tp_late = TopicProgress.objects.create(
            user=self.learner,
            topic=topic_1b,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=late_dt,
            frozen_deadline=frozen_dl
        )
        res_late = calculate_topic_performance(tp_late.topic, tp_late, self.learner.date_of_joining)
        self.assertEqual(res_late['status'], PhasePerformanceStatus.COMPLETED_LATE)
        self.assertEqual(res_late['days_late'], 1)
        self.assertEqual(res_late['nominal_deadline'], frozen_dl)

    def test_missing_frozen_deadline_remains_timing_unavailable(self):
        """4. Completed topic with null frozen deadline remains timing unavailable."""
        tp_no_deadline = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now(),
            frozen_deadline=None
        )
        res = calculate_topic_performance(self.topic1, tp_no_deadline, self.learner.date_of_joining)
        self.assertEqual(res['status'], PhasePerformanceStatus.COMPLETED_TIMING_UNAVAILABLE)
        self.assertFalse(res['is_timing_verified'])
        self.assertFalse(res['is_eligible_for_on_time_rate'])
        self.assertIn('deadline was not recorded', res['timing_unavailable_reason'])

    def test_no_phase_topic_behavior_remains_unchanged(self):
        """5. Completed topic with no learning phase evaluates as NO_PHASE,
        not as a timing failure or timing unavailable."""
        topic_unphased = Topic.objects.create(
            subject=self.subject,
            learning_phase=None,
            name='Unphased Topic',
            order=99,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        tp_unphased = TopicProgress.objects.create(
            user=self.learner,
            topic=topic_unphased,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now(),
            frozen_deadline=None
        )
        res = calculate_topic_performance(topic_unphased, tp_unphased, self.learner.date_of_joining)
        self.assertEqual(res['status'], PhasePerformanceStatus.NO_PHASE)
        self.assertEqual(res['status_label'], 'Completed (No Phase)')
        self.assertIsNone(res['nominal_deadline'])
        self.assertFalse(res['is_timing_verified'])
        self.assertFalse(res['is_eligible_for_on_time_rate'])
        self.assertEqual(res['timing_unavailable_reason'], 'Topic is not assigned to a learning phase.')

    # --------------------------------------------------------------------------
    # GROUP 9: Stage 2 Phase-Level P1 Frozen Deadline Evaluation Tests
    # --------------------------------------------------------------------------
    def test_p1_selection_with_multiple_completed_topics_distinguishes_from_latest_deadline(self):
        """1. P1 selects triggering topic's frozen deadline as phase benchmark.
        Crucially distinguishes P1 from picking the latest deadline across topics:
        Topic A completes later (triggering topic) with an earlier deadline (Late +5d),
        while Topic B completed earlier with a much later deadline (which would be On Time if chosen)."""
        subject = Subject.objects.create(name='P1 Multi Subject', slug='p1-multi-sub')
        phase_p1 = LearningPhase.objects.create(
            subject=subject,
            name='Phase P1 Multi',
            start_day=1,
            end_day=20
        )
        topic_a = Topic.objects.create(
            subject=subject,
            learning_phase=phase_p1,
            name='Topic A',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        topic_b = Topic.objects.create(
            subject=subject,
            learning_phase=phase_p1,
            name='Topic B',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )

        # Topic B completed on Jan 18 with frozen deadline Jan 25
        dt_b = datetime(2026, 1, 18, 10, 0, tzinfo=dt_timezone.utc)
        tp_b = TopicProgress.objects.create(
            user=self.learner,
            topic=topic_b,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=dt_b,
            frozen_deadline=date(2026, 1, 25)
        )

        # Topic A completed on Jan 20 with frozen deadline Jan 15 (Topic A triggers phase completion)
        dt_a = datetime(2026, 1, 20, 10, 0, tzinfo=dt_timezone.utc)
        tp_a = TopicProgress.objects.create(
            user=self.learner,
            topic=topic_a,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=dt_a,
            frozen_deadline=date(2026, 1, 15)
        )

        progress_map = {
            topic_a.id: tp_a,
            topic_b.id: tp_b,
        }

        eval_result = calculate_phase_performance(
            phase=phase_p1,
            active_topics=[topic_a, topic_b],
            progress_map=progress_map,
            date_of_joining=self.doj
        )

        # Under P1: Topic A triggered completion (completed_at Jan 20 > Jan 18).
        # Its deadline is Jan 15. Completion date Jan 20 > Jan 15 by 5 days -> COMPLETED_LATE (+5d).
        # If naive logic picked max(deadline across topics), it would pick Jan 25, which would wrongly evaluate to ON TIME.
        self.assertEqual(eval_result['status'], PhasePerformanceStatus.COMPLETED_LATE)
        self.assertEqual(eval_result['days_late'], 5)
        self.assertEqual(eval_result['nominal_deadline'], date(2026, 1, 15))
        self.assertEqual(eval_result['completed_at'], dt_a)
        self.assertTrue(eval_result['is_timing_verified'])
        self.assertTrue(eval_result['is_eligible_for_on_time_rate'])

    def test_p1_deterministic_tie_breaker_when_completion_timestamps_equal(self):
        """Tie-breaker test: when completion timestamps are equal,
        deterministic ordering (Topic order, then id) selects the triggering topic."""
        subject = Subject.objects.create(name='P1 Tie Subject', slug='p1-tie-sub')
        phase_tie = LearningPhase.objects.create(
            subject=subject,
            name='Phase Tie',
            start_day=1,
            end_day=20
        )
        topic_1 = Topic.objects.create(
            subject=subject,
            learning_phase=phase_tie,
            name='Topic 1',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        topic_2 = Topic.objects.create(
            subject=subject,
            learning_phase=phase_tie,
            name='Topic 2',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )

        same_dt = datetime(2026, 1, 20, 10, 0, tzinfo=dt_timezone.utc)
        tp_1 = TopicProgress.objects.create(
            user=self.learner,
            topic=topic_1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=same_dt,
            frozen_deadline=date(2026, 1, 18)
        )
        tp_2 = TopicProgress.objects.create(
            user=self.learner,
            topic=topic_2,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=same_dt,
            frozen_deadline=date(2026, 1, 22)
        )

        progress_map = {
            topic_1.id: tp_1,
            topic_2.id: tp_2,
        }

        eval_result = calculate_phase_performance(
            phase=phase_tie,
            active_topics=[topic_1, topic_2],
            progress_map=progress_map,
            date_of_joining=self.doj
        )

        # Topic 2 has order 2 > order 1, so Topic 2 breaks tie. Its deadline is Jan 22.
        # Jan 20 <= Jan 22 -> COMPLETED_ON_TIME.
        self.assertEqual(eval_result['status'], PhasePerformanceStatus.COMPLETED_ON_TIME)
        self.assertEqual(eval_result['nominal_deadline'], date(2026, 1, 22))

    def test_phase_status_immutable_when_learner_doj_changes_after_completion(self):
        """2. DOJ changes after phase completion do not alter historical phase status or deadline."""
        subject = Subject.objects.create(name='P1 DOJ Subject', slug='p1-doj-sub')
        phase_doj = LearningPhase.objects.create(
            subject=subject,
            name='Phase DOJ Immutability',
            start_day=1,
            end_day=15
        )
        topic = Topic.objects.create(
            subject=subject,
            learning_phase=phase_doj,
            name='Phase Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        dt_comp = datetime(2026, 1, 24, 12, 0, tzinfo=dt_timezone.utc)
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=topic,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=dt_comp,
            frozen_deadline=date(2026, 1, 24)
        )

        progress_map = {topic.id: tp}
        eval_before = calculate_phase_performance(
            phase=phase_doj,
            active_topics=[topic],
            progress_map=progress_map,
            date_of_joining=self.doj
        )
        self.assertEqual(eval_before['status'], PhasePerformanceStatus.COMPLETED_ON_TIME)
        self.assertEqual(eval_before['nominal_deadline'], date(2026, 1, 24))

        # Admin edits learner DOJ to 10 days earlier (2025-12-31)
        # If dynamically recomputed, deadline would become 2026-01-14 -> LATE (+10d)
        new_doj = date(2025, 12, 31)
        self.learner.date_of_joining = new_doj
        self.learner.save()

        eval_after = calculate_phase_performance(
            phase=phase_doj,
            active_topics=[topic],
            progress_map=progress_map,
            date_of_joining=new_doj
        )
        self.assertEqual(eval_after['status'], PhasePerformanceStatus.COMPLETED_ON_TIME)
        self.assertEqual(eval_after['nominal_deadline'], date(2026, 1, 24))
        self.assertEqual(eval_after['days_late'], 0)
        self.assertTrue(eval_after['is_timing_verified'])

    def test_phase_status_immutable_when_phase_schedule_changes_after_completion(self):
        """3. Phase end_day changes after completion do not flip historical phase status."""
        subject = Subject.objects.create(name='P1 Sched Subject', slug='p1-sched-sub')
        phase_sched = LearningPhase.objects.create(
            subject=subject,
            name='Phase Schedule Immutability',
            start_day=1,
            end_day=10
        )
        topic = Topic.objects.create(
            subject=subject,
            learning_phase=phase_sched,
            name='Phase Topic Sched',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        # Completed on 2026-01-23 with deadline 2026-01-19 (4 days late)
        dt_comp = datetime(2026, 1, 23, 12, 0, tzinfo=dt_timezone.utc)
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=topic,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=dt_comp,
            frozen_deadline=date(2026, 1, 19)
        )

        progress_map = {topic.id: tp}
        eval_before = calculate_phase_performance(
            phase=phase_sched,
            active_topics=[topic],
            progress_map=progress_map,
            date_of_joining=self.doj
        )
        self.assertEqual(eval_before['status'], PhasePerformanceStatus.COMPLETED_LATE)
        self.assertEqual(eval_before['days_late'], 4)
        self.assertEqual(eval_before['nominal_deadline'], date(2026, 1, 19))

        # Admin shifts phase end_day from 10 to 30.
        # If dynamically recomputed, deadline would become 2026-02-08 -> ON TIME.
        phase_sched.end_day = 30
        phase_sched.save()

        eval_after = calculate_phase_performance(
            phase=phase_sched,
            active_topics=[topic],
            progress_map=progress_map,
            date_of_joining=self.doj
        )
        self.assertEqual(eval_after['status'], PhasePerformanceStatus.COMPLETED_LATE)
        self.assertEqual(eval_after['days_late'], 4)
        self.assertEqual(eval_after['nominal_deadline'], date(2026, 1, 19))
        self.assertTrue(eval_after['is_timing_verified'])

    def test_missing_trigger_topic_frozen_deadline_remains_timing_unavailable(self):
        """4. If the triggering topic lacks a frozen deadline, phase remains timing unavailable.
        The phase does NOT borrow another topic's deadline or recalculate one from schedule."""
        subject = Subject.objects.create(name='P1 Missing DL Subject', slug='p1-missing-dl-sub')
        phase_no_dl = LearningPhase.objects.create(
            subject=subject,
            name='Phase Missing Trigger DL',
            start_day=1,
            end_day=20
        )
        topic_first = Topic.objects.create(
            subject=subject,
            learning_phase=phase_no_dl,
            name='First Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        topic_trigger = Topic.objects.create(
            subject=subject,
            learning_phase=phase_no_dl,
            name='Trigger Topic',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )

        # topic_first has a valid frozen deadline
        dt_first = datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc)
        tp_first = TopicProgress.objects.create(
            user=self.learner,
            topic=topic_first,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=dt_first,
            frozen_deadline=date(2026, 1, 25)
        )

        # topic_trigger is completed later (triggering topic), but lacks a frozen deadline
        dt_trigger = datetime(2026, 1, 20, 10, 0, tzinfo=dt_timezone.utc)
        tp_trigger = TopicProgress.objects.create(
            user=self.learner,
            topic=topic_trigger,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=dt_trigger,
            frozen_deadline=None
        )

        progress_map = {
            topic_first.id: tp_first,
            topic_trigger.id: tp_trigger,
        }

        eval_result = calculate_phase_performance(
            phase=phase_no_dl,
            active_topics=[topic_first, topic_trigger],
            progress_map=progress_map,
            date_of_joining=self.doj
        )

        # Must not borrow topic_first's deadline (date(2026, 1, 25)).
        # Must evaluate to COMPLETED_TIMING_UNAVAILABLE.
        self.assertEqual(eval_result['status'], PhasePerformanceStatus.COMPLETED_TIMING_UNAVAILABLE)
        self.assertFalse(eval_result['is_timing_verified'])
        self.assertFalse(eval_result['is_eligible_for_on_time_rate'])
        self.assertNotEqual(eval_result['nominal_deadline'], date(2026, 1, 25))
        self.assertEqual(eval_result['completed_topics'], 2)
        self.assertEqual(eval_result['total_topics'], 2)

    def test_existing_incomplete_phase_behavior_remains_unchanged(self):
        """5. Incomplete phase behavior remains unchanged across on-track, due-soon, and overdue."""
        subject = Subject.objects.create(name='P1 Incomplete Subject', slug='p1-incomplete-sub')
        phase_incomp = LearningPhase.objects.create(
            subject=subject,
            name='Phase Incomplete',
            start_day=1,
            end_day=20
        )
        topic_1 = Topic.objects.create(
            subject=subject,
            learning_phase=phase_incomp,
            name='T1',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        topic_2 = Topic.objects.create(
            subject=subject,
            learning_phase=phase_incomp,
            name='T2',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )

        # Complete only T1
        tp_1 = TopicProgress.objects.create(
            user=self.learner,
            topic=topic_1,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc),
            frozen_deadline=date(2026, 1, 29)
        )
        progress_map = {topic_1.id: tp_1}

        # Case A: On track (as_of_date = 2026-01-15, deadline = 2026-01-29)
        eval_ontrack = calculate_phase_performance(
            phase=phase_incomp,
            active_topics=[topic_1, topic_2],
            progress_map=progress_map,
            date_of_joining=self.doj,
            as_of_date=date(2026, 1, 15)
        )
        self.assertEqual(eval_ontrack['status'], PhasePerformanceStatus.ON_TRACK)
        self.assertEqual(eval_ontrack['completed_topics'], 1)
        self.assertEqual(eval_ontrack['total_topics'], 2)
        self.assertFalse(eval_ontrack['is_empty'])

        # Case B: Overdue (as_of_date = 2026-02-05, deadline = 2026-01-29 -> 7 days overdue)
        eval_overdue = calculate_phase_performance(
            phase=phase_incomp,
            active_topics=[topic_1, topic_2],
            progress_map=progress_map,
            date_of_joining=self.doj,
            as_of_date=date(2026, 2, 5)
        )
        self.assertEqual(eval_overdue['status'], PhasePerformanceStatus.OVERDUE)
        self.assertEqual(eval_overdue['days_overdue'], 7)

    def test_existing_no_phase_and_empty_phase_behavior_remains_unchanged(self):
        """6. Existing EMPTY phase and NO_PHASE behavior remains strictly intact."""
        subject = Subject.objects.create(name='P1 Empty Subject', slug='p1-empty-sub')
        empty_phase = LearningPhase.objects.create(
            subject=subject,
            name='Phase Zero Topics',
            start_day=1,
            end_day=10
        )
        eval_empty = calculate_phase_performance(
            phase=empty_phase,
            active_topics=[],
            progress_map={},
            date_of_joining=self.doj
        )
        self.assertEqual(eval_empty['status'], PhasePerformanceStatus.EMPTY)
        self.assertTrue(eval_empty['is_empty'])
        self.assertEqual(eval_empty['total_topics'], 0)
        self.assertEqual(eval_empty['completed_topics'], 0)
        self.assertIsNone(eval_empty['nominal_deadline'])
        self.assertFalse(eval_empty['is_timing_verified'])

        # Unphased topic evaluated with calculate_topic_performance
        unphased_top = Topic.objects.create(
            subject=subject,
            learning_phase=None,
            name='Unphased Topic Stage 2',
            order=100,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        tp_unphased = TopicProgress.objects.create(
            user=self.learner,
            topic=unphased_top,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=timezone.now(),
            frozen_deadline=None
        )
        top_eval = calculate_topic_performance(unphased_top, tp_unphased, self.learner.date_of_joining)
        self.assertEqual(top_eval['status'], PhasePerformanceStatus.NO_PHASE)
        self.assertEqual(top_eval['status_label'], 'Completed (No Phase)')
        self.assertIsNone(top_eval['nominal_deadline'])

    # --------------------------------------------------------------------------
    # GROUP 10: Stage 3A Saved Schedule Snapshot Tests
    # --------------------------------------------------------------------------
    def test_incomplete_topic_performance_uses_expected_end_day_snapshot(self):
        """1 & 3. Incomplete topic captures snapshot day 10. Phase end_day later shifts to 25.
        Topic performance evaluation must use day 10, NOT day 25.
        Assert that as_of_date after day 10 evaluates as OVERDUE, whereas if day 25 was used it would be ON_TRACK."""
        sub = Subject.objects.create(name='Snap Incomp Subj', slug='snap-incomp-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Snap Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Snap Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )

        # Learner starts topic: snapshot = 10
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=topic,
            status=TopicProgress.STATUS_IN_PROGRESS,
            expected_end_day_snapshot=10
        )

        # Phase schedule shifts to end_day = 25
        phase.end_day = 25
        phase.save()

        # DOJ = 2026-01-10.
        # Day 10 deadline = 2026-01-10 + 9 days = 2026-01-19.
        # If naive/buggy code used current phase end_day (25): deadline = 2026-02-03.
        # As of 2026-01-22:
        # Under snapshot day 10: 2026-01-22 > 2026-01-19 (+3d overdue) -> OVERDUE.
        # Under current schedule day 25: 2026-01-22 < 2026-02-03 -> ON_TRACK.
        res = calculate_topic_performance(topic, tp, self.learner.date_of_joining, as_of_date=date(2026, 1, 22))

        # Confirms snapshot takes precedence over current phase schedule
        self.assertEqual(res['status'], PhasePerformanceStatus.OVERDUE)
        self.assertEqual(res['days_overdue'], 3)
        self.assertEqual(res['nominal_deadline'], date(2026, 1, 19))

    def test_first_completion_after_schedule_drift_freezes_snapshot_deadline(self):
        """2. TopicProgress captures snapshot day 10. Phase end_day later shifts to 25.
        When learner passes quiz, frozen_deadline must use snapshot day 10 (2026-01-19),
        NOT the newer current phase end_day 25 (2026-02-03)."""
        sub = Subject.objects.create(name='Snap First Comp Subj', slug='snap-first-comp-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Snap Comp Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Snap Comp Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q = Question.objects.create(topic=topic, text='Q', order=1, is_active=True)
        Choice.objects.create(question=q, text='A', is_correct=True)

        # Progress instantiated with snapshot = 10
        TopicProgress.objects.create(
            user=self.learner,
            topic=topic,
            status=TopicProgress.STATUS_IN_PROGRESS,
            expected_end_day_snapshot=10
        )

        # Phase schedule shifts to end_day = 25 before completion
        phase.end_day = 25
        phase.save()

        # Learner passes quiz
        att = QuizAttempt.objects.create(
            user=self.learner,
            topic=topic,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att, question=q, is_correct=True)

        tp_reconciled = reconcile_topic_progress(self.learner, topic)
        self.assertEqual(tp_reconciled.status, TopicProgress.STATUS_COMPLETED)

        # Frozen deadline must be calculated from snapshot 10 (DOJ 2026-01-10 + 9d = 2026-01-19),
        # strictly not 2026-02-03
        expected_dl = calculate_nominal_deadline(self.doj, 10)
        self.assertEqual(tp_reconciled.frozen_deadline, expected_dl)
        self.assertEqual(tp_reconciled.frozen_deadline, date(2026, 1, 19))

    def test_missing_snapshot_falls_back_safely_without_fabrication(self):
        """4. If expected_end_day_snapshot is None, safe fallback uses phase.end_day,
        and no historical snapshot is fabricated on the model."""
        sub = Subject.objects.create(name='Snap None Subj', slug='snap-none-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Snap None Phase', start_day=1, end_day=15)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Snap None Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )

        # TopicProgress with null snapshot
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=topic,
            status=TopicProgress.STATUS_IN_PROGRESS,
            expected_end_day_snapshot=None
        )

        # Incomplete evaluation falls back safely to phase.end_day (15) -> deadline 2026-01-24
        res = calculate_topic_performance(topic, tp, self.learner.date_of_joining, as_of_date=date(2026, 1, 15))
        self.assertEqual(res['status'], PhasePerformanceStatus.ON_TRACK)
        self.assertEqual(res['nominal_deadline'], date(2026, 1, 24))

        # Model snapshot remains None (no fabrication)
        tp.refresh_from_db()
        self.assertIsNone(tp.expected_end_day_snapshot)

    def test_assessed_topic_first_completion_on_null_snapshot_saves_snapshot_and_deadline(self):
        """When an existing TopicProgress record has expected_end_day_snapshot=None,
        genuine first completion through normal assessed-topic reconciliation must
        calculate frozen_deadline from phase.end_day and save that phase-end day
        into expected_end_day_snapshot, remaining immutable on subsequent reconciliation."""
        sub = Subject.objects.create(name='Null Snap First Comp Subj', slug='null-snap-first-comp-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Null Snap Phase', start_day=1, end_day=12)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Null Snap Assessed Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q = Question.objects.create(topic=topic, text='Q1', order=1, is_active=True)
        Choice.objects.create(question=q, text='Correct', is_correct=True)

        # Progress record exists first with IN_PROGRESS and null snapshot
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=topic,
            status=TopicProgress.STATUS_IN_PROGRESS,
            expected_end_day_snapshot=None
        )

        # Passing quiz attempt
        att = QuizAttempt.objects.create(
            user=self.learner,
            topic=topic,
            attempt_number=1,
            correct_count=1,
            total_questions=1,
            score=100,
            passed=True,
            passing_score_used=75
        )
        QuizResponse.objects.create(attempt=att, question=q, is_correct=True)

        # Reconcile ordinary application completion path
        tp_reconciled = reconcile_topic_progress(self.learner, topic)

        # Verify completion and frozen deadline
        self.assertEqual(tp_reconciled.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp_reconciled.completed_at, att.created_at)
        expected_deadline = calculate_nominal_deadline(self.doj, 12)
        self.assertEqual(tp_reconciled.frozen_deadline, expected_deadline)

        # Option B requirement: at genuine first completion, phase-end day
        # used for deadline should also be saved into expected_end_day_snapshot
        self.assertEqual(tp_reconciled.expected_end_day_snapshot, 12)

        # Verify database persistence
        tp.refresh_from_db()
        self.assertEqual(tp.expected_end_day_snapshot, 12)
        self.assertEqual(tp.frozen_deadline, expected_deadline)

        # Idempotence: subsequent reconciliation preserves snapshot and deadline
        tp_again = reconcile_topic_progress(self.learner, topic)
        self.assertEqual(tp_again.expected_end_day_snapshot, 12)
        self.assertEqual(tp_again.frozen_deadline, expected_deadline)
        self.assertEqual(tp_again.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp_again.completed_at, att.created_at)

    def test_completed_topic_immutability_retains_frozen_deadline_despite_schedule_changes(self):
        """5. A completed topic with an existing frozen deadline retains that deadline
        despite subsequent phase schedule drift, and reconciliation never overwrites it."""
        sub = Subject.objects.create(name='Snap Comp Immut Subj', slug='snap-comp-immut-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Snap Immut Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Snap Immut Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q = Question.objects.create(topic=topic, text='Q', order=1, is_active=True)
        Choice.objects.create(question=q, text='A', is_correct=True)

        original_frozen_dl = date(2026, 1, 19)
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=topic,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc),
            frozen_deadline=original_frozen_dl,
            expected_end_day_snapshot=10
        )

        # Admin drastically changes phase end_day to 40
        phase.end_day = 40
        phase.save()

        # Reconciliation maintains original frozen deadline
        tp_reconciled = reconcile_topic_progress(self.learner, topic)
        self.assertEqual(tp_reconciled.frozen_deadline, original_frozen_dl)

        # Evaluation maintains original frozen deadline
        res = calculate_topic_performance(topic, tp_reconciled, self.learner.date_of_joining)
        self.assertEqual(res['nominal_deadline'], original_frozen_dl)
        self.assertEqual(res['status'], PhasePerformanceStatus.COMPLETED_ON_TIME)

    def test_non_assessed_topic_first_completion_respects_snapshot(self):
        """Non-assessed topic: when first completion occurs,
        frozen_deadline respects expected_end_day_snapshot even if phase end_day changed."""
        sub = Subject.objects.create(name='Snap NonAssess Subj', slug='snap-nonassess-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Snap NonAssess Phase', start_day=1, end_day=12)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Non-Assessed Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )

        TopicProgress.objects.create(
            user=self.learner,
            topic=topic,
            status=TopicProgress.STATUS_IN_PROGRESS,
            expected_end_day_snapshot=12
        )

        # Phase schedule shifts to 30
        phase.end_day = 30
        phase.save()

        # Complete non-assessed topic
        comp_tp = complete_non_assessed_topic(self.learner, topic)
        self.assertEqual(comp_tp.status, TopicProgress.STATUS_COMPLETED)
        # Frozen deadline should be calculate_nominal_deadline(doj, 12) = 2026-01-21, NOT 30
        self.assertEqual(comp_tp.frozen_deadline, date(2026, 1, 21))

    # --------------------------------------------------------------------------
    # GROUP 11: Stage 3B Completion Validity, Missing DOJ Guard, & Manual Review
    # --------------------------------------------------------------------------
    def test_assessed_topic_completion_blocked_when_doj_is_missing(self):
        """1. Passing an assessed quiz without a valid DOJ records attempt & score,
        but keeps TopicProgress IN_PROGRESS with no completed_at or frozen_deadline."""
        learner_no_doj = User.objects.create_user(
            username='no_doj_learner_assessed',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=None
        )
        sub = Subject.objects.create(name='Stage3B Assessed Subj', slug='stage3b-assessed-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Stage3B Phase 1', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Stage3B Assessed Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q = Question.objects.create(topic=topic, text='Q1', question_type=Question.TYPE_SINGLE_CHOICE, order=1, is_active=True)
        c_good = Choice.objects.create(question=q, text='Correct', is_correct=True)
        Choice.objects.create(question=q, text='Wrong', is_correct=False)

        attempt, tp = submit_quiz_attempt(learner_no_doj, topic, {q.id: c_good.id})
        self.assertTrue(attempt.passed)
        self.assertEqual(attempt.score, 100)

        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)
        self.assertIsNone(tp.frozen_deadline)
        self.assertEqual(tp.best_score, 100)
        self.assertEqual(tp.latest_score, 100)

    def test_assessed_topic_completes_when_doj_is_subsequently_set(self):
        """2. When learner DOJ is subsequently provided, reconciliation transitions
        the blocked topic to COMPLETED with passing timestamp and frozen deadline."""
        learner = User.objects.create_user(
            username='no_doj_then_set_learner',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=None
        )
        sub = Subject.objects.create(name='Stage3B Subseq Subj', slug='stage3b-subseq-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Stage3B Subseq Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Stage3B Subseq Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q = Question.objects.create(topic=topic, text='Q1', question_type=Question.TYPE_SINGLE_CHOICE, order=1, is_active=True)
        c_good = Choice.objects.create(question=q, text='Correct', is_correct=True)

        attempt, tp = submit_quiz_attempt(learner, topic, {q.id: c_good.id})
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)

        # Admin assigns DOJ to learner
        learner.date_of_joining = date(2026, 1, 10)
        learner.save()

        tp_reconciled = reconcile_topic_progress(learner, topic)
        self.assertEqual(tp_reconciled.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp_reconciled.completed_at, attempt.created_at)
        self.assertEqual(tp_reconciled.frozen_deadline, date(2026, 1, 19))

    def test_manual_review_approval_with_missing_doj_blocks_topic_completion(self):
        """3. Manual review approval with missing DOJ updates response and attempt score,
        but keeps TopicProgress IN_PROGRESS without completion or deadline."""
        learner_no_doj = User.objects.create_user(
            username='no_doj_manual_learner',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=None
        )
        sub = Subject.objects.create(name='Stage3B Manual Rev Subj', slug='stage3b-manrev-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Stage3B ManRev Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Stage3B ManRev Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q_sc = Question.objects.create(topic=topic, text='Q_SC', question_type=Question.TYPE_SINGLE_CHOICE, order=1, is_active=True)
        c_good = Choice.objects.create(question=q_sc, text='A', is_correct=True)
        q_para = Question.objects.create(topic=topic, text='Q_Para', question_type=Question.TYPE_PARAGRAPH, order=2, is_active=True)

        attempt, tp = submit_quiz_attempt(learner_no_doj, topic, {q_sc.id: c_good.id, q_para.id: 'My text response'})
        para_resp = attempt.responses.get(question=q_para)
        self.assertIsNone(para_resp.is_correct)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)

        # Admin approves the pending paragraph response
        para_resp.is_correct = True
        para_resp.save()
        recalculate_attempt_score(attempt)

        attempt.refresh_from_db()
        self.assertTrue(attempt.passed)
        self.assertEqual(attempt.score, 100)

        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)
        self.assertIsNone(tp.frozen_deadline)

    def test_manual_review_approval_with_valid_doj_preserves_submission_timestamp_and_snapshot_deadline(self):
        """4. Manual review approval with valid DOJ preserves original attempt submission
        timestamp as completed_at and uses saved expected_end_day_snapshot for frozen deadline,
        even if phase schedule was modified prior to review."""
        sub = Subject.objects.create(name='Stage3B Snap Rev Subj', slug='stage3b-snaprev-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Stage3B Snap Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Stage3B Snap Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q_sc = Question.objects.create(topic=topic, text='Q_SC', question_type=Question.TYPE_SINGLE_CHOICE, order=1, is_active=True)
        c_good = Choice.objects.create(question=q_sc, text='A', is_correct=True)
        q_para = Question.objects.create(topic=topic, text='Q_Para', question_type=Question.TYPE_PARAGRAPH, order=2, is_active=True)

        submission_time = datetime(2026, 1, 12, 10, 0, tzinfo=dt_timezone.utc)
        attempt, tp = submit_quiz_attempt(self.learner, topic, {q_sc.id: c_good.id, q_para.id: 'My text response'})
        # Fix created_at to an explicit earlier time
        QuizAttempt.objects.filter(id=attempt.id).update(created_at=submission_time)
        attempt.refresh_from_db()
        self.assertEqual(attempt.created_at, submission_time)

        # Snapshot is 10
        self.assertEqual(tp.expected_end_day_snapshot, 10)

        # Admin shifts phase end_day to 35 before review
        phase.end_day = 35
        phase.save()

        # Admin approves response days later
        para_resp = attempt.responses.get(question=q_para)
        para_resp.is_correct = True
        para_resp.save()
        recalculate_attempt_score(attempt)

        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        # completed_at must be the original submission timestamp, not review time
        self.assertEqual(tp.completed_at, submission_time)
        # frozen_deadline must use snapshot (day 10 -> 2026-01-19), not phase.end_day 35
        self.assertEqual(tp.frozen_deadline, date(2026, 1, 19))

    def test_non_assessed_topic_completion_blocked_when_doj_is_missing(self):
        """5. Calling complete_non_assessed_topic on a learner without DOJ leaves topic
        in IN_PROGRESS without setting completed_at or frozen_deadline."""
        learner_no_doj = User.objects.create_user(
            username='no_doj_non_assessed',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=None
        )
        sub = Subject.objects.create(name='Stage3B NonAss Subj', slug='stage3b-nonass-sub')
        # Case A: Unassigned non-assessed topic
        topic_unassigned = Topic.objects.create(
            subject=sub,
            learning_phase=None,
            name='Stage3B NonAss Unassigned',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )
        tp_unassigned = complete_non_assessed_topic(learner_no_doj, topic_unassigned)
        self.assertIsNotNone(tp_unassigned)
        self.assertEqual(tp_unassigned.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp_unassigned.completed_at)
        self.assertIsNone(tp_unassigned.frozen_deadline)

        # Case B: Phased non-assessed topic with existing IN_PROGRESS state
        phase = LearningPhase.objects.create(subject=sub, name='Stage3B NonAss Phase', start_day=1, end_day=10)
        topic_phased = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Stage3B NonAss Phased',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )
        TopicProgress.objects.create(
            user=learner_no_doj,
            topic=topic_phased,
            status=TopicProgress.STATUS_IN_PROGRESS
        )
        tp_phased = complete_non_assessed_topic(learner_no_doj, topic_phased)
        self.assertIsNotNone(tp_phased)
        self.assertEqual(tp_phased.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp_phased.completed_at)
        self.assertIsNone(tp_phased.frozen_deadline)

    def test_non_assessed_topic_completes_when_doj_is_valid(self):
        """6. Calling complete_non_assessed_topic on a learner with valid DOJ marks
        topic COMPLETED with completed_at and frozen_deadline."""
        sub = Subject.objects.create(name='Stage3B Valid NonAss Subj', slug='stage3b-valnonass-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Stage3B Valid NonAss Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Stage3B Valid NonAss Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )

        tp = complete_non_assessed_topic(self.learner, topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)
        self.assertEqual(tp.frozen_deadline, date(2026, 1, 19))

    def test_already_completed_assessed_topic_retains_completed_at_and_frozen_deadline_if_doj_cleared(self):
        """7. An already-completed assessed topic retains completed status, completed_at,
        and frozen_deadline even if learner DOJ is subsequently cleared."""
        sub = Subject.objects.create(name='Stage3B Immut Ass Subj', slug='stage3b-immutass-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Stage3B Immut Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Stage3B Immut Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q = Question.objects.create(topic=topic, text='Q', question_type=Question.TYPE_SINGLE_CHOICE, order=1, is_active=True)
        c_good = Choice.objects.create(question=q, text='A', is_correct=True)

        learner = User.objects.create_user(
            username='immut_ass_learner',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=date(2026, 1, 10)
        )
        attempt, tp = submit_quiz_attempt(learner, topic, {q.id: c_good.id})
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        saved_completed_at = tp.completed_at
        saved_frozen_deadline = tp.frozen_deadline
        self.assertIsNotNone(saved_completed_at)
        self.assertIsNotNone(saved_frozen_deadline)

        # Learner DOJ is cleared
        learner.date_of_joining = None
        learner.save()

        # Routine reconciliation
        tp_after = reconcile_topic_progress(learner, topic)
        self.assertEqual(tp_after.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp_after.completed_at, saved_completed_at)
        self.assertEqual(tp_after.frozen_deadline, saved_frozen_deadline)

    def test_already_completed_non_assessed_topic_retains_completion_if_doj_cleared(self):
        """8. An already-completed non-assessed topic retains completion status, completed_at,
        and frozen_deadline if learner DOJ is cleared."""
        sub = Subject.objects.create(name='Stage3B Immut NonAss Subj', slug='stage3b-immutnonass-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Stage3B Immut NonAss Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Stage3B Immut NonAss Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )

        learner = User.objects.create_user(
            username='immut_nonass_learner',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=date(2026, 1, 10)
        )
        tp = complete_non_assessed_topic(learner, topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        saved_completed_at = tp.completed_at
        saved_frozen_deadline = tp.frozen_deadline

        # Clear DOJ
        learner.date_of_joining = None
        learner.save()

        # Re-invoking complete_non_assessed_topic preserves existing completion
        tp_after = complete_non_assessed_topic(learner, topic)
        self.assertEqual(tp_after.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp_after.completed_at, saved_completed_at)
        self.assertEqual(tp_after.frozen_deadline, saved_frozen_deadline)

    def test_failed_retake_on_completed_topic_preserves_completion_and_deadline(self):
        """9. A failed retake on an already-completed topic preserves STATUS_COMPLETED,
        completed_at, and frozen_deadline."""
        sub = Subject.objects.create(name='Stage3B Retake Subj', slug='stage3b-retake-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Stage3B Retake Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Stage3B Retake Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q = Question.objects.create(topic=topic, text='Q', question_type=Question.TYPE_SINGLE_CHOICE, order=1, is_active=True)
        c_good = Choice.objects.create(question=q, text='A', is_correct=True)
        c_bad = Choice.objects.create(question=q, text='B', is_correct=False)

        attempt1, tp1 = submit_quiz_attempt(self.learner, topic, {q.id: c_good.id})
        self.assertEqual(tp1.status, TopicProgress.STATUS_COMPLETED)
        initial_completed_at = tp1.completed_at
        initial_frozen_deadline = tp1.frozen_deadline

        # Learner retakes and fails
        attempt2, tp2 = submit_quiz_attempt(self.learner, topic, {q.id: c_bad.id})
        self.assertFalse(attempt2.passed)
        tp2.refresh_from_db()
        self.assertEqual(tp2.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp2.completed_at, initial_completed_at)
        self.assertEqual(tp2.frozen_deadline, initial_frozen_deadline)

    def test_unassigned_topic_evaluates_as_no_phase_not_timing_failure(self):
        """10. An unassigned topic (learning_phase=None) evaluates as NO_PHASE,
        not as a timing failure or timing unavailable."""
        sub = Subject.objects.create(name='Stage3B Unphased Subj', slug='stage3b-unphased-sub')
        topic_unphased = Topic.objects.create(
            subject=sub,
            learning_phase=None,
            name='Stage3B Unphased Topic',
            order=88,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )
        tp = complete_non_assessed_topic(self.learner, topic_unphased)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNone(tp.frozen_deadline)

        res = calculate_topic_performance(topic_unphased, tp, self.learner.date_of_joining)
        self.assertEqual(res['status'], PhasePerformanceStatus.NO_PHASE)
        self.assertEqual(res['status_label'], 'Completed (No Phase)')
        self.assertFalse(res['is_timing_verified'])
        self.assertFalse(res['is_eligible_for_on_time_rate'])

    def test_calculate_topic_performance_for_in_progress_missing_doj_returns_no_doj(self):
        """11. An in-progress topic for a learner with missing DOJ evaluates as NO_DOJ."""
        learner_no_doj = User.objects.create_user(
            username='eval_no_doj_learner',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=None
        )
        tp = TopicProgress.objects.create(
            user=learner_no_doj,
            topic=self.topic1,
            status=TopicProgress.STATUS_IN_PROGRESS
        )
        res = calculate_topic_performance(self.topic1, tp, learner_no_doj.date_of_joining)
        self.assertEqual(res['status'], PhasePerformanceStatus.NO_DOJ)
        self.assertEqual(res['status_label'], 'DOJ Not Set')

    def test_multi_attempt_earliest_passing_selected_when_doj_resolved(self):
        """12. When multiple attempts exist while DOJ was missing, resolving DOJ
        selects the earliest passing attempt timestamp as completed_at."""
        learner = User.objects.create_user(
            username='multi_att_doj_learner',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=None
        )
        sub = Subject.objects.create(name='Stage3B Multi Subj', slug='stage3b-multi-sub')
        phase = LearningPhase.objects.create(subject=sub, name='Stage3B Multi Phase', start_day=1, end_day=10)
        topic = Topic.objects.create(
            subject=sub,
            learning_phase=phase,
            name='Stage3B Multi Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        q = Question.objects.create(topic=topic, text='Q', question_type=Question.TYPE_SINGLE_CHOICE, order=1, is_active=True)
        c_good = Choice.objects.create(question=q, text='A', is_correct=True)
        c_bad = Choice.objects.create(question=q, text='B', is_correct=False)

        # Attempt 1: Fail at 10:00
        t1 = datetime(2026, 1, 12, 10, 0, tzinfo=dt_timezone.utc)
        att1, _ = submit_quiz_attempt(learner, topic, {q.id: c_bad.id})
        QuizAttempt.objects.filter(id=att1.id).update(created_at=t1)

        # Attempt 2: Pass at 11:00 (earliest pass)
        t2 = datetime(2026, 1, 12, 11, 0, tzinfo=dt_timezone.utc)
        att2, _ = submit_quiz_attempt(learner, topic, {q.id: c_good.id})
        QuizAttempt.objects.filter(id=att2.id).update(created_at=t2)

        # Attempt 3: Pass again at 12:00
        t3 = datetime(2026, 1, 12, 12, 0, tzinfo=dt_timezone.utc)
        att3, tp = submit_quiz_attempt(learner, topic, {q.id: c_good.id})
        QuizAttempt.objects.filter(id=att3.id).update(created_at=t3)

        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)

        # Learner DOJ is assigned
        learner.date_of_joining = date(2026, 1, 10)
        learner.save()

        tp_reconciled = reconcile_topic_progress(learner, topic)
        self.assertEqual(tp_reconciled.status, TopicProgress.STATUS_COMPLETED)
        # completed_at must be t2 (earliest pass), NOT t3
        self.assertEqual(tp_reconciled.completed_at, t2)
        self.assertEqual(tp_reconciled.frozen_deadline, date(2026, 1, 19))

    def test_calculate_reconciled_fields_pure_unit_logic(self):
        """13. Direct unit test of calculate_reconciled_fields verifies missing DOJ guard,
        normal completion, and immutability under all status transitions."""
        pass_dt = datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc)
        attempts_data = [
            {'score': 100, 'passed': True, 'created_at': pass_dt, 'is_fully_graded': True}
        ]

        # A: Passing attempts but date_of_joining is None -> blocked, IN_PROGRESS
        res_no_doj = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=attempts_data,
            current_status=TopicProgress.STATUS_NOT_STARTED,
            current_completed_at=None,
            current_frozen_deadline=None,
            date_of_joining=None,
            phase_end_day=10
        )
        self.assertEqual(res_no_doj['status'], TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(res_no_doj['completed_at'])
        self.assertIsNone(res_no_doj['frozen_deadline'])

        # B: Passing attempts with date_of_joining -> COMPLETED with nominal deadline
        res_with_doj = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=attempts_data,
            current_status=TopicProgress.STATUS_NOT_STARTED,
            current_completed_at=None,
            current_frozen_deadline=None,
            date_of_joining=date(2026, 1, 10),
            phase_end_day=10
        )
        self.assertEqual(res_with_doj['status'], TopicProgress.STATUS_COMPLETED)
        self.assertEqual(res_with_doj['completed_at'], pass_dt)
        self.assertEqual(res_with_doj['frozen_deadline'], date(2026, 1, 19))

        # C: Already COMPLETED topic with existing frozen_deadline -> preserved even if DOJ is None
        existing_frozen = date(2026, 1, 19)
        res_immut = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=attempts_data,
            current_status=TopicProgress.STATUS_COMPLETED,
            current_completed_at=pass_dt,
            current_frozen_deadline=existing_frozen,
            date_of_joining=None,
            phase_end_day=10
        )
        self.assertEqual(res_immut['status'], TopicProgress.STATUS_COMPLETED)
        self.assertEqual(res_immut['completed_at'], pass_dt)
        self.assertEqual(res_immut['frozen_deadline'], existing_frozen)




