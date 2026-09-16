from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from access.models import LearnerAccess
from content.models import Subject


User = get_user_model()


class LearnerAccessSaveTransactionTest(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin',
            password='adminpass'
        )
        self.admin.role = User.ROLE_ADMIN
        self.admin.is_staff = True
        self.admin.save()

        self.learner = User.objects.create_user(
            username='learner',
            password='learnerpass'
        )
        self.learner.role = User.ROLE_LEARNER
        self.learner.save()

        self.subject_a = Subject.objects.create(
            name='Subject A',
            is_active=True,
            order=1,
        )

        self.subject_b = Subject.objects.create(
            name='Subject B',
            is_active=True,
            order=2,
        )

        self.client = Client()
        self.client.login(
            username='admin',
            password='adminpass'
        )

    def test_access_save_rolls_back_when_creation_fails(self):
        # Existing restriction that should be restored by rollback.
        subject_ct = ContentType.objects.get_for_model(Subject)

        LearnerAccess.objects.create(
            learner=self.learner,
            content_type=subject_ct,
            object_id=self.subject_a.id,
            is_allowed=False,
        )

        save_url = reverse(
            'portal_learner_access_save',
            kwargs={'learner_id': self.learner.id},
        )

        # New request removes Subject A restriction
        # and adds Subject B restriction.
        payload = {
            'access_data': (
                f'[{{"type": "subject", "id": {self.subject_b.id}}}]'
            ),
        }

        # Force the actual get_or_create operation used by the view
        # to fail after stale restrictions have been deleted.
        with patch.object(
            LearnerAccess.objects,
            'get_or_create',
            side_effect=RuntimeError('Simulated database failure'),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    save_url,
                    data=payload,
                )

        # The transaction must have restored the original restriction.
        original_access = LearnerAccess.objects.filter(
            learner=self.learner,
            content_type=subject_ct,
            object_id=self.subject_a.id,
            is_allowed=False,
        )

        self.assertEqual(original_access.count(), 1)

        # The new restriction must not have been persisted.
        new_access = LearnerAccess.objects.filter(
            learner=self.learner,
            content_type=subject_ct,
            object_id=self.subject_b.id,
            is_allowed=False,
        )

        self.assertEqual(new_access.count(), 0)
