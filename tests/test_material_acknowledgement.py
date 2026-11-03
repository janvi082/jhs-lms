import time
from datetime import timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from content.models import Subject, Topic, Video, Resource, ResourceAcknowledgement
from access.models import LearnerAccess
from progress.models import TopicProgress
from quizzes.models import Question, Choice, QuizAttempt
from progress.services import (
    record_topic_view,
    submit_quiz_attempt,
    reconcile_topic_progress,
    get_topic_access_state,
    TopicAccessState,
)

User = get_user_model()


class MaterialReviewAcknowledgementTests(TestCase):
    def setUp(self):
        self.learner1 = User.objects.create_user(
            username='learner1', password='password123', role=User.ROLE_LEARNER,
            date_of_joining=timezone.localdate()
        )
        self.learner2 = User.objects.create_user(
            username='learner2', password='password123', role=User.ROLE_LEARNER,
            date_of_joining=timezone.localdate()
        )
        self.admin = User.objects.create_superuser(
            username='admin_user', password='password123', role=User.ROLE_ADMIN
        )

        self.subject = Subject.objects.create(name='Accounting', slug='accounting', order=1, is_active=True)
        self.topic1 = Topic.objects.create(
            subject=self.subject,
            name='Introduction to Ledgers',
            order=1,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True,
            acknowledgement_required=True
        )
        for i in range(5):
            q = Question.objects.create(topic=self.topic1, text=f'Q{i}', order=i+1, is_active=True)
            Choice.objects.create(question=q, text='A', is_correct=True)
            Choice.objects.create(question=q, text='B', is_correct=False)
        self.topic2 = Topic.objects.create(
            subject=self.subject,
            name='Trial Balance',
            order=2,
            status=Topic.STATUS_PUBLISHED,
            is_active=True,
            assessment_required=True
        )

        self.res1 = Resource.objects.create(
            topic=self.topic1,
            title='Intro Slide Deck',
            resource_type='ppt',
            url='https://onedrive.example.com/slides.ppt',
            version=1,
            order=1
        )
        self.res2 = Resource.objects.create(
            topic=self.topic1,
            title='Ledger Worksheet',
            resource_type='excel',
            url='https://onedrive.example.com/sheet.xlsx',
            version=1,
            order=2
        )

        self.client1 = Client()
        self.client1.login(username='learner1', password='password123')

        self.client2 = Client()
        self.client2.login(username='learner2', password='password123')

    def test_1_existing_material_without_acknowledgement_displays_pending(self):
        """1. Existing material without an acknowledgement displays Pending."""
        resp = self.client1.get(reverse('topic_detail', args=[self.subject.slug, self.topic1.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Pending Review')
        self.assertContains(resp, 'Confirm Review')
        self.assertEqual(ResourceAcknowledgement.objects.filter(user=self.learner1).count(), 0)

    def test_2_learner_can_acknowledge_accessible_material(self):
        """2. A learner can acknowledge an accessible material."""
        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        resp = self.client1.post(ack_url, follow=True)
        self.assertEqual(resp.status_code, 200)

        ack = ResourceAcknowledgement.objects.filter(user=self.learner1, resource=self.res1).first()
        self.assertIsNotNone(ack)
        self.assertEqual(ack.acknowledged_version, self.res1.version)
        self.assertContains(resp, 'Acknowledged')
        self.assertContains(resp, 'Reviewed')

    def test_3_correct_authenticated_learner_is_recorded(self):
        """3. The correct authenticated learner is recorded."""
        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        self.client1.post(ack_url)

        ack = ResourceAcknowledgement.objects.get(resource=self.res1)
        self.assertEqual(ack.user, self.learner1)
        self.assertNotEqual(ack.user, self.learner2)

    def test_4_acknowledged_version_matches_current_resource_version(self):
        """4. The acknowledged version matches the current resource version."""
        self.res1.version = 3
        self.res1.save()

        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        self.client1.post(ack_url)

        ack = ResourceAcknowledgement.objects.get(user=self.learner1, resource=self.res1)
        self.assertEqual(ack.acknowledged_version, 3)

    def test_5_acknowledgement_timestamp_is_recorded(self):
        """5. The acknowledgement timestamp is recorded."""
        before = timezone.now()
        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        self.client1.post(ack_url)
        after = timezone.now()

        ack = ResourceAcknowledgement.objects.get(user=self.learner1, resource=self.res1)
        self.assertTrue(before <= ack.acknowledged_at <= after)

    def test_6_repeated_acknowledgement_does_not_create_duplicate_records(self):
        """6. Repeated acknowledgement of the same version does not create duplicate records."""
        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        self.client1.post(ack_url)
        self.client1.post(ack_url)
        self.client1.post(ack_url)

        self.assertEqual(
            ResourceAcknowledgement.objects.filter(user=self.learner1, resource=self.res1).count(),
            1
        )

    def test_7_repeated_acknowledgement_does_not_unnecessarily_change_timestamp(self):
        """7. Repeated acknowledgement of the same version does not rewrite the original timestamp."""
        orig_time = timezone.now() - timedelta(days=2)
        ack = ResourceAcknowledgement.objects.create(
            user=self.learner1,
            resource=self.res1,
            acknowledged_version=self.res1.version,
            acknowledged_at=orig_time
        )

        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        self.client1.post(ack_url)

        ack.refresh_from_db()
        self.assertEqual(ack.acknowledged_at, orig_time)

    def test_8_acknowledgement_for_older_version_displays_updated_review_required(self):
        """8. An acknowledgement for an older version displays Updated — Review Required."""
        ResourceAcknowledgement.objects.create(
            user=self.learner1,
            resource=self.res1,
            acknowledged_version=1,
            acknowledged_at=timezone.now()
        )

        # Bump resource version to 2
        self.res1.version = 2
        self.res1.save()

        resp = self.client1.get(reverse('topic_detail', args=[self.subject.slug, self.topic1.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Updated — Review Required')
        self.assertContains(resp, 'Acknowledge Latest')

    def test_9_learner_can_acknowledge_latest_version(self):
        """9. A learner can acknowledge the latest version."""
        orig_time = timezone.now() - timedelta(days=5)
        ack = ResourceAcknowledgement.objects.create(
            user=self.learner1,
            resource=self.res1,
            acknowledged_version=1,
            acknowledged_at=orig_time
        )
        self.res1.version = 2
        self.res1.save()

        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        resp = self.client1.post(ack_url, follow=True)
        self.assertEqual(resp.status_code, 200)

        ack.refresh_from_db()
        self.assertEqual(ack.acknowledged_version, 2)
        self.assertGreater(ack.acknowledged_at, orig_time)
        self.assertContains(resp, 'Acknowledged')

    def test_10_learner_cannot_acknowledge_inaccessible_or_locked_material(self):
        """10. A learner cannot acknowledge inaccessible or locked material."""
        # A. Denied via LearnerAccess on resource
        LearnerAccess.objects.create(learner=self.learner1, content_object=self.res2, is_allowed=False)
        ack_url = reverse('acknowledge_material', args=[self.res2.id])
        resp = self.client1.post(ack_url)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(ResourceAcknowledgement.objects.filter(user=self.learner1, resource=self.res2).exists())

        # B. Locked sequential topic (topic 2 locked because topic 1 not passed)
        res_topic2 = Resource.objects.create(
            topic=self.topic2,
            title='Balance Sheet Template',
            resource_type='excel',
            url='https://onedrive.example.com/bs.xlsx',
            version=1
        )
        ack_url_t2 = reverse('acknowledge_material', args=[res_topic2.id])
        resp2 = self.client1.post(ack_url_t2)
        self.assertEqual(resp2.status_code, 403)
        self.assertFalse(ResourceAcknowledgement.objects.filter(user=self.learner1, resource=res_topic2).exists())

    def test_11_learner_cannot_acknowledge_another_learner_material_via_params(self):
        """11. A learner cannot acknowledge another learner's material by manipulating submitted data."""
        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        # Submit fake user parameter in post payload
        resp = self.client1.post(ack_url, {'user': self.learner2.id, 'user_id': self.learner2.id})
        self.assertEqual(resp.status_code, 302)

        # Confirm learner2 was not acknowledged; only learner1 was recorded
        self.assertFalse(ResourceAcknowledgement.objects.filter(user=self.learner2).exists())
        self.assertTrue(ResourceAcknowledgement.objects.filter(user=self.learner1, resource=self.res1).exists())

    def test_12_quiz_access_remains_unchanged_regardless_of_acknowledgement(self):
        """12. Quiz access remains unchanged regardless of acknowledgement."""
        # In topic 1, resources are pending, yet quiz_modal is fully accessible
        quiz_url = reverse('quiz_modal', args=[self.subject.slug, self.topic1.id])
        resp = self.client1.get(quiz_url)
        self.assertEqual(resp.status_code, 200)

        # Now acknowledge all resources
        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        self.client1.post(ack_url)
        ack_url2 = reverse('acknowledge_material', args=[self.res2.id])
        self.client1.post(ack_url2)

        # Quiz modal still accessible identically
        resp2 = self.client1.get(quiz_url)
        self.assertEqual(resp2.status_code, 200)

    def test_13_topic_progress_and_completion_remain_unchanged(self):
        """13. TopicProgress and topic completion remain unchanged."""
        progress_before = TopicProgress.objects.filter(user=self.learner1, topic=self.topic1).first()
        is_completed_before = (progress_before.status == TopicProgress.STATUS_COMPLETED) if progress_before else False

        # Acknowledge resources
        self.client1.post(reverse('acknowledge_material', args=[self.res1.id]))
        self.client1.post(reverse('acknowledge_material', args=[self.res2.id]))

        progress_after = TopicProgress.objects.filter(user=self.learner1, topic=self.topic1).first()
        is_completed_after = (progress_after.status == TopicProgress.STATUS_COMPLETED) if progress_after else False

        self.assertEqual(is_completed_before, is_completed_after)
        self.assertFalse(is_completed_after)

    def test_14_existing_learner_topic_resource_functionality_still_works(self):
        """14. Existing learner topic/resource functionality still works."""
        resp = self.client1.get(reverse('topic_detail', args=[self.subject.slug, self.topic1.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.res1.title)
        self.assertContains(resp, self.res2.title)
        self.assertContains(resp, self.res1.url)
        self.assertContains(resp, 'Open Material')


    def test_15_concurrent_first_time_acknowledgement_race_handled_safely(self):
        """15. Simulate concurrent first-time acknowledgement where another process commits first."""
        from unittest.mock import patch
        from django.db import IntegrityError

        # Pre-seed record as if committed by a concurrent process that won the race
        ResourceAcknowledgement.objects.create(
            user=self.learner1,
            resource=self.res1,
            acknowledged_version=self.res1.version,
            acknowledged_at=timezone.now()
        )

        def mock_racing_create(*args, **kwargs):
            raise IntegrityError("UNIQUE constraint failed: content_resourceacknowledgement.user_id, content_resourceacknowledgement.resource_id")

        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        with patch.object(ResourceAcknowledgement.objects, 'create', side_effect=mock_racing_create):
            resp = self.client1.post(ack_url, follow=True)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ResourceAcknowledgement.objects.filter(user=self.learner1, resource=self.res1).count(), 1)
        self.assertContains(resp, "already up to date")


    def test_16_unrelated_integrity_error_is_re_raised_when_no_record_exists(self):
        """16. Verify that an unexpected IntegrityError without an existing acknowledgement is re-raised."""
        from unittest.mock import patch
        from django.db import IntegrityError

        def fail_create_unexpectedly(*args, **kwargs):
            raise IntegrityError("Unexpected foreign key or null constraint failure")

        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        with patch.object(ResourceAcknowledgement.objects, 'create', side_effect=fail_create_unexpectedly):
            with self.assertRaises(IntegrityError):
                self.client1.post(ack_url)

        self.assertEqual(ResourceAcknowledgement.objects.filter(user=self.learner1, resource=self.res1).count(), 0)


    def test_17_unrelated_integrity_error_is_re_raised_even_when_record_exists(self):
        """17. Verify that an unrelated IntegrityError is re-raised even if an acknowledgement row already exists."""
        from unittest.mock import patch
        from django.db import IntegrityError

        # Pre-seed record
        ResourceAcknowledgement.objects.create(
            user=self.learner1,
            resource=self.res1,
            acknowledged_version=self.res1.version,
            acknowledged_at=timezone.now()
        )

        def fail_with_unrelated_integrity_error(*args, **kwargs):
            raise IntegrityError("FOREIGN KEY constraint failed")

        ack_url = reverse('acknowledge_material', args=[self.res1.id])
        with patch.object(ResourceAcknowledgement.objects, 'create', side_effect=fail_with_unrelated_integrity_error):
            # Also patch select_for_update or filter if necessary; in this case create won't be called if ack exists,
            # so let's test where ack is None initially, row is created concurrently, but error is unrelated
            pass

        # Simulate: view gets ack is None initially, then create() raises unrelated error, row exists in DB
        orig_first = ResourceAcknowledgement.objects.select_for_update

        class MockQuerySet:
            def __init__(self, real_qs):
                self.real_qs = real_qs
                self.calls = 0

            def filter(self, *args, **kwargs):
                return self

            def first(self):
                self.calls += 1
                if self.calls == 1:
                    return None  # First call in view sees None
                return self.real_qs.first()  # Second call sees existing row

        mock_qs = MockQuerySet(ResourceAcknowledgement.objects.filter(user=self.learner1, resource=self.res1))

        with patch.object(ResourceAcknowledgement.objects, 'select_for_update', return_value=mock_qs):
            with patch.object(ResourceAcknowledgement.objects, 'create', side_effect=fail_with_unrelated_integrity_error):
                with self.assertRaises(IntegrityError):
                    self.client1.post(ack_url)


    def test_18_admin_portal_learner_detail_displays_pending_materials_summary(self):
        """18. Admin learner detail shows Pending Review when materials exist without acknowledgement."""
        admin_client = Client()
        admin_client.login(username='admin_user', password='password123')

        url = reverse('portal_learner_progress_detail', args=[self.learner1.id])
        resp = admin_client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Pending Review')
        self.assertContains(resp, '2/2 Pending Review')

    def test_19_admin_portal_learner_detail_displays_acknowledged_materials_summary(self):
        """19. Admin learner detail shows Acknowledged when materials are acknowledged at current version."""
        ResourceAcknowledgement.objects.create(
            user=self.learner1,
            resource=self.res1,
            acknowledged_version=self.res1.version,
            acknowledged_at=timezone.now()
        )
        ResourceAcknowledgement.objects.create(
            user=self.learner1,
            resource=self.res2,
            acknowledged_version=self.res2.version,
            acknowledged_at=timezone.now()
        )

        admin_client = Client()
        admin_client.login(username='admin_user', password='password123')

        url = reverse('portal_learner_progress_detail', args=[self.learner1.id])
        resp = admin_client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '2/2 Acknowledged')

    def test_20_admin_portal_learner_detail_displays_updated_review_required(self):
        """20. Admin learner detail shows Updated — Review Required when resource version is incremented."""
        ResourceAcknowledgement.objects.create(
            user=self.learner1,
            resource=self.res1,
            acknowledged_version=1,
            acknowledged_at=timezone.now()
        )
        self.res1.version = 2
        self.res1.save()

        admin_client = Client()
        admin_client.login(username='admin_user', password='password123')

        url = reverse('portal_learner_progress_detail', args=[self.learner1.id])
        resp = admin_client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Updated — Review Required')

    def test_21_admin_portal_learner_detail_handles_topic_with_no_materials(self):
        """21. Admin learner detail displays neutral dash when a topic has no materials."""
        # Topic 2 has no materials created initially
        admin_client = Client()
        admin_client.login(username='admin_user', password='password123')

        url = reverse('portal_learner_progress_detail', args=[self.learner1.id])
        resp = admin_client.get(url)
        self.assertEqual(resp.status_code, 200)
        # Should render neutral dash for Topic 2
        self.assertContains(resp, '—')

    def test_22_admin_portal_excludes_inaccessible_materials_from_learner_counts(self):
        """22. Inaccessible materials under LearnerAccess are excluded from learner's required review count."""
        LearnerAccess.objects.create(learner=self.learner1, content_object=self.res2, is_allowed=False)
        # Learner 1 acknowledges res1 (only accessible resource)
        ResourceAcknowledgement.objects.create(
            user=self.learner1,
            resource=self.res1,
            acknowledged_version=self.res1.version,
            acknowledged_at=timezone.now()
        )

        admin_client = Client()
        admin_client.login(username='admin_user', password='password123')

        url = reverse('portal_learner_progress_detail', args=[self.learner1.id])
        resp = admin_client.get(url)
        self.assertEqual(resp.status_code, 200)
        # Res2 is restricted, so total accessible materials is 1, and 1/1 is Acknowledged
        self.assertContains(resp, '1/1 Acknowledged')

    def test_23_unauthorized_user_cannot_access_portal_learner_detail(self):
        """23. Unauthorized learner is redirected and cannot access admin portal learner detail."""
        url = reverse('portal_learner_progress_detail', args=[self.learner1.id])
        resp = self.client1.get(url)
        # Non-admin user is redirected by @admin_required
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('learner_dashboard'), resp.url)

    def test_24_topic_acknowledgement_required_default_and_persistence(self):
        """24. Topic.acknowledgement_required defaults to False and can be updated."""
        topic = Topic.objects.create(
            subject=self.subject,
            name="New Default Topic",
            summary="Testing default",
            status="published"
        )
        self.assertFalse(topic.acknowledgement_required)
        self.assertTrue(topic.assessment_required)

        topic.acknowledgement_required = True
        topic.save()
        topic.refresh_from_db()
        self.assertTrue(topic.acknowledgement_required)

    def test_25_resource_is_required_default_and_persistence(self):
        """25. Resource.is_required defaults to True and can be set to False (optional)."""
        res = Resource.objects.create(
            topic=self.topic1,
            title="Optional Resource",
            resource_type="pdf",
            url="https://example.com/opt.pdf"
        )
        self.assertTrue(res.is_required)
        self.assertEqual(res.version, 1)

        res.is_required = False
        res.save()
        res.refresh_from_db()
        self.assertFalse(res.is_required)

    def test_26_portal_forms_topic_and_resource_handle_new_fields(self):
        """26. TopicForm, TopicAddForm, and ResourceForm correctly bind and save new fields."""
        from portal.forms import TopicForm, TopicAddForm, ResourceForm

        # TopicForm
        topic_form = TopicForm(data={
            'subject': self.subject.id,
            'name': 'Form Topic',
            'summary': 'Form Topic Summary',
            'status': 'published',
            'assessment_required': True,
            'acknowledgement_required': True,
        })
        self.assertTrue(topic_form.is_valid(), topic_form.errors)
        saved_topic = topic_form.save()
        self.assertTrue(saved_topic.acknowledgement_required)

        # TopicAddForm
        add_form = TopicAddForm(data={
            'subject': self.subject.id,
            'name': 'Added Topic',
            'summary': 'Added Topic Summary',
            'assessment_required': False,
            'acknowledgement_required': True,
        })
        self.assertTrue(add_form.is_valid(), add_form.errors)
        added_topic = add_form.save()
        self.assertTrue(added_topic.acknowledgement_required)
        self.assertFalse(added_topic.assessment_required)

        # ResourceForm
        res_form = ResourceForm(data={
            'title': 'Form Resource',
            'resource_type': 'pdf',
            'url': 'https://example.com/res.pdf',
            'description': 'Description',
            'is_required': False,
        })
        self.assertTrue(res_form.is_valid(), res_form.errors)
        saved_res = res_form.save(commit=False)
        saved_res.topic = self.topic1
        saved_res.save()
        self.assertFalse(saved_res.is_required)
    def test_27_scenario_1_assessment_required_alone_completes_on_passing_quiz(self):
        """T1: assessment_required=True, acknowledgement_required=False. Quiz passed completes topic; materials do not block."""
        sub = Subject.objects.create(name="Sub T1", slug="sub-t1", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="T1 Topic",
            order=1,
            status="published",
            assessment_required=True,
            acknowledgement_required=False
        )
        q = Question.objects.create(topic=topic, text="Q1?", question_type=Question.TYPE_SINGLE_CHOICE, is_active=True, order=1)
        c_right = Choice.objects.create(question=q, text="Yes", is_correct=True)
        Resource.objects.create(topic=topic, title="Material 1", is_required=True, url="https://example.com/1")

        attempt, tp = submit_quiz_attempt(self.learner1, topic, {q.id: c_right.id})
        self.assertTrue(attempt.passed)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)

    def test_28_scenario_2_acknowledgement_required_alone_view_leaves_in_progress(self):
        """T2: assessment_required=False, acknowledgement_required=True. Viewing topic page sets in_progress, NOT completed."""
        sub = Subject.objects.create(name="Sub T2", slug="sub-t2", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="T2 Topic",
            order=1,
            status="published",
            assessment_required=False,
            acknowledgement_required=True
        )
        Resource.objects.create(topic=topic, title="Material 2", is_required=True, url="https://example.com/2")

        tp = record_topic_view(self.learner1, topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)

    def test_29_scenario_3_acknowledgement_required_alone_all_materials_acknowledged_completes_topic(self):
        """T3: assessment_required=False, acknowledgement_required=True. Acknowledging all required materials completes topic."""
        sub = Subject.objects.create(name="Sub T3", slug="sub-t3", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="T3 Topic",
            order=1,
            status="published",
            assessment_required=False,
            acknowledgement_required=True
        )
        res = Resource.objects.create(topic=topic, title="Material 3", is_required=True, url="https://example.com/3")

        record_topic_view(self.learner1, topic)
        resp = self.client1.post(reverse('acknowledge_material', args=[res.id]))
        self.assertEqual(resp.status_code, 302)

        tp = TopicProgress.objects.get(user=self.learner1, topic=topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)

    def test_30_scenario_4_optional_material_does_not_block_completion(self):
        """T4: 1 required material acknowledged, 1 optional material unacknowledged. Completes topic."""
        sub = Subject.objects.create(name="Sub T4", slug="sub-t4", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="T4 Topic",
            order=1,
            status="published",
            assessment_required=False,
            acknowledgement_required=True
        )
        res_req = Resource.objects.create(topic=topic, title="Required Doc", is_required=True, url="https://example.com/req")
        res_opt = Resource.objects.create(topic=topic, title="Optional Doc", is_required=False, url="https://example.com/opt")

        record_topic_view(self.learner1, topic)
        self.client1.post(reverse('acknowledge_material', args=[res_req.id]))

        tp = TopicProgress.objects.get(user=self.learner1, topic=topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)

    def test_31_scenario_5_both_required_quiz_passed_materials_pending_remains_in_progress(self):
        """T5: Both required. Quiz passed, but materials not yet acknowledged. Remains in_progress."""
        sub = Subject.objects.create(name="Sub T5", slug="sub-t5", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="T5 Topic",
            order=1,
            status="published",
            assessment_required=True,
            acknowledgement_required=True
        )
        q = Question.objects.create(topic=topic, text="Q5?", question_type=Question.TYPE_SINGLE_CHOICE, is_active=True, order=1)
        c_right = Choice.objects.create(question=q, text="Yes", is_correct=True)
        res = Resource.objects.create(topic=topic, title="Material 5", is_required=True, url="https://example.com/5")

        attempt, tp = submit_quiz_attempt(self.learner1, topic, {q.id: c_right.id})
        self.assertTrue(attempt.passed)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)

    def test_32_scenario_6_both_required_materials_acknowledged_quiz_pending_remains_in_progress(self):
        """T6: Both required. Materials acknowledged, quiz not passed. Remains in_progress."""
        sub = Subject.objects.create(name="Sub T6", slug="sub-t6", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="T6 Topic",
            order=1,
            status="published",
            assessment_required=True,
            acknowledgement_required=True
        )
        q = Question.objects.create(topic=topic, text="Q6?", question_type=Question.TYPE_SINGLE_CHOICE, is_active=True, order=1)
        Choice.objects.create(question=q, text="Yes", is_correct=True)
        res = Resource.objects.create(topic=topic, title="Material 6", is_required=True, url="https://example.com/6")

        record_topic_view(self.learner1, topic)
        self.client1.post(reverse('acknowledge_material', args=[res.id]))

        tp = TopicProgress.objects.get(user=self.learner1, topic=topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        self.assertIsNone(tp.completed_at)

    def test_33_scenario_7_both_required_both_satisfied_completes_topic(self):
        """T7: Both required. Both quiz passed and materials acknowledged completes topic."""
        sub = Subject.objects.create(name="Sub T7", slug="sub-t7", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="T7 Topic",
            order=1,
            status="published",
            assessment_required=True,
            acknowledgement_required=True
        )
        q = Question.objects.create(topic=topic, text="Q7?", question_type=Question.TYPE_SINGLE_CHOICE, is_active=True, order=1)
        c_right = Choice.objects.create(question=q, text="Yes", is_correct=True)
        res = Resource.objects.create(topic=topic, title="Material 7", is_required=True, url="https://example.com/7")

        submit_quiz_attempt(self.learner1, topic, {q.id: c_right.id})
        self.client1.post(reverse('acknowledge_material', args=[res.id]))

        tp = TopicProgress.objects.get(user=self.learner1, topic=topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        self.assertIsNotNone(tp.completed_at)

    def test_34_scenario_8_both_completion_orders_supported(self):
        """T8: Both completion orders (materials first then quiz, or quiz first then materials) work identically."""
        sub_a = Subject.objects.create(name="Sub Order A", slug="sub-order-a", is_active=True)
        topic_a = Topic.objects.create(subject=sub_a, name="Order A", order=1, status="published", assessment_required=True, acknowledgement_required=True)
        qa = Question.objects.create(topic=topic_a, text="QA?", question_type=Question.TYPE_SINGLE_CHOICE, is_active=True)
        ca = Choice.objects.create(question=qa, text="Yes", is_correct=True)
        ra = Resource.objects.create(topic=topic_a, title="Res A", is_required=True, url="https://example.com/a")

        record_topic_view(self.learner1, topic_a)
        self.client1.post(reverse('acknowledge_material', args=[ra.id]))
        tp_a_before = TopicProgress.objects.get(user=self.learner1, topic=topic_a)
        self.assertEqual(tp_a_before.status, TopicProgress.STATUS_IN_PROGRESS)

        att_a, tp_a = submit_quiz_attempt(self.learner1, topic_a, {qa.id: ca.id})
        self.assertEqual(tp_a.status, TopicProgress.STATUS_COMPLETED)

        sub_b = Subject.objects.create(name="Sub Order B", slug="sub-order-b", is_active=True)
        topic_b = Topic.objects.create(subject=sub_b, name="Order B", order=1, status="published", assessment_required=True, acknowledgement_required=True)
        qb = Question.objects.create(topic=topic_b, text="QB?", question_type=Question.TYPE_SINGLE_CHOICE, is_active=True)
        cb = Choice.objects.create(question=qb, text="Yes", is_correct=True)
        rb = Resource.objects.create(topic=topic_b, title="Res B", is_required=True, url="https://example.com/b")

        record_topic_view(self.learner1, topic_b)
        submit_quiz_attempt(self.learner1, topic_b, {qb.id: cb.id})
        tp_b_before = TopicProgress.objects.get(user=self.learner1, topic=topic_b)
        self.assertEqual(tp_b_before.status, TopicProgress.STATUS_IN_PROGRESS)

        self.client1.post(reverse('acknowledge_material', args=[rb.id]))
        tp_b = TopicProgress.objects.get(user=self.learner1, topic=topic_b)
        self.assertEqual(tp_b.status, TopicProgress.STATUS_COMPLETED)

    def test_35_scenario_9_zero_required_materials_blocks_completion_and_shows_admin_warning(self):
        """T9: acknowledgement_required=True with 0 required materials blocks completion and renders warning in Admin Portal."""
        sub = Subject.objects.create(name="Sub T9", slug="sub-t9", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="Zero Mat Topic",
            order=1,
            status="published",
            assessment_required=False,
            acknowledgement_required=True
        )
        Resource.objects.create(topic=topic, title="Only Optional", is_required=False, url="https://example.com/opt")

        tp = record_topic_view(self.learner1, topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)
        reconcile_topic_progress(self.learner1, topic)
        tp.refresh_from_db()
        self.assertEqual(tp.status, TopicProgress.STATUS_IN_PROGRESS)

        admin_client = Client()
        admin_client.login(username='admin_user', password='password123')
        resp = admin_client.get(reverse('portal_topic_edit', args=[topic.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Configuration Warning")
        self.assertContains(resp, "no required learning materials")

    def test_36_scenario_10_inaccessible_material_does_not_block_completion(self):
        """T10: Inaccessible materials restricted via LearnerAccess do not block learner completion."""
        sub = Subject.objects.create(name="Sub T10", slug="sub-t10", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="Inaccessible Mat Topic",
            order=1,
            status="published",
            assessment_required=False,
            acknowledgement_required=True
        )
        res_accessible = Resource.objects.create(topic=topic, title="Public Material", is_required=True, url="https://example.com/pub")
        res_restricted = Resource.objects.create(topic=topic, title="Secret Material", is_required=True, url="https://example.com/sec")

        LearnerAccess.objects.create(learner=self.learner1, content_object=res_restricted, is_allowed=False)

        record_topic_view(self.learner1, topic)
        self.client1.post(reverse('acknowledge_material', args=[res_accessible.id]))

        tp = TopicProgress.objects.get(user=self.learner1, topic=topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)

    def test_37_scenario_11_version_bump_preserves_historical_completion_and_deadline(self):
        """T11: Completed topic has material version bumped from v1 -> v2. Historical completion and frozen deadline are strictly preserved."""
        sub = Subject.objects.create(name="Sub T11", slug="sub-t11", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="Version Bump Topic",
            order=1,
            status="published",
            assessment_required=False,
            acknowledgement_required=True
        )
        res = Resource.objects.create(topic=topic, title="Doc v1", is_required=True, version=1, url="https://example.com/v1")

        record_topic_view(self.learner1, topic)
        self.client1.post(reverse('acknowledge_material', args=[res.id]))

        tp = TopicProgress.objects.get(user=self.learner1, topic=topic)
        self.assertEqual(tp.status, TopicProgress.STATUS_COMPLETED)
        original_completed_at = tp.completed_at
        original_frozen_deadline = tp.frozen_deadline
        original_provenance = tp.timing_provenance
        self.assertIsNotNone(original_completed_at)

        res.version = 2
        res.save()

        reconciled = reconcile_topic_progress(self.learner1, topic)
        self.assertEqual(reconciled.status, TopicProgress.STATUS_COMPLETED)
        self.assertEqual(reconciled.completed_at, original_completed_at)
        self.assertEqual(reconciled.frozen_deadline, original_frozen_deadline)
        self.assertEqual(reconciled.timing_provenance, original_provenance)

    def test_38_scenario_12_sequential_unlocking_respects_configurable_rules(self):
        """T12: Next topic remains locked until current topic satisfies all configurable requirements."""
        sub_seq = Subject.objects.create(name="Sub Seq", slug="sub-seq", is_active=True)
        topic1 = Topic.objects.create(
            subject=sub_seq,
            name="Sequential T1",
            order=1,
            status="published",
            assessment_required=True,
            acknowledgement_required=True
        )
        q1 = Question.objects.create(topic=topic1, text="Q Seq?", question_type=Question.TYPE_SINGLE_CHOICE, is_active=True)
        c1 = Choice.objects.create(question=q1, text="Yes", is_correct=True)
        res1 = Resource.objects.create(topic=topic1, title="Seq Res 1", is_required=True, url="https://example.com/s1")

        topic2 = Topic.objects.create(
            subject=sub_seq,
            name="Sequential T2",
            order=2,
            status="published",
            assessment_required=True
        )

        record_topic_view(self.learner1, topic1)
        self.assertEqual(get_topic_access_state(self.learner1, topic2), TopicAccessState.LOCKED_SEQUENTIAL)

        # Pass quiz only -> still locked because material pending
        submit_quiz_attempt(self.learner1, topic1, {q1.id: c1.id})
        self.assertEqual(get_topic_access_state(self.learner1, topic2), TopicAccessState.LOCKED_SEQUENTIAL)

        # Now acknowledge material -> topic1 completes, topic2 unlocks!
        self.client1.post(reverse('acknowledge_material', args=[res1.id]))
        self.assertEqual(get_topic_access_state(self.learner1, topic2), TopicAccessState.AVAILABLE)

    def test_39_locked_topic_messaging_reflects_previous_topic_requirements(self):
        """Verify get_sequential_lock_reason generates correct explanatory messages for each combination."""
        from progress.services import get_sequential_lock_reason

        # Combination 1: Both quiz and ack required
        sub1 = Subject.objects.create(name="Sub Combo 1", slug="sub-combo-1", is_active=True)
        t1_1 = Topic.objects.create(subject=sub1, name="T1", order=1, status="published", assessment_required=True, acknowledgement_required=True)
        q1 = Question.objects.create(topic=t1_1, text="Q?", is_active=True)
        t1_2 = Topic.objects.create(subject=sub1, name="T2", order=2, status="published")
        msg1 = get_sequential_lock_reason(self.learner1, t1_2)
        self.assertEqual(msg1, "Complete the previous topic to unlock: pass the required quiz and review all required learning materials.")

        # Combination 2: Quiz only
        sub2 = Subject.objects.create(name="Sub Combo 2", slug="sub-combo-2", is_active=True)
        t2_1 = Topic.objects.create(subject=sub2, name="T1", order=1, status="published", assessment_required=True, acknowledgement_required=False)
        q2 = Question.objects.create(topic=t2_1, text="Q?", is_active=True)
        t2_2 = Topic.objects.create(subject=sub2, name="T2", order=2, status="published")
        msg2 = get_sequential_lock_reason(self.learner1, t2_2)
        self.assertEqual(msg2, "Complete the previous topic to unlock: pass the required quiz.")

        # Combination 3: Ack only
        sub3 = Subject.objects.create(name="Sub Combo 3", slug="sub-combo-3", is_active=True)
        t3_1 = Topic.objects.create(subject=sub3, name="T1", order=1, status="published", assessment_required=False, acknowledgement_required=True)
        t3_2 = Topic.objects.create(subject=sub3, name="T2", order=2, status="published")
        msg3 = get_sequential_lock_reason(self.learner1, t3_2)
        self.assertEqual(msg3, "Complete the previous topic to unlock: review and acknowledge all required learning materials.")

        # Combination 4: Neither required
        sub4 = Subject.objects.create(name="Sub Combo 4", slug="sub-combo-4", is_active=True)
        t4_1 = Topic.objects.create(subject=sub4, name="T1", order=1, status="published", assessment_required=False, acknowledgement_required=False)
        t4_2 = Topic.objects.create(subject=sub4, name="T2", order=2, status="published")
        msg4 = get_sequential_lock_reason(self.learner1, t4_2)
        self.assertEqual(msg4, "Complete the previous topic to unlock.")

    def test_40_acknowledgement_controls_hidden_when_setting_unchecked(self):
        """When acknowledgement_required is False, material acknowledgement controls and modals are not rendered."""
        sub = Subject.objects.create(name="Sub No Ack UI", slug="sub-no-ack-ui", is_active=True)
        topic = Topic.objects.create(
            subject=sub,
            name="No Ack Topic",
            order=1,
            status="published",
            assessment_required=False,
            acknowledgement_required=False
        )
        res = Resource.objects.create(topic=topic, title="Reference Sheet", is_required=True, url="https://example.com/ref")

        resp = self.client1.get(reverse('topic_detail', args=[sub.slug, topic.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Open Material")
        self.assertNotContains(resp, "Confirm Review")
        self.assertNotContains(resp, "Acknowledge Latest")
        self.assertNotContains(resp, f"#confirmAckModal_{res.id}")
        self.assertNotContains(resp, "Pending Review")

        # Now enable acknowledgement_required and confirm controls reappear
        topic.acknowledgement_required = True
        topic.save()
        resp2 = self.client1.get(reverse('topic_detail', args=[sub.slug, topic.id]))
        self.assertEqual(resp2.status_code, 200)
        self.assertContains(resp2, "Confirm Review")
        self.assertContains(resp2, f"#confirmAckModal_{res.id}")
        self.assertContains(resp2, "Pending Review")
