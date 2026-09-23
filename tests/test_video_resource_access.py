from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from content.models import Subject, Topic, Video, Resource
from access.models import LearnerAccess
from django.contrib.contenttypes.models import ContentType
import json

User = get_user_model()

class VideoResourceAccessTest(TestCase):
    def setUp(self):
        # Create Users
        self.learner = User.objects.create_user(username='learner', password='password123', role=User.ROLE_LEARNER)
        self.admin = User.objects.create_superuser(username='admin', password='password123', role=User.ROLE_ADMIN)
        
        # Create Content
        self.subject = Subject.objects.create(name='Test Subject', order=1, is_active=True)
        self.topic = Topic.objects.create(subject=self.subject, name='Test Topic', order=1, status=Topic.STATUS_PUBLISHED, assessment_required=False)
        
        self.video_a = Video.objects.create(topic=self.topic, title='Video A', url='https://test.com/a', order=1)
        self.video_b = Video.objects.create(topic=self.topic, title='Video B', url='https://test.com/b', order=2)
        
        self.resource_a = Resource.objects.create(topic=self.topic, title='Resource A', url='https://test.com/a.pdf', order=1)
        self.resource_b = Resource.objects.create(topic=self.topic, title='Resource B', url='https://test.com/b.pdf', order=2)

        self.topic_url = reverse('topic_detail', args=[self.subject.slug, self.topic.id])
        self.access_save_url = reverse('portal_learner_access_save', args=[self.learner.id])

    def test_1_explicit_video_denial_hides_from_learner(self):
        # Deny Video A explicitly
        LearnerAccess.objects.create(learner=self.learner, content_object=self.video_a, is_allowed=False)
        self.client.login(username='learner', password='password123')
        
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.video_a.title)
        self.assertContains(response, self.video_b.title)

    def test_2_explicit_resource_denial_hides_from_learner(self):
        # Deny Resource A explicitly
        LearnerAccess.objects.create(learner=self.learner, content_object=self.resource_a, is_allowed=False)
        self.client.login(username='learner', password='password123')
        
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.resource_a.title)
        self.assertContains(response, self.resource_b.title)

    def test_3_denying_video_a_does_not_hide_video_b(self):
        LearnerAccess.objects.create(learner=self.learner, content_object=self.video_a, is_allowed=False)
        self.client.login(username='learner', password='password123')
        
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.video_b.title)

    def test_4_denying_resource_a_does_not_hide_resource_b(self):
        LearnerAccess.objects.create(learner=self.learner, content_object=self.resource_a, is_allowed=False)
        self.client.login(username='learner', password='password123')
        
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.resource_b.title)

    def test_5_explicit_video_denial_survives_topic_deny_allow(self):
        # Simulate state preservation test via POST
        self.client.login(username='admin', password='password123')
        
        # Step 1: Explicitly deny Video A
        payload1 = [
            {"type": "video", "id": self.video_a.id}
        ]
        self.client.post(self.access_save_url, {'access_data': json.dumps(payload1)})
        
        ct_video = ContentType.objects.get_for_model(Video)
        self.assertTrue(LearnerAccess.objects.filter(learner=self.learner, content_type=ct_video, object_id=self.video_a.id, is_allowed=False).exists())

        # Step 2: Deny Topic (Video A is still explicitly denied in frontend, so we must include it in payload even if disabled)
        payload2 = [
            {"type": "topic", "id": self.topic.id},
            {"type": "video", "id": self.video_a.id}
        ]
        self.client.post(self.access_save_url, {'access_data': json.dumps(payload2)})
        
        ct_topic = ContentType.objects.get_for_model(Topic)
        self.assertTrue(LearnerAccess.objects.filter(learner=self.learner, content_type=ct_topic, object_id=self.topic.id, is_allowed=False).exists())
        self.assertTrue(LearnerAccess.objects.filter(learner=self.learner, content_type=ct_video, object_id=self.video_a.id, is_allowed=False).exists())

        # Step 3: Allow Topic (but keep Video A explicitly denied)
        payload3 = [
            {"type": "video", "id": self.video_a.id}
        ]
        self.client.post(self.access_save_url, {'access_data': json.dumps(payload3)})
        
        self.assertFalse(LearnerAccess.objects.filter(learner=self.learner, content_type=ct_topic, object_id=self.topic.id, is_allowed=False).exists())
        self.assertTrue(LearnerAccess.objects.filter(learner=self.learner, content_type=ct_video, object_id=self.video_a.id, is_allowed=False).exists())

        # Step 4: Verify learner cannot see Video A
        self.client.login(username='learner', password='password123')
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.video_a.title)
        self.assertContains(response, self.video_b.title)

    def test_6_explicit_resource_denial_survives_topic_deny_allow(self):
        self.client.login(username='admin', password='password123')
        
        payload1 = [
            {"type": "resource", "id": self.resource_a.id}
        ]
        self.client.post(self.access_save_url, {'access_data': json.dumps(payload1)})
        
        ct_res = ContentType.objects.get_for_model(Resource)
        
        payload2 = [
            {"type": "topic", "id": self.topic.id},
            {"type": "resource", "id": self.resource_a.id}
        ]
        self.client.post(self.access_save_url, {'access_data': json.dumps(payload2)})
        
        self.assertTrue(LearnerAccess.objects.filter(learner=self.learner, content_type=ct_res, object_id=self.resource_a.id, is_allowed=False).exists())

        payload3 = [
            {"type": "resource", "id": self.resource_a.id}
        ]
        self.client.post(self.access_save_url, {'access_data': json.dumps(payload3)})
        
        self.assertTrue(LearnerAccess.objects.filter(learner=self.learner, content_type=ct_res, object_id=self.resource_a.id, is_allowed=False).exists())

        self.client.login(username='learner', password='password123')
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.resource_a.title)
        self.assertContains(response, self.resource_b.title)

    def test_7_admin_bypass_remains_intact(self):
        LearnerAccess.objects.create(learner=self.admin, content_object=self.video_a, is_allowed=False)
        self.client.login(username='admin', password='password123')
        
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.video_a.title)

    def test_8_existing_inheritance_remains_intact(self):
        LearnerAccess.objects.create(learner=self.learner, content_object=self.topic, is_allowed=False)
        self.client.login(username='learner', password='password123')
        
        response = self.client.get(self.topic_url)
        self.assertEqual(response.status_code, 403)

    def test_9_cache_performance(self):
        from access.services import has_video_access, has_resource_access
        from unittest.mock import patch
        
        # Clear cache if it was accidentally populated earlier
        if hasattr(self.learner, '_learner_access_cache'):
            delattr(self.learner, '_learner_access_cache')

        self.assertFalse(hasattr(self.learner, '_learner_access_cache'))

        # First evaluation should populate the cache
        has_video_access(self.learner, self.video_a)
        
        # Verify the cache exists, is a set, and contains exactly the expected tuples
        # (Since the fixture creates no LearnerAccess records for this learner, it must be empty)
        self.assertTrue(hasattr(self.learner, '_learner_access_cache'))
        self.assertIsInstance(self.learner._learner_access_cache, set)
        self.assertEqual(len(self.learner._learner_access_cache), 0)
        self.assertEqual(self.learner._learner_access_cache, set())
        
        # Now patch LearnerAccess.objects.filter to ensure no further LearnerAccess
        # DB queries are made by the access services for this learner
        with patch('access.models.LearnerAccess.objects.filter') as mock_filter:
            has_video_access(self.learner, self.video_b)
            has_resource_access(self.learner, self.resource_a)
            has_resource_access(self.learner, self.resource_b)
            
            # The cache should completely prevent LearnerAccess from being queried again
            mock_filter.assert_not_called()
