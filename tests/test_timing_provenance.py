from datetime import date, datetime, timezone as dt_timezone
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.management import call_command

from content.models import Subject, LearningPhase, Topic, SiteConfig
from quizzes.models import Question, Choice, QuizAttempt, QuizResponse
from progress.models import TopicProgress
from progress.services import (
    calculate_nominal_deadline,
    calculate_reconciled_fields,
    reconcile_topic_progress,
    submit_quiz_attempt,
    complete_non_assessed_topic,
    record_topic_view,
    recalculate_attempt_score,
)

User = get_user_model()


class TimingProvenanceModelAndUnitTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.default_required_question_count = 1
        self.site_config.save()

        self.doj = date(2026, 1, 10)
        self.learner = User.objects.create_user(
            username='provenance_learner_1',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.doj
        )

        self.subject = Subject.objects.create(name='Provenance Subject', slug='prov-sub', order=1, is_active=True)
        self.phase = LearningPhase.objects.create(subject=self.subject, name='Phase 1', start_day=1, end_day=15)

    def test_model_choices_and_null_default(self):
        """Model definition has explicit choices and defaults timing_provenance to NULL."""
        self.assertEqual(TopicProgress.PROVENANCE_SYSTEM_CAPTURED, 'system_captured')
        self.assertEqual(TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED, 'retrospectively_reconstructed')

        field = TopicProgress._meta.get_field('timing_provenance')
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertEqual(field.max_length, 32)
        choice_keys = [c[0] for c in field.choices]
        self.assertIn('system_captured', choice_keys)
        self.assertIn('retrospectively_reconstructed', choice_keys)

        topic = Topic.objects.create(
            subject=self.subject,
            name='T1',
            learning_phase=self.phase,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )
        tp = TopicProgress.objects.create(user=self.learner, topic=topic)
        self.assertIsNone(tp.timing_provenance)

    def test_calculate_reconciled_fields_genuine_first_completion(self):
        """Genuine first completion with DOJ and valid deadline sets system_captured."""
        pass_dt = datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc)
        attempts_data = [
            {'score': 100, 'passed': True, 'created_at': pass_dt, 'is_fully_graded': True}
        ]

        res = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=attempts_data,
            current_status=TopicProgress.STATUS_NOT_STARTED,
            current_completed_at=None,
            current_frozen_deadline=None,
            date_of_joining=self.doj,
            phase_end_day=15,
            expected_end_day_snapshot=15,
            current_timing_provenance=None,
        )

        self.assertEqual(res['status'], TopicProgress.STATUS_COMPLETED)
        self.assertEqual(res['completed_at'], pass_dt)
        self.assertEqual(res['frozen_deadline'], date(2026, 1, 24))
        self.assertEqual(res['timing_provenance'], TopicProgress.PROVENANCE_SYSTEM_CAPTURED)

    def test_calculate_reconciled_fields_missing_doj_blocks_provenance(self):
        """Missing DOJ leaves record in_progress with null deadline and null provenance."""
        pass_dt = datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc)
        attempts_data = [
            {'score': 100, 'passed': True, 'created_at': pass_dt, 'is_fully_graded': True}
        ]

        res = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=attempts_data,
            current_status=TopicProgress.STATUS_NOT_STARTED,
            current_completed_at=None,
            current_frozen_deadline=None,
            date_of_joining=None,
            phase_end_day=15,
            expected_end_day_snapshot=15,
            current_timing_provenance=None,
        )

        self.assertEqual(res['status'], TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(res['frozen_deadline'])
        self.assertIsNone(res['timing_provenance'])

    def test_calculate_reconciled_fields_missing_phase_schedule_leaves_provenance_null(self):
        """Valid DOJ but no phase end day snapshot sets completed but null deadline and null provenance."""
        pass_dt = datetime(2026, 1, 15, 10, 0, tzinfo=dt_timezone.utc)
        attempts_data = [
            {'score': 100, 'passed': True, 'created_at': pass_dt, 'is_fully_graded': True}
        ]

        res = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=attempts_data,
            current_status=TopicProgress.STATUS_NOT_STARTED,
            current_completed_at=None,
            current_frozen_deadline=None,
            date_of_joining=self.doj,
            phase_end_day=None,
            expected_end_day_snapshot=None,
            current_timing_provenance=None,
        )

        self.assertEqual(res['status'], TopicProgress.STATUS_COMPLETED)
        self.assertIsNone(res['frozen_deadline'])
        self.assertIsNone(res['timing_provenance'])

    def test_calculate_reconciled_fields_retake_preserves_system_captured(self):
        """Retakes preserve system_captured provenance and existing frozen deadline."""
        t1 = datetime(2026, 1, 12, 10, 0, tzinfo=dt_timezone.utc)
        t2 = datetime(2026, 1, 18, 10, 0, tzinfo=dt_timezone.utc)
        attempts_data = [
            {'score': 80, 'passed': True, 'created_at': t1, 'is_fully_graded': True},
            {'score': 90, 'passed': True, 'created_at': t2, 'is_fully_graded': True},
        ]
        frozen_dl = date(2026, 1, 24)

        res = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=attempts_data,
            current_status=TopicProgress.STATUS_COMPLETED,
            current_completed_at=t1,
            current_frozen_deadline=frozen_dl,
            date_of_joining=self.doj,
            phase_end_day=20,  # Schedule changed to 20
            expected_end_day_snapshot=15,
            current_timing_provenance=TopicProgress.PROVENANCE_SYSTEM_CAPTURED,
        )

        self.assertEqual(res['status'], TopicProgress.STATUS_COMPLETED)
        self.assertEqual(res['completed_at'], t1)
        self.assertEqual(res['frozen_deadline'], frozen_dl)
        self.assertEqual(res['timing_provenance'], TopicProgress.PROVENANCE_SYSTEM_CAPTURED)

    def test_calculate_reconciled_fields_retake_preserves_retrospectively_reconstructed(self):
        """Retakes preserve retrospectively_reconstructed provenance and never overwrite it."""
        t1 = datetime(2026, 1, 12, 10, 0, tzinfo=dt_timezone.utc)
        t2 = datetime(2026, 1, 18, 10, 0, tzinfo=dt_timezone.utc)
        attempts_data = [
            {'score': 80, 'passed': True, 'created_at': t1, 'is_fully_graded': True},
            {'score': 95, 'passed': True, 'created_at': t2, 'is_fully_graded': True},
        ]
        reconstructed_dl = date(2026, 1, 24)

        res = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=attempts_data,
            current_status=TopicProgress.STATUS_COMPLETED,
            current_completed_at=t1,
            current_frozen_deadline=reconstructed_dl,
            date_of_joining=self.doj,
            phase_end_day=15,
            expected_end_day_snapshot=15,
            current_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED,
        )

        self.assertEqual(res['status'], TopicProgress.STATUS_COMPLETED)
        self.assertEqual(res['frozen_deadline'], reconstructed_dl)
        self.assertEqual(res['timing_provenance'], TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED)

    def test_calculate_reconciled_fields_existing_null_provenance_not_silently_relabelled(self):
        """Existing completed records with NULL provenance remain NULL across reconciliations."""
        t1 = datetime(2026, 1, 12, 10, 0, tzinfo=dt_timezone.utc)
        attempts_data = [
            {'score': 80, 'passed': True, 'created_at': t1, 'is_fully_graded': True},
        ]

        res = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=attempts_data,
            current_status=TopicProgress.STATUS_COMPLETED,
            current_completed_at=t1,
            current_frozen_deadline=None,
            date_of_joining=self.doj,
            phase_end_day=15,
            expected_end_day_snapshot=None,
            current_timing_provenance=None,
        )

        self.assertEqual(res['status'], TopicProgress.STATUS_COMPLETED)
        self.assertIsNone(res['frozen_deadline'])
        self.assertIsNone(res['timing_provenance'])

    def test_calculate_reconciled_fields_failed_retake_preserves_provenance_under_status_trust(self):
        """Failed retake (Option 2B status trust) preserves existing completion and provenance."""
        t1 = datetime(2026, 1, 12, 10, 0, tzinfo=dt_timezone.utc)
        attempts_data = [
            {'score': 40, 'passed': False, 'created_at': t1, 'is_fully_graded': True},
        ]
        frozen_dl = date(2026, 1, 24)

        res = calculate_reconciled_fields(
            topic_assessment_required=True,
            attempts_data=attempts_data,
            current_status=TopicProgress.STATUS_COMPLETED,
            current_completed_at=t1,
            current_frozen_deadline=frozen_dl,
            date_of_joining=self.doj,
            phase_end_day=15,
            expected_end_day_snapshot=15,
            current_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED,
        )

        self.assertEqual(res['status'], TopicProgress.STATUS_COMPLETED)
        self.assertEqual(res['frozen_deadline'], frozen_dl)
        self.assertEqual(res['timing_provenance'], TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED)


class TimingProvenancePathwaysTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.default_required_question_count = 1
        self.site_config.save()

        self.doj = date(2026, 1, 10)
        self.learner = User.objects.create_user(
            username='pathways_learner',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.doj
        )

        self.subject = Subject.objects.create(name='Pathways Subject', slug='path-sub', order=1, is_active=True)
        self.phase = LearningPhase.objects.create(subject=self.subject, name='Phase 1', start_day=1, end_day=15)

        self.topic_assessed = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase,
            name='Assessed Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )
        self.q1 = Question.objects.create(
            topic=self.topic_assessed,
            text='Q1',
            question_type=Question.TYPE_SINGLE_CHOICE,
            is_active=True,
            order=1
        )
        self.c1_correct = Choice.objects.create(question=self.q1, text='Correct', is_correct=True)
        self.c1_wrong = Choice.objects.create(question=self.q1, text='Wrong', is_correct=False)

        # Non-assessed subject & topic (order=1 in its own subject)
        self.subject_non_assessed = Subject.objects.create(name='Non-Assessed Subject', slug='non-ass-sub', is_active=True)
        self.phase_non_assessed = LearningPhase.objects.create(subject=self.subject_non_assessed, name='NA Phase 1', start_day=1, end_day=15)
        self.topic_non_assessed = Topic.objects.create(
            subject=self.subject_non_assessed,
            learning_phase=self.phase_non_assessed,
            name='Non-assessed Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )

    # --------------------------------------------------------------------------
    # Pathway 1: Assessed quiz submission
    # --------------------------------------------------------------------------
    def test_pathway_1_quiz_submission_first_completion_and_retake(self):
        # 1. First completion: score 100% -> completed, system_captured
        attempt, tp = submit_quiz_attempt(
            user=self.learner,
            topic=self.topic_assessed,
            submitted_answers={str(self.q1.id): [str(self.c1_correct.id)]}
        )
        self.assertTrue(attempt.passed)

        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.frozen_deadline, date(2026, 1, 24))
        self.assertEqual(tp.timing_provenance, TopicProgress.PROVENANCE_SYSTEM_CAPTURED)

        # 2. Retake: score 0% (failed retake) -> remains completed, preserves system_captured
        attempt2, tp2 = submit_quiz_attempt(
            user=self.learner,
            topic=self.topic_assessed,
            submitted_answers={str(self.q1.id): [str(self.c1_wrong.id)]}
        )
        self.assertFalse(attempt2.passed)

        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.frozen_deadline, date(2026, 1, 24))
        self.assertEqual(tp.timing_provenance, TopicProgress.PROVENANCE_SYSTEM_CAPTURED)

    # --------------------------------------------------------------------------
    # Pathway 2: Non-assessed topic completion
    # --------------------------------------------------------------------------
    def test_pathway_2_complete_non_assessed_topic(self):
        tp = complete_non_assessed_topic(self.learner, self.topic_non_assessed)
        self.assertIsNotNone(tp)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.frozen_deadline, date(2026, 1, 24))
        self.assertEqual(tp.timing_provenance, TopicProgress.PROVENANCE_SYSTEM_CAPTURED)

        # Repeated completion call preserves provenance
        tp2 = complete_non_assessed_topic(self.learner, self.topic_non_assessed)
        self.assertEqual(tp2.timing_provenance, TopicProgress.PROVENANCE_SYSTEM_CAPTURED)

    def test_pathway_2_complete_non_assessed_topic_missing_doj_blocks_provenance(self):
        learner_no_doj = User.objects.create_user(
            username='no_doj_learner',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=None
        )
        sub_no_doj = Subject.objects.create(name='No DOJ Subj', slug='no-doj-subj', is_active=True)
        topic_unassigned = Topic.objects.create(
            subject=sub_no_doj,
            learning_phase=None,
            name='No DOJ Topic',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=False
        )
        tp = complete_non_assessed_topic(learner_no_doj, topic_unassigned)
        self.assertIsNotNone(tp)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.frozen_deadline)
        self.assertIsNone(tp.timing_provenance)

    # --------------------------------------------------------------------------
    # Pathway 3: Topic view completion
    # --------------------------------------------------------------------------
    def test_pathway_3_record_topic_view_non_assessed(self):
        tp = record_topic_view(self.learner, self.topic_non_assessed)
        self.assertIsNotNone(tp)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.frozen_deadline, date(2026, 1, 24))
        self.assertEqual(tp.timing_provenance, TopicProgress.PROVENANCE_SYSTEM_CAPTURED)

        # Subsequent view preserves provenance
        tp_again = record_topic_view(self.learner, self.topic_non_assessed)
        self.assertEqual(tp_again.timing_provenance, TopicProgress.PROVENANCE_SYSTEM_CAPTURED)

    # --------------------------------------------------------------------------
    # Pathway 4: Manual review / score recalculation
    # --------------------------------------------------------------------------
    def test_pathway_4_manual_review_recalculation_provenance(self):
        # Create an attempt that initially has an ungraded paragraph question
        q_essay = Question.objects.create(
            topic=self.topic_assessed,
            text='Essay question',
            question_type=Question.TYPE_PARAGRAPH,
            is_active=True,
            order=2
        )
        attempt = QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic_assessed,
            attempt_number=1,
            correct_count=1,
            score=0,
            passed=False,
            total_questions=2,
            passing_score_used=75
        )
        # Correct response on q1
        QuizResponse.objects.create(
            attempt=attempt,
            question=self.q1,
            is_correct=True
        )
        # Ungraded response on q_essay (is_correct=None)
        resp_essay = QuizResponse.objects.create(
            attempt=attempt,
            question=q_essay,
            is_correct=None,
            text_response='Essay answer'
        )

        # Before manual review, attempt is not fully graded -> progress is in_progress
        reconcile_topic_progress(self.learner, self.topic_assessed)
        tp = TopicProgress.objects.get(user=self.learner, topic=self.topic_assessed)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.timing_provenance)

        # Admin approves essay response
        resp_essay.is_correct = True
        resp_essay.save()

        # recalculate_attempt_score triggers reconcile_topic_progress
        recalculate_attempt_score(attempt)
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(tp.frozen_deadline, date(2026, 1, 24))
        self.assertEqual(tp.timing_provenance, TopicProgress.PROVENANCE_SYSTEM_CAPTURED)

        # Now test that if a record is retrospectively_reconstructed, recalculate_attempt_score preserves it
        tp.timing_provenance = TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        tp.save(update_fields=['timing_provenance'])

        recalculate_attempt_score(attempt)
        tp.refresh_from_db()
        self.assertEqual(tp.timing_provenance, TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED)

    # --------------------------------------------------------------------------
    # Pathway 5: Repair command / reconciliation service
    # --------------------------------------------------------------------------
    def test_pathway_5_repair_command_preserves_provenance(self):
        # Setup an existing completed record with retrospectively_reconstructed provenance
        t1 = datetime(2026, 1, 12, 10, 0, tzinfo=dt_timezone.utc)
        QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic_assessed,
            attempt_number=1,
            correct_count=1,
            score=100,
            passed=True,
            created_at=t1,
            total_questions=1,
            passing_score_used=75
        )
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic_assessed,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=t1,
            frozen_deadline=date(2026, 1, 24),
            timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED,
            expected_end_day_snapshot=15,
            attempts_count=1,
            best_score=100,
            latest_score=100
        )

        # Run repair command (with --execute inside test runner)
        call_command('repair_topic_progress', execute=True)

        tp.refresh_from_db()
        self.assertEqual(tp.timing_provenance, TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED)
        self.assertEqual(tp.frozen_deadline, date(2026, 1, 24))
        self.assertEqual(tp.completed_at, t1)

    def test_pathway_5_repair_command_preserves_null_provenance(self):
        # Setup an existing completed record with NULL provenance
        t1 = datetime(2026, 1, 12, 10, 0, tzinfo=dt_timezone.utc)
        QuizAttempt.objects.create(
            user=self.learner,
            topic=self.topic_assessed,
            attempt_number=1,
            correct_count=1,
            score=100,
            passed=True,
            created_at=t1,
            total_questions=1,
            passing_score_used=75
        )
        tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic_assessed,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=t1,
            frozen_deadline=None,
            timing_provenance=None,
            expected_end_day_snapshot=None,
            attempts_count=1,
            best_score=100,
            latest_score=100
        )

        call_command('repair_topic_progress', execute=True)

        tp.refresh_from_db()
        self.assertIsNone(tp.timing_provenance)
        self.assertIsNone(tp.frozen_deadline)
