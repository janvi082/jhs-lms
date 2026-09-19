from django.test import TestCase
from django.urls import reverse
from content.models import Subject, Topic, Resource
from accounts.models import User
from unittest.mock import patch
import json

class resourceSequencingTests(TestCase):
    def setUp(self):
        # Create an admin user
        self.admin_user = User.objects.create_superuser('admin', 'admin@example.com', 'password')
        
        # Create a learner user
        self.learner_user = User.objects.create_user('learner', 'learner@example.com', 'password')
        self.learner_user.is_staff = False
        self.learner_user.save()

        # Subject and Topics
        self.subject = Subject.objects.create(name='Subject', description='Desc')
        self.topic_a = Topic.objects.create(subject=self.subject, name='Topic A', order=1)
        self.topic_b = Topic.objects.create(subject=self.subject, name='Topic B', order=2)
        
        # Topic A with some resources
        self.resource_a1 = Resource.objects.create(topic=self.topic_a, title='A1', order=1)
        self.resource_a2 = Resource.objects.create(topic=self.topic_a, title='A2', order=2)
        self.resource_a3 = Resource.objects.create(topic=self.topic_a, title='A3', order=10) # Gap for testing MAX+1
        
        # Topic B with some resources
        self.resource_b1 = Resource.objects.create(topic=self.topic_b, title='B1', order=1)
        self.resource_b2 = Resource.objects.create(topic=self.topic_b, title='B2', order=1) # Duplicate for testing

        self.reorder_url = reverse('portal_resources_reorder')

    def test_new_resource_append_behavior(self):
        # Create new resource in Topic A
        resource_a4 = Resource.objects.create(topic=self.topic_a, title='A4')
        self.assertEqual(resource_a4.order, 11) # Max was 10, so 10 + 1 = 11

        # Create new resource in Topic B
        resource_b3 = Resource.objects.create(topic=self.topic_b, title='B3')
        self.assertEqual(resource_b3.order, 2) # Max was 1, so 1 + 1 = 2

    def test_existing_resource_save_preserves_order(self):
        # First resource in an empty Topic gets order 1
        empty_topic = Topic.objects.create(subject=self.subject, name='Empty Topic', order=3)
        first_resource = Resource.objects.create(topic=empty_topic, title='First')
        self.assertEqual(first_resource.order, 1)

        # Existing resource's save does not change its order
        old_order = self.resource_a1.order
        self.resource_a1.title = "Updated Title"
        self.resource_a1.save()
        self.resource_a1.refresh_from_db()
        self.assertEqual(self.resource_a1.order, old_order)

    def test_resource_forms_no_order(self):
        from portal.forms import ResourceForm
        edit_form = ResourceForm()
        self.assertNotIn('order', edit_form.fields)

    def test_successful_reorder(self):
        self.client.force_login(self.admin_user)
        payload = {
            'topic_id': self.topic_a.id,
            'resource_ids': [self.resource_a2.id, self.resource_a3.id, self.resource_a1.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        
        self.resource_a1.refresh_from_db()
        self.resource_a2.refresh_from_db()
        self.resource_a3.refresh_from_db()
        
        self.assertEqual(self.resource_a2.order, 1)
        self.assertEqual(self.resource_a3.order, 2)
        self.assertEqual(self.resource_a1.order, 3)

    def test_topic_isolation(self):
        self.client.force_login(self.admin_user)
        payload = {
            'topic_id': self.topic_a.id,
            'resource_ids': [self.resource_a2.id, self.resource_a3.id, self.resource_a1.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        
        # Verify Topic B resources are untouched
        self.resource_b1.refresh_from_db()
        self.resource_b2.refresh_from_db()
        self.assertEqual(self.resource_b1.order, 1)
        self.assertEqual(self.resource_b2.order, 1)

    def test_cross_topic_rejection(self):
        self.client.force_login(self.admin_user)
        payload = {
            'topic_id': self.topic_a.id,
            'resource_ids': [self.resource_a1.id, self.resource_a2.id, self.resource_b1.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Submitted IDs do not exactly match existing resources for this topic.', response.json()['error'])
        
        self.resource_a1.refresh_from_db()
        self.assertEqual(self.resource_a1.order, 1) # Unchanged

    def test_missing_resource_rejection(self):
        self.client.force_login(self.admin_user)
        payload = {
            'topic_id': self.topic_a.id,
            'resource_ids': [self.resource_a1.id, self.resource_a2.id] # Missing a3
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Submitted list length does not match existing resources.', response.json()['error'])

    def test_extra_nonexistent_resource_rejection(self):
        self.client.force_login(self.admin_user)
        payload = {
            'topic_id': self.topic_a.id,
            'resource_ids': [self.resource_a1.id, self.resource_a2.id, self.resource_a3.id, 9999]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Submitted list length does not match existing resources.', response.json()['error'])

    def test_duplicate_id_rejection(self):
        self.client.force_login(self.admin_user)
        payload = {
            'topic_id': self.topic_a.id,
            'resource_ids': [self.resource_a1.id, self.resource_a2.id, self.resource_a1.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_invalid_json(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(self.reorder_url, data="invalid json", content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_invalid_resource_id_format(self):
        self.client.force_login(self.admin_user)
        payload = {
            'topic_id': self.topic_a.id,
            'resource_ids': [self.resource_a1.id, "abc", self.resource_a3.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_missing_topic_id(self):
        self.client.force_login(self.admin_user)
        payload = {
            'resource_ids': [self.resource_a1.id, self.resource_a2.id, self.resource_a3.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_invalid_topic_id(self):
        self.client.force_login(self.admin_user)
        payload = {
            'topic_id': 9999,
            'resource_ids': [self.resource_a1.id, self.resource_a2.id, self.resource_a3.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_post_requirement(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.reorder_url)
        self.assertEqual(response.status_code, 400)

    @patch('content.models.Resource.objects.filter')
    def test_atomic_rollback(self, mock_filter):
        self.client.force_login(self.admin_user)
        payload = {
            'topic_id': self.topic_a.id,
            'resource_ids': [self.resource_a2.id, self.resource_a3.id, self.resource_a1.id]
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
            return MockQuerySet(Resource.objects.all().filter(*args, **kwargs))

        mock_filter.side_effect = side_effect

        with self.assertRaisesRegex(Exception, "Simulated DB Error"):
            self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
            
        # Verify original order remains due to atomic rollback
        self.resource_a1.refresh_from_db()
        self.resource_a2.refresh_from_db()
        self.resource_a3.refresh_from_db()
        
        self.assertEqual(self.resource_a1.order, 1)
        self.assertEqual(self.resource_a2.order, 2)
        self.assertEqual(self.resource_a3.order, 10)

    def test_invalid_topic_id_type(self):
        self.client.force_login(self.admin_user)
        payload = {'topic_id': [1, 2], 'resource_ids': [self.resource_a1.id]}
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_invalid_resource_id_type_null(self):
        self.client.force_login(self.admin_user)
        payload = {'topic_id': self.topic_a.id, 'resource_ids': [self.resource_a1.id, None]}
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_admin_authorization(self):
        # Learner cannot reorder
        self.client.force_login(self.learner_user)
        payload = {
            'topic_id': self.topic_a.id,
            'resource_ids': [self.resource_a2.id, self.resource_a3.id, self.resource_a1.id]
        }
        response = self.client.post(self.reorder_url, data=json.dumps(payload), content_type='application/json')
        self.assertIn(response.status_code, [302, 403])

class resourceFilteringTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser('admin2', 'admin2@example.com', 'password')
        self.subject_1 = Subject.objects.create(name='Subject 1', order=1)
        self.subject_2 = Subject.objects.create(name='Subject 2', order=2)
        self.topic_1a = Topic.objects.create(subject=self.subject_1, name='Topic 1A', order=1)
        self.topic_1b = Topic.objects.create(subject=self.subject_1, name='Topic 1B', order=2)
        self.topic_2a = Topic.objects.create(subject=self.subject_2, name='Topic 2A', order=1)
        
        self.resource_1 = Resource.objects.create(topic=self.topic_1a, title='V1')
        self.resource_2 = Resource.objects.create(topic=self.topic_1b, title='V2')
        self.resource_3 = Resource.objects.create(topic=self.topic_2a, title='V3')
        
        self.overview_url = reverse('portal_materials_overview')

    def test_all_subjects_all_topics(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(self.overview_url)
        self.assertEqual(response.status_code, 200)
        materials = response.context['materials']
        self.assertEqual(len(materials), 3)
        self.assertIsNone(response.context['selected_subject'])
        self.assertIsNone(response.context['selected_topic'])

    def test_subject_selected_all_topics(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(f"{self.overview_url}?subject_id={self.subject_1.id}")
        self.assertEqual(response.status_code, 200)
        materials = response.context['materials']
        self.assertEqual(len(materials), 2)
        self.assertEqual(response.context['selected_subject'], self.subject_1)
        self.assertIsNone(response.context['selected_topic'])

    def test_subject_selected_valid_topic(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(f"{self.overview_url}?subject_id={self.subject_1.id}&topic_id={self.topic_1a.id}")
        self.assertEqual(response.status_code, 200)
        materials = response.context['materials']
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0], self.resource_1)
        self.assertEqual(response.context['selected_subject'], self.subject_1)
        self.assertEqual(response.context['selected_topic'], self.topic_1a)

    def test_changing_subject_resets_incompatible_topic(self):
        self.client.force_login(self.admin_user)
        # Pass subject 2, but topic 1A (which belongs to subject 1)
        response = self.client.get(f"{self.overview_url}?subject_id={self.subject_2.id}&topic_id={self.topic_1a.id}")
        self.assertEqual(response.status_code, 200)
        materials = response.context['materials']
        # It should reset topic, so it shows all resources for subject 2 (which is V3)
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0], self.resource_3)
        self.assertEqual(response.context['selected_subject'], self.subject_2)
        self.assertIsNone(response.context['selected_topic'])

    def test_invalid_subject_id(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(f"{self.overview_url}?subject_id=999999")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['selected_subject'])
        self.assertIsNone(response.context['selected_topic'])

    def test_invalid_topic_id(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(f"{self.overview_url}?subject_id={self.subject_1.id}&topic_id=999999")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_subject'], self.subject_1)
        self.assertIsNone(response.context['selected_topic'])
