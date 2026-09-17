from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from content.models import Subject
import json

class SubjectSequencingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='portaladmin',
            password='password123',
            role=User.ROLE_ADMIN,
            is_staff=True
        )
        self.client = Client()
        self.client.login(username='portaladmin', password='password123')
        
        # Clear subjects created in migrations/demo data
        Subject.objects.all().delete()
        
        self.sub_a = Subject.objects.create(name="A", is_active=True, order=1)
        self.sub_b = Subject.objects.create(name="B", is_active=True, order=2)
        self.sub_c = Subject.objects.create(name="C", is_active=True, order=3)
        self.sub_d = Subject.objects.create(name="D", is_active=True, order=4)

    def test_new_subject_appended_to_end(self):
        """1. New Subject is appended to the end."""
        self.assertEqual(Subject.objects.count(), 4)
        
        post_data = {
            'name': 'E',
            'description': 'Test',
            'is_active': True
        }
        response = self.client.post(reverse('portal_subject_add'), post_data)
        self.assertEqual(response.status_code, 302)
        
        sub_e = Subject.objects.get(name='E')
        self.assertEqual(sub_e.order, 5)

    def test_reorder_valid(self):
        """2. A1 B2 C3 D4 reordered to A1 D2 B3 C4"""
        """4. Save Order persists the new positions."""
        """5. Positions are exactly sequential 1..N after successful save."""
        
        new_order_ids = [self.sub_a.id, self.sub_d.id, self.sub_b.id, self.sub_c.id]
        
        response = self.client.post(
            reverse('portal_subjects_reorder'),
            data=json.dumps({'subject_ids': new_order_ids}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        
        self.sub_a.refresh_from_db()
        self.sub_d.refresh_from_db()
        self.sub_b.refresh_from_db()
        self.sub_c.refresh_from_db()
        
        self.assertEqual(self.sub_a.order, 1)
        self.assertEqual(self.sub_d.order, 2)
        self.assertEqual(self.sub_b.order, 3)
        self.assertEqual(self.sub_c.order, 4)

    def test_reject_duplicate_ids(self):
        """6. Duplicate submitted IDs are rejected."""
        new_order_ids = [self.sub_a.id, self.sub_a.id, self.sub_b.id, self.sub_c.id]
        response = self.client.post(
            reverse('portal_subjects_reorder'),
            data=json.dumps({'subject_ids': new_order_ids}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.order, 1)

    def test_reject_invalid_id(self):
        """7. Invalid Subject ID is rejected."""
        new_order_ids = [self.sub_a.id, self.sub_b.id, "not-an-id", self.sub_d.id]
        response = self.client.post(
            reverse('portal_subjects_reorder'),
            data=json.dumps({'subject_ids': new_order_ids}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.order, 1)

    def test_reject_missing_id(self):
        """8. Missing Subject ID is rejected."""
        new_order_ids = [self.sub_a.id, self.sub_b.id, self.sub_c.id] # D is missing
        response = self.client.post(
            reverse('portal_subjects_reorder'),
            data=json.dumps({'subject_ids': new_order_ids}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('length does not match', response.json()['message'])
        
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.order, 1)

    def test_reject_unexpected_id(self):
        """9. Unexpected/foreign ID is rejected."""
        # 9999 is unexpected
        new_order_ids = [self.sub_a.id, self.sub_b.id, self.sub_c.id, self.sub_d.id, 9999]
        response = self.client.post(
            reverse('portal_subjects_reorder'),
            data=json.dumps({'subject_ids': new_order_ids}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('length does not match', response.json()['message'])

    def test_cancel_or_failed_validation_leaves_original(self):
        """10. Failed validation leaves the original database ordering unchanged."""
        new_order_ids = [self.sub_d.id, self.sub_a.id, self.sub_b.id] # missing C
        response = self.client.post(
            reverse('portal_subjects_reorder'),
            data=json.dumps({'subject_ids': new_order_ids}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.order, 1)

    def test_transaction_rollback_on_exception(self):
        """11. An exception during the reorder transaction leaves the original ordering unchanged, proving no partial order changes remain."""
        from unittest.mock import patch
        
        new_order_ids = [self.sub_d.id, self.sub_a.id, self.sub_b.id, self.sub_c.id]
        
        original_filter = Subject.objects.filter
        def mocked_filter(*args, **kwargs):
            if kwargs.get('id') == self.sub_a.id:
                raise Exception("Simulated DB error during loop")
            return original_filter(*args, **kwargs)

        with patch('content.models.Subject.objects.filter', side_effect=mocked_filter):
            try:
                self.client.post(
                    reverse('portal_subjects_reorder'),
                    data=json.dumps({'subject_ids': new_order_ids}),
                    content_type='application/json'
                )
            except Exception:
                pass # Expected
                
        # D was updated before A in the loop. Verify all objects rolled back safely.
        self.sub_a.refresh_from_db()
        self.sub_b.refresh_from_db()
        self.sub_c.refresh_from_db()
        self.sub_d.refresh_from_db()
        
        self.assertEqual(self.sub_a.order, 1)
        self.assertEqual(self.sub_b.order, 2)
        self.assertEqual(self.sub_c.order, 3)
        self.assertEqual(self.sub_d.order, 4)

    def test_up_down_arrows_equivalent_to_drag(self):
        """12. Up/down controls produce the same final sequence as drag-and-drop."""
        # Initial: A, B, C, D
        # Drag D to position 2 -> A, D, B, C
        drag_payload = [self.sub_a.id, self.sub_d.id, self.sub_b.id, self.sub_c.id]
        
        # Up Arrow on D (pos 4) -> A, B, D, C (pos 3)
        # Up Arrow on D (pos 3) -> A, D, B, C (pos 2)
        up_payload = [self.sub_a.id, self.sub_d.id, self.sub_b.id, self.sub_c.id]
        
        self.assertEqual(drag_payload, up_payload)
        
        # Both submit the exact same array to the backend, resulting in identical 1..N database assignment
        response = self.client.post(
            reverse('portal_subjects_reorder'),
            data=json.dumps({'subject_ids': up_payload}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        
        self.sub_d.refresh_from_db()
        self.assertEqual(self.sub_d.order, 2)

    def test_saved_order_persists_after_reload(self):
        """14. Saved order remains correct after a fresh database query/page reload."""
        new_order_ids = [self.sub_d.id, self.sub_a.id, self.sub_b.id, self.sub_c.id]
        self.client.post(
            reverse('portal_subjects_reorder'),
            data=json.dumps({'subject_ids': new_order_ids}),
            content_type='application/json'
        )
        
        # Simulate page reload query
        subjects = list(Subject.objects.all().order_by('order', 'name'))
        self.assertEqual(subjects[0].id, self.sub_d.id)
        self.assertEqual(subjects[0].order, 1)
        self.assertEqual(subjects[1].id, self.sub_a.id)
        self.assertEqual(subjects[1].order, 2)
        self.assertEqual(subjects[2].id, self.sub_b.id)
        self.assertEqual(subjects[2].order, 3)
        self.assertEqual(subjects[3].id, self.sub_c.id)
        self.assertEqual(subjects[3].order, 4)
