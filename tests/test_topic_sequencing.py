from django.test import TestCase
from django.urls import reverse
from content.models import Subject, Topic
from accounts.models import User
from unittest.mock import patch
import json

class TopicSequencingTests(TestCase):
    def setUp(self):
        # Create an admin user
        self.admin_user = User.objects.create_superuser('admin', 'admin@example.com', 'password')
        
        # Create a learner user
        self.learner_user = User.objects.create_user('learner', 'learner@example.com', 'password')
        self.learner_user.is_staff = False
        self.learner_user.save()

        # Subject A with some topics
        self.subject_a = Subject.objects.create(name='Subject A', description='Desc A')
        self.topic_a1 = Topic.objects.create(subject=self.subject_a, name='A1', order=1)
        self.topic_a2 = Topic.objects.create(subject=self.subject_a, name='A2', order=2)
        self.topic_a3 = Topic.objects.create(subject=self.subject_a, name='A3', order=10) # Gap for testing MAX+1
        
        # Subject B with some topics
        self.subject_b = Subject.objects.create(name='Subject B', description='Desc B')
        self.topic_b1 = Topic.objects.create(subject=self.subject_b, name='B1', order=1)
        self.topic_b2 = Topic.objects.create(subject=self.subject_b, name='B2', order=1) # Duplicate for testing

        self.reorder_url = reverse('portal_topics_reorder')

    def test_new_topic_append_behavior(self):
        # Create new topic in Subject A
        topic_a4 = Topic.objects.create(subject=self.subject_a, name='A4')
        self.assertEqual(topic_a4.order, 11) # Max was 10, so 10 + 1 = 11

        # Create new topic in Subject B
        topic_b3 = Topic.objects.create(subject=self.subject_b, name='B3')
        self.assertEqual(topic_b3.order, 2) # Max was 1, so 1 + 1 = 2

    def test_topic_forms_no_order(self):
        from portal.forms import TopicAddForm, TopicForm
        add_form = TopicAddForm()
        edit_form = TopicForm()
        self.assertNotIn('order', add_form.fields)
        self.assertNotIn('order', edit_form.fields)

    def test_successful_reorder(self):
        self.client.force_login(self.admin_user)
        payload = {
            'subject_id': self.subject_a.id,
            'topic_ids': [self.topic_a2.id, self.topic_a3.id, self.topic_a1.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        
        self.topic_a1.refresh_from_db()
        self.topic_a2.refresh_from_db()
        self.topic_a3.refresh_from_db()
        
        self.assertEqual(self.topic_a2.order, 1)
        self.assertEqual(self.topic_a3.order, 2)
        self.assertEqual(self.topic_a1.order, 3)

    def test_subject_isolation(self):
        self.client.force_login(self.admin_user)
        payload = {
            'subject_id': self.subject_a.id,
            'topic_ids': [self.topic_a2.id, self.topic_a3.id, self.topic_a1.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        
        # Verify Subject B topics are untouched
        self.topic_b1.refresh_from_db()
        self.topic_b2.refresh_from_db()
        self.assertEqual(self.topic_b1.order, 1)
        self.assertEqual(self.topic_b2.order, 1)

    def test_cross_subject_rejection(self):
        self.client.force_login(self.admin_user)
        payload = {
            'subject_id': self.subject_a.id,
            'topic_ids': [self.topic_a1.id, self.topic_a2.id, self.topic_b1.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Submitted IDs do not exactly match existing topics for this subject.', response.json()['message'])
        
        self.topic_a1.refresh_from_db()
        self.assertEqual(self.topic_a1.order, 1) # Unchanged

    def test_missing_topic_rejection(self):
        self.client.force_login(self.admin_user)
        payload = {
            'subject_id': self.subject_a.id,
            'topic_ids': [self.topic_a1.id, self.topic_a2.id] # Missing a3
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Submitted list length does not match existing topics.', response.json()['message'])

    def test_extra_nonexistent_topic_rejection(self):
        self.client.force_login(self.admin_user)
        payload = {
            'subject_id': self.subject_a.id,
            'topic_ids': [self.topic_a1.id, self.topic_a2.id, self.topic_a3.id, 9999]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Submitted list length does not match existing topics.', response.json()['message'])

    def test_duplicate_id_rejection(self):
        self.client.force_login(self.admin_user)
        payload = {
            'subject_id': self.subject_a.id,
            'topic_ids': [self.topic_a1.id, self.topic_a2.id, self.topic_a1.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_invalid_json(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(self.reorder_url, data="invalid json", content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_invalid_topic_id_format(self):
        self.client.force_login(self.admin_user)
        payload = {
            'subject_id': self.subject_a.id,
            'topic_ids': [self.topic_a1.id, "abc", self.topic_a3.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_missing_subject_id(self):
        self.client.force_login(self.admin_user)
        payload = {
            'topic_ids': [self.topic_a1.id, self.topic_a2.id, self.topic_a3.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_invalid_subject_id(self):
        self.client.force_login(self.admin_user)
        payload = {
            'subject_id': 9999,
            'topic_ids': [self.topic_a1.id, self.topic_a2.id, self.topic_a3.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_post_requirement(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.reorder_url)
        self.assertEqual(response.status_code, 400)

    @patch('content.models.Topic.objects.filter')
    def test_atomic_rollback(self, mock_filter):
        self.client.force_login(self.admin_user)
        payload = {
            'subject_id': self.subject_a.id,
            'topic_ids': [self.topic_a2.id, self.topic_a3.id, self.topic_a1.id]
        }
        
        class MockQuerySet:
            def __init__(self, original_qs):
                self.original_qs = original_qs
                self.call_count = 0

            def update(self, **kwargs):
                self.call_count += 1
                if kwargs.get('order') == 2:
                    raise Exception("Simulated DB Error")
                return self.original_qs.update(**kwargs)
                
            def values_list(self, *args, **kwargs):
                return self.original_qs.values_list(*args, **kwargs)

            def exists(self):
                return self.original_qs.exists()

        def side_effect(*args, **kwargs):
            return MockQuerySet(Topic.objects.all().filter(*args, **kwargs))

        mock_filter.side_effect = side_effect

        with self.assertRaises(Exception):
            self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
            
        # Verify original order remains due to atomic rollback
        self.topic_a1.refresh_from_db()
        self.topic_a2.refresh_from_db()
        self.topic_a3.refresh_from_db()
        
        self.assertEqual(self.topic_a1.order, 1)
        self.assertEqual(self.topic_a2.order, 2)
        self.assertEqual(self.topic_a3.order, 10)

    def test_admin_authorization(self):
        # Learner cannot reorder
        self.client.force_login(self.learner_user)
        payload = {
            'subject_id': self.subject_a.id,
            'topic_ids': [self.topic_a2.id, self.topic_a3.id, self.topic_a1.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        # Admin required decorator redirects to login page for unauthorized users or returns 403
        self.assertIn(response.status_code, [302, 403])
