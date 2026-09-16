from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from access.models import LearnerAccess
from content.models import Subject, Topic, Video, Resource

User = get_user_model()

class LearnerAccessUITest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin', password='adminpass')
        self.admin.role = User.ROLE_ADMIN
        self.admin.is_staff = True
        self.admin.save()
        self.learner = User.objects.create_user(username='learner', password='learnerpass')
        self.learner.role = User.ROLE_LEARNER
        self.learner.save()
        self.subject = Subject.objects.create(name='Test Subject', is_active=True, order=1)
        self.topic = Topic.objects.create(subject=self.subject, name='Test Topic', status=Topic.STATUS_PUBLISHED, order=1)
        self.video = Video.objects.create(topic=self.topic, title='Test Video')
        self.resource = Resource.objects.create(topic=self.topic, title='Test Resource')
        self.client = Client()

    def test_admin_can_access_ui(self):
        self.client.login(username='admin', password='adminpass')
        url = reverse('portal_learner_access', kwargs={'learner_id': self.learner.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Access Control for Learner')

    def test_non_admin_forbidden(self):
        self.client.login(username='learner', password='learnerpass')
        url = reverse('portal_learner_access', kwargs={'learner_id': self.learner.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_save_denied_access(self):
        self.client.login(username='admin', password='adminpass')
        save_url = reverse('portal_learner_access_save', kwargs={'learner_id': self.learner.id})
        payload = {
            'access_data': f'[{{"type": "subject", "id": {self.subject.id}}}]',
        }
        response = self.client.post(save_url, data=payload, follow=True)
        self.assertRedirects(response, reverse('portal_learner_access', kwargs={'learner_id': self.learner.id}))
        la_qs = LearnerAccess.objects.filter(learner=self.learner, is_allowed=False)
        self.assertEqual(la_qs.count(), 1)
        la = la_qs.first()
        self.assertEqual(la.content_object, self.subject)
        child_qs = LearnerAccess.objects.filter(learner=self.learner, is_allowed=False).exclude(content_type__model='subject')
        self.assertEqual(child_qs.count(), 0)

    def test_case_a_independent_topic_and_video(self):
        topic_a = self.topic
        topic_b = Topic.objects.create(subject=self.subject, name='Topic B', status=Topic.STATUS_PUBLISHED, order=2)
        video_b = Video.objects.create(topic=topic_b, title='Video B')

        self.client.login(username='admin', password='adminpass')
        save_url = reverse('portal_learner_access_save', kwargs={'learner_id': self.learner.id})
        payload = {
            'access_data': f'[{{"type": "topic", "id": {topic_a.id}}}, {{"type": "video", "id": {video_b.id}}}]',
        }
        response = self.client.post(save_url, data=payload, follow=True)
        self.assertRedirects(response, reverse('portal_learner_access', kwargs={'learner_id': self.learner.id}))

        la_qs = LearnerAccess.objects.filter(learner=self.learner, is_allowed=False)
        self.assertEqual(la_qs.count(), 2)
        self.assertTrue(la_qs.filter(object_id=topic_a.id, content_type__model='topic').exists())
        self.assertTrue(la_qs.filter(object_id=video_b.id, content_type__model='video').exists())

    def test_case_b_child_alone(self):
        self.client.login(username='admin', password='adminpass')
        save_url = reverse('portal_learner_access_save', kwargs={'learner_id': self.learner.id})
        payload = {
            'access_data': f'[{{"type": "video", "id": {self.video.id}}}]',
        }
        response = self.client.post(save_url, data=payload, follow=True)
        self.assertRedirects(response, reverse('portal_learner_access', kwargs={'learner_id': self.learner.id}))

        la_qs = LearnerAccess.objects.filter(learner=self.learner, is_allowed=False)
        self.assertEqual(la_qs.count(), 1)
        self.assertEqual(la_qs.first().content_object, self.video)

    def test_case_c_parent_and_child_redundant(self):
        self.client.login(username='admin', password='adminpass')
        save_url = reverse('portal_learner_access_save', kwargs={'learner_id': self.learner.id})
        payload = {
            'access_data': f'[{{"type": "topic", "id": {self.topic.id}}}, {{"type": "video", "id": {self.video.id}}}]',
        }
        response = self.client.post(save_url, data=payload, follow=True)
        self.assertRedirects(response, reverse('portal_learner_access', kwargs={'learner_id': self.learner.id}))

        la_qs = LearnerAccess.objects.filter(learner=self.learner, is_allowed=False)
        self.assertEqual(la_qs.count(), 1)
        self.assertEqual(la_qs.first().content_object, self.topic)

    def test_case_d_independent_resource(self):
        topic_a = self.topic
        topic_b = Topic.objects.create(subject=self.subject, name='Topic B', status=Topic.STATUS_PUBLISHED, order=2)
        resource_b = Resource.objects.create(topic=topic_b, title='Resource B')

        self.client.login(username='admin', password='adminpass')
        save_url = reverse('portal_learner_access_save', kwargs={'learner_id': self.learner.id})
        payload = {
            'access_data': f'[{{"type": "topic", "id": {topic_a.id}}}, {{"type": "resource", "id": {resource_b.id}}}]',
        }
        response = self.client.post(save_url, data=payload, follow=True)
        self.assertRedirects(response, reverse('portal_learner_access', kwargs={'learner_id': self.learner.id}))

        la_qs = LearnerAccess.objects.filter(learner=self.learner, is_allowed=False)
        self.assertEqual(la_qs.count(), 2)
        self.assertTrue(la_qs.filter(object_id=topic_a.id, content_type__model='topic').exists())
        self.assertTrue(la_qs.filter(object_id=resource_b.id, content_type__model='resource').exists())
