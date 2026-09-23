from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User
from progress.models import TopicProgress
from content.models import Subject, Topic

class PortalLearnersProgressTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='adminpass', role=User.ROLE_ADMIN, is_staff=True
        )
        self.learner = User.objects.create_user(
            username='learner1', password='password123', role=User.ROLE_LEARNER, email='learner1@example.com'
        )
        self.client = Client()

    def test_learners_page_admin_only(self):
        # Unauthenticated
        resp = self.client.get(reverse('portal_learners_list'))
        self.assertNotEqual(resp.status_code, 200)
        # Learner
        self.client.login(username='learner1', password='password123')
        resp = self.client.get(reverse('portal_learners_list'))
        self.assertNotEqual(resp.status_code, 200)

    def test_progress_page_admin_only(self):
        resp = self.client.get(reverse('portal_progress'))
        self.assertNotEqual(resp.status_code, 200)
        self.client.login(username='learner1', password='password123')
        resp = self.client.get(reverse('portal_progress'))
        self.assertNotEqual(resp.status_code, 200)

    def test_learners_page_content(self):
        self.client.login(username='admin', password='adminpass')
        resp = self.client.get(reverse('portal_learners_list'))
        self.assertEqual(resp.status_code, 200)
        
        # Check context does not have overall_progress
        learners_data = resp.context['learners_data']
        for item in learners_data:
            self.assertNotIn('overall_progress', item)
            self.assertIn('learner', item)
            
        # Check template content
        content = resp.content.decode('utf-8')
        self.assertIn('Add Learner', content)
        self.assertIn('Access', content)
        self.assertIn('Deactivate', content)
        self.assertIn('Reset Password', content)
        
        self.assertNotIn('Overall Progress', content)
        self.assertNotIn('View Progress', content)

    def test_progress_page_content(self):
        self.client.login(username='admin', password='adminpass')
        resp = self.client.get(reverse('portal_progress'))
        self.assertEqual(resp.status_code, 200)
        
        # Check context has overall_progress
        learners_data = resp.context['learners_data']
        for item in learners_data:
            self.assertIn('overall_progress', item)
            self.assertIn('learner', item)
            
        # Check template content
        content = resp.content.decode('utf-8')
        self.assertIn('Overall Progress', content)
        self.assertIn('View Progress', content)
        self.assertIn('learner1@example.com', content)
        
        self.assertNotIn('Add Learner', content)
        self.assertNotIn('Access', content)
        self.assertNotIn('Deactivate', content)
        self.assertNotIn('Reset Password', content)

    def test_view_progress_drilldown(self):
        self.client.login(username='admin', password='adminpass')
        url = reverse('portal_learner_progress_detail', args=[self.learner.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.learner.username, resp.content.decode('utf-8'))
