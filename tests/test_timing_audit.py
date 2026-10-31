import uuid
from datetime import date, datetime, timezone as dt_timezone
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.db import transaction, IntegrityError

from content.models import Subject, LearningPhase, Topic, SiteConfig
from progress.models import TopicProgress, TopicProgressTimingAudit
from progress.services import (
    apply_timing_reconstruction_with_audit,
    rollback_timing_reconstruction_with_audit,
    apply_timing_reconstruction_batch,
    reconcile_topic_progress,
    StaleRollbackError,
)

User = get_user_model()


class TopicProgressTimingAuditTests(TestCase):
    def setUp(self):
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.save()

        self.doj = date(2026, 1, 10)
        self.learner = User.objects.create_user(
            username='audit_learner_1',
            password='password123',
            role=User.ROLE_LEARNER,
            date_of_joining=self.doj
        )
        self.admin_user = User.objects.create_user(
            username='audit_admin_1',
            password='password123',
            role=User.ROLE_ADMIN
        )

        self.subject = Subject.objects.create(name='Audit Subject', slug='audit-sub', order=1, is_active=True)
        self.phase = LearningPhase.objects.create(subject=self.subject, name='Phase 1', start_day=1, end_day=15)
        self.topic = Topic.objects.create(
            subject=self.subject,
            learning_phase=self.phase,
            name='Audit Topic 1',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True
        )

        self.completion_time = datetime(2026, 1, 20, 10, 0, tzinfo=dt_timezone.utc)
        self.tp = TopicProgress.objects.create(
            user=self.learner,
            topic=self.topic,
            status=TopicProgress.STATUS_COMPLETED,
            completed_at=self.completion_time,
            frozen_deadline=None,
            timing_provenance=None,
            expected_end_day_snapshot=None,
            attempts_count=1,
            best_score=90,
            latest_score=90
        )

    # --------------------------------------------------------------------------
    # 1. Audit record creation with before-and-after values
    # --------------------------------------------------------------------------
    def test_audit_record_creation_with_before_and_after_values(self):
        new_deadline = date(2026, 1, 24)
        audit_entry, changed = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=new_deadline,
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED,
            reason="Historical timing reconstruction for Phase 1",
            actor=self.admin_user
        )

        self.assertTrue(changed)
        self.assertIsNotNone(audit_entry)

        # TopicProgress updated in database
        self.tp.refresh_from_db()
        self.assertEqual(self.tp.frozen_deadline, new_deadline)
        self.assertEqual(self.tp.expected_end_day_snapshot, 15)
        self.assertEqual(self.tp.timing_provenance, TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED)

        # Audit values match actual before and after states
        self.assertIsNone(audit_entry.before_frozen_deadline)
        self.assertEqual(audit_entry.after_frozen_deadline, new_deadline)
        self.assertIsNone(audit_entry.before_expected_end_day_snapshot)
        self.assertEqual(audit_entry.after_expected_end_day_snapshot, 15)
        self.assertIsNone(audit_entry.before_timing_provenance)
        self.assertEqual(audit_entry.after_timing_provenance, TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED)
        self.assertEqual(audit_entry.before_status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(audit_entry.after_status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(audit_entry.before_completed_at, self.completion_time)
        self.assertEqual(audit_entry.after_completed_at, self.completion_time)

    # --------------------------------------------------------------------------
    # 2. Correct association with TopicProgress and repair batch
    # --------------------------------------------------------------------------
    def test_association_with_topic_progress_and_repair_batch(self):
        custom_batch_id = uuid.uuid4()
        audit_entry, changed = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            repair_batch_id=custom_batch_id,
            reason="Batch test"
        )

        self.assertEqual(audit_entry.topic_progress, self.tp)
        self.assertEqual(audit_entry.original_topic_progress_id, self.tp.id)
        self.assertEqual(audit_entry.repair_batch_id, custom_batch_id)

    # --------------------------------------------------------------------------
    # 3. Preservation of actor and reason
    # --------------------------------------------------------------------------
    def test_preservation_of_actor_and_reason(self):
        reason_text = "Approved audit reconstruction under Oct 2026 executive policy"
        audit_entry, changed = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            actor=self.admin_user,
            reason=reason_text
        )

        self.assertEqual(audit_entry.actor, self.admin_user)
        self.assertEqual(audit_entry.reason, reason_text)

    # --------------------------------------------------------------------------
    # 4. No audit entry for unchanged records (Idempotency)
    # --------------------------------------------------------------------------
    def test_no_audit_entry_for_unchanged_records(self):
        # First execution applies change and creates 1 audit entry
        audit_1, changed_1 = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        self.assertTrue(changed_1)
        self.assertEqual(TopicProgressTimingAudit.objects.filter(original_topic_progress_id=self.tp.id).count(), 1)

        # Second execution with identical target values must NOT create an audit entry
        audit_2, changed_2 = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        self.assertFalse(changed_2)
        self.assertIsNone(audit_2)
        self.assertEqual(TopicProgressTimingAudit.objects.filter(original_topic_progress_id=self.tp.id).count(), 1)

    # --------------------------------------------------------------------------
    # 5. TopicProgress update and audit insertion rollback together on failure
    # --------------------------------------------------------------------------
    def test_topic_progress_and_audit_rollback_together_on_failure(self):
        # We simulate a failure inside the atomic transaction block
        initial_deadline = self.tp.frozen_deadline
        initial_provenance = self.tp.timing_provenance

        try:
            with transaction.atomic():
                # Perform reconstruction
                apply_timing_reconstruction_with_audit(
                    topic_progress=self.tp,
                    new_frozen_deadline=date(2026, 1, 24),
                    new_expected_end_day_snapshot=15
                )
                # Intentionally trigger an exception to rollback transaction
                raise RuntimeError("Simulated transaction failure")
        except RuntimeError:
            pass

        self.tp.refresh_from_db()
        self.assertEqual(self.tp.frozen_deadline, initial_deadline)
        self.assertEqual(self.tp.timing_provenance, initial_provenance)
        self.assertEqual(TopicProgressTimingAudit.objects.filter(original_topic_progress_id=self.tp.id).count(), 0)

    # --------------------------------------------------------------------------
    # 6. Multiple changed rows in one batch
    # --------------------------------------------------------------------------
    def test_multiple_changed_rows_in_one_batch(self):
        learner2 = User.objects.create_user(username='audit_learner_2', role=User.ROLE_LEARNER, date_of_joining=self.doj)
        learner3 = User.objects.create_user(username='audit_learner_3', role=User.ROLE_LEARNER, date_of_joining=self.doj)

        tp2 = TopicProgress.objects.create(
            user=learner2, topic=self.topic, status=TopicProgress.STATUS_COMPLETED,
            completed_at=self.completion_time
        )
        tp3 = TopicProgress.objects.create(
            user=learner3, topic=self.topic, status=TopicProgress.STATUS_COMPLETED,
            completed_at=self.completion_time
        )

        shared_batch_id = uuid.uuid4()

        _, c1 = apply_timing_reconstruction_with_audit(self.tp, date(2026, 1, 24), 15, repair_batch_id=shared_batch_id)
        _, c2 = apply_timing_reconstruction_with_audit(tp2, date(2026, 1, 24), 15, repair_batch_id=shared_batch_id)
        _, c3 = apply_timing_reconstruction_with_audit(tp3, date(2026, 1, 24), 15, repair_batch_id=shared_batch_id)

        self.assertTrue(c1 and c2 and c3)

        batch_audits = TopicProgressTimingAudit.objects.filter(repair_batch_id=shared_batch_id)
        self.assertEqual(batch_audits.count(), 3)
        affected_ids = set(batch_audits.values_list('original_topic_progress_id', flat=True))
        self.assertEqual(affected_ids, {self.tp.id, tp2.id, tp3.id})

    # --------------------------------------------------------------------------
    # 7. Separate rollback audit entries without modifying original audit entries
    # --------------------------------------------------------------------------
    def test_separate_rollback_audit_entries_without_modifying_original(self):
        # 1. Apply reconstruction
        orig_audit, _ = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED,
            reason="Initial reconstruction"
        )
        orig_id = orig_audit.id
        orig_action = orig_audit.action
        orig_created_at = orig_audit.created_at

        # 2. Rollback using helper
        rollback_batch = uuid.uuid4()
        rollback_audit = rollback_timing_reconstruction_with_audit(
            audit_entry=orig_audit,
            reason="Authorised rollback of Phase 1 timing adjustment",
            actor=self.admin_user,
            rollback_batch_id=rollback_batch
        )

        # Verify TopicProgress restored to before-values
        self.tp.refresh_from_db()
        self.assertIsNone(self.tp.frozen_deadline)
        self.assertIsNone(self.tp.expected_end_day_snapshot)
        self.assertIsNone(self.tp.timing_provenance)

        # Verify original audit record was NOT modified
        orig_audit.refresh_from_db()
        self.assertEqual(orig_audit.id, orig_id)
        self.assertEqual(orig_audit.action, orig_action)
        self.assertEqual(orig_audit.created_at, orig_created_at)

        # Verify rollback record is a separate append-only entry
        self.assertEqual(rollback_audit.action, TopicProgressTimingAudit.ACTION_ROLLBACK)
        self.assertEqual(rollback_audit.repair_batch_id, rollback_batch)
        self.assertEqual(rollback_audit.reverted_audit_entry, orig_audit)
        self.assertEqual(rollback_audit.before_frozen_deadline, date(2026, 1, 24))
        self.assertIsNone(rollback_audit.after_frozen_deadline)
        self.assertEqual(rollback_audit.before_timing_provenance, TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED)
        self.assertIsNone(rollback_audit.after_timing_provenance)

        # Total audit records in database is 2
        total_audits = TopicProgressTimingAudit.objects.filter(original_topic_progress_id=self.tp.id).count()
        self.assertEqual(total_audits, 2)

    # --------------------------------------------------------------------------
    # 8. Existing provenance remains unchanged unless explicit reconstruction
    # --------------------------------------------------------------------------
    def test_existing_provenance_unchanged_unless_explicit_reconstruction(self):
        # A completed record with NULL provenance
        reconcile_topic_progress(self.learner, self.topic)
        self.tp.refresh_from_db()
        self.assertIsNone(self.tp.timing_provenance)
        self.assertEqual(TopicProgressTimingAudit.objects.count(), 0)

        # Even after re-reconciling, provenance is untouched and no audit records exist
        reconcile_topic_progress(self.learner, self.topic)
        self.tp.refresh_from_db()
        self.assertIsNone(self.tp.timing_provenance)
        self.assertEqual(TopicProgressTimingAudit.objects.count(), 0)

        # Only explicit reconstruction triggers audit and updates provenance
        apply_timing_reconstruction_with_audit(
            self.tp,
            date(2026, 1, 24),
            15,
            TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        self.tp.refresh_from_db()
        self.assertEqual(self.tp.timing_provenance, TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED)
        self.assertEqual(TopicProgressTimingAudit.objects.count(), 1)

    # --------------------------------------------------------------------------
    # 9. Append-only model enforcement & preservation on foreign key deletion
    # --------------------------------------------------------------------------
    def test_append_only_save_and_delete_prevention(self):
        audit_entry, _ = apply_timing_reconstruction_with_audit(
            self.tp,
            date(2026, 1, 24),
            15
        )

        # Attempt to modify audit entry via .save() must raise PermissionError
        audit_entry.reason = "Modified reason"
        with self.assertRaises(PermissionError):
            audit_entry.save()

        # Attempt to delete audit entry via .delete() must raise PermissionError
        with self.assertRaises(PermissionError):
            audit_entry.delete()

    def test_preservation_when_topic_progress_deleted(self):
        tp_id = self.tp.id
        audit_entry, _ = apply_timing_reconstruction_with_audit(
            self.tp,
            date(2026, 1, 24),
            15
        )

        # Delete TopicProgress
        self.tp.delete()

        # Audit entry remains in database; topic_progress is set to NULL, original_topic_progress_id is preserved
        audit_entry.refresh_from_db()
        self.assertIsNone(audit_entry.topic_progress)
        self.assertEqual(audit_entry.original_topic_progress_id, tp_id)
        self.assertEqual(audit_entry.after_frozen_deadline, date(2026, 1, 24))

    # --------------------------------------------------------------------------
    # 10. Safe rollback validation: rejection on diverged fields
    # --------------------------------------------------------------------------
    def test_rollback_rejected_when_expected_end_day_snapshot_diverges(self):
        orig_audit, _ = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        self.tp.expected_end_day_snapshot = 20
        self.tp.save(update_fields=['expected_end_day_snapshot'])

        with self.assertRaises(StaleRollbackError) as ctx:
            rollback_timing_reconstruction_with_audit(orig_audit, reason="Rollback attempt")
        self.assertIn("expected_end_day_snapshot", str(ctx.exception))

    def test_rollback_rejected_when_frozen_deadline_diverges(self):
        orig_audit, _ = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        self.tp.frozen_deadline = date(2026, 1, 28)
        self.tp.save(update_fields=['frozen_deadline'])

        with self.assertRaises(StaleRollbackError) as ctx:
            rollback_timing_reconstruction_with_audit(orig_audit, reason="Rollback attempt")
        self.assertIn("frozen_deadline", str(ctx.exception))

    def test_rollback_rejected_when_timing_provenance_diverges(self):
        orig_audit, _ = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        self.tp.timing_provenance = TopicProgress.PROVENANCE_SYSTEM_CAPTURED
        self.tp.save(update_fields=['timing_provenance'])

        with self.assertRaises(StaleRollbackError) as ctx:
            rollback_timing_reconstruction_with_audit(orig_audit, reason="Rollback attempt")
        self.assertIn("timing_provenance", str(ctx.exception))

    def test_rollback_rejected_when_status_diverges(self):
        orig_audit, _ = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        self.tp.status = TopicProgress.STATUS_IN_PROGRESS
        self.tp.save(update_fields=['status'])

        with self.assertRaises(StaleRollbackError) as ctx:
            rollback_timing_reconstruction_with_audit(orig_audit, reason="Rollback attempt")
        self.assertIn("status", str(ctx.exception))

    def test_rollback_rejected_when_completed_at_diverges(self):
        orig_audit, _ = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        self.tp.completed_at = datetime(2026, 2, 1, 12, 0, tzinfo=dt_timezone.utc)
        self.tp.save(update_fields=['completed_at'])

        with self.assertRaises(StaleRollbackError) as ctx:
            rollback_timing_reconstruction_with_audit(orig_audit, reason="Rollback attempt")
        self.assertIn("completed_at", str(ctx.exception))

    # --------------------------------------------------------------------------
    # 11. Rejected rollback leaves state and audit table untouched
    # --------------------------------------------------------------------------
    def test_rejected_rollback_leaves_state_and_audit_untouched(self):
        orig_audit, _ = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        self.tp.frozen_deadline = date(2026, 1, 30)
        self.tp.save(update_fields=['frozen_deadline'])

        pre_audit_count = TopicProgressTimingAudit.objects.count()

        with self.assertRaises(StaleRollbackError):
            rollback_timing_reconstruction_with_audit(orig_audit, reason="Must fail")

        # Confirm TopicProgress was not reverted or changed
        self.tp.refresh_from_db()
        self.assertEqual(self.tp.frozen_deadline, date(2026, 1, 30))
        self.assertEqual(self.tp.expected_end_day_snapshot, 15)

        # Confirm no rollback audit entry was created
        post_audit_count = TopicProgressTimingAudit.objects.count()
        self.assertEqual(post_audit_count, pre_audit_count)

    # --------------------------------------------------------------------------
    # 12. Duplicate rollback prevention (application check and DB constraint)
    # --------------------------------------------------------------------------
    def test_cannot_rollback_same_audit_entry_twice_application_check(self):
        orig_audit, _ = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        # First rollback succeeds
        rollback_1 = rollback_timing_reconstruction_with_audit(orig_audit, reason="First rollback")
        self.assertIsNotNone(rollback_1)
        self.assertEqual(rollback_1.reverted_audit_entry, orig_audit)

        # Second rollback attempt must be rejected with StaleRollbackError
        with self.assertRaises(StaleRollbackError) as ctx:
            rollback_timing_reconstruction_with_audit(orig_audit, reason="Second rollback attempt")
        self.assertIn("already been rolled back", str(ctx.exception))

    def test_cannot_rollback_same_audit_entry_database_uniqueness(self):
        orig_audit, _ = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        # First rollback creates entry with reverted_audit_entry
        TopicProgressTimingAudit.objects.create(
            topic_progress=self.tp,
            reverted_audit_entry=orig_audit,
            action=TopicProgressTimingAudit.ACTION_ROLLBACK,
            reason="Direct rollback 1"
        )

        # A direct second creation bypassing application checks hits the OneToOneField unique constraint
        with self.assertRaises(IntegrityError):
            TopicProgressTimingAudit.objects.create(
                topic_progress=self.tp,
                reverted_audit_entry=orig_audit,
                action=TopicProgressTimingAudit.ACTION_ROLLBACK,
                reason="Direct rollback 2"
            )

    # --------------------------------------------------------------------------
    # 13. Rejection of rollback on a rollback entry
    # --------------------------------------------------------------------------
    def test_cannot_rollback_a_rollback_entry(self):
        orig_audit, _ = apply_timing_reconstruction_with_audit(
            topic_progress=self.tp,
            new_frozen_deadline=date(2026, 1, 24),
            new_expected_end_day_snapshot=15,
            new_timing_provenance=TopicProgress.PROVENANCE_RETROSPECTIVELY_RECONSTRUCTED
        )
        rollback_audit = rollback_timing_reconstruction_with_audit(orig_audit, reason="Valid rollback")

        with self.assertRaises(StaleRollbackError) as ctx:
            rollback_timing_reconstruction_with_audit(rollback_audit, reason="Rollback of rollback")
        self.assertIn("is a rollback entry and cannot be rolled back", str(ctx.exception))

    # --------------------------------------------------------------------------
    # 14. Batch atomicity: outer transaction rolls back all records on failure
    # --------------------------------------------------------------------------
    def test_batch_atomicity_rolls_back_all_records_on_failure(self):
        learners = [
            User.objects.create_user(username=f'batch_learner_{i}', role=User.ROLE_LEARNER, date_of_joining=self.doj)
            for i in range(1, 5)
        ]
        tps = [
            TopicProgress.objects.create(
                user=learners[i],
                topic=self.topic,
                status=TopicProgress.STATUS_COMPLETED,
                completed_at=self.completion_time
            )
            for i in range(4)
        ]

        batch_id = uuid.uuid4()
        plans = [
            {
                'topic_progress': tps[0],
                'new_frozen_deadline': date(2026, 1, 24),
                'new_expected_end_day_snapshot': 15,
            },
            {
                'topic_progress': tps[1],
                'new_frozen_deadline': date(2026, 1, 25),
                'new_expected_end_day_snapshot': 15,
            },
            {
                'topic_progress': tps[2],
                'new_frozen_deadline': date(2026, 1, 26),
                'new_expected_end_day_snapshot': 15,
            },
            {
                # 4th record fails (non-existent TopicProgress ID 999999)
                'topic_progress': 999999,
                'new_frozen_deadline': date(2026, 1, 27),
                'new_expected_end_day_snapshot': 15,
            },
        ]

        with self.assertRaises(TopicProgress.DoesNotExist):
            apply_timing_reconstruction_batch(plans, repair_batch_id=batch_id)

        # Confirm records 0, 1, and 2 were rolled back
        for tp in tps[:3]:
            tp.refresh_from_db()
            self.assertIsNone(tp.frozen_deadline)
            self.assertIsNone(tp.expected_end_day_snapshot)
            self.assertIsNone(tp.timing_provenance)

        # Confirm no audit entries exist for this batch ID
        self.assertEqual(TopicProgressTimingAudit.objects.filter(repair_batch_id=batch_id).count(), 0)
