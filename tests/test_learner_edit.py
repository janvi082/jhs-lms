from django.test import TestCase, Client
from django.urls import reverse
from accounts.models import User

class LearnerEditTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_user(
            username='admin_test',
            password='password123',
            role=User.ROLE_ADMIN,
            is_staff=True,
            is_superuser=True
        )
        self.learner_user = User.objects.create_user(
            username='learner1',
            first_name='John',
            last_name='Doe',
            email='john@example.com',
            password='password123',
            role=User.ROLE_LEARNER
        )
        self.learner_user2 = User.objects.create_user(
            username='learner2',
            password='password123',
            role=User.ROLE_LEARNER
        )
        self.edit_url = reverse('portal_learner_edit', args=[self.learner_user.id])

    def test_admin_can_get_learner_edit_endpoint(self):
        self.client.login(username='admin_test', password='password123')
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portal/learners_list.html')
        self.assertIn('edit_form', response.context)
        self.assertEqual(response.context['edit_learner'], self.learner_user)
        self.assertEqual(response.context['show_modal'], 'learner_edit')

    def test_admin_can_submit_valid_edit_data(self):
        self.client.login(username='admin_test', password='password123')
        data = {
            'username': 'john_updated',
            'first_name': 'Johnny',
            'last_name': 'D',
            'email': 'johnny@example.com',
        }
        response = self.client.post(self.edit_url, data)
        self.assertEqual(response.status_code, 302)
        
        self.learner_user.refresh_from_db()
        self.assertEqual(self.learner_user.username, 'john_updated')
        self.assertEqual(self.learner_user.first_name, 'Johnny')
        self.assertEqual(self.learner_user.last_name, 'D')
        self.assertEqual(self.learner_user.email, 'johnny@example.com')

    def test_duplicate_username_is_rejected(self):
        self.client.login(username='admin_test', password='password123')
        data = {
            'username': 'learner2', # already exists
            'first_name': 'Johnny',
            'last_name': 'D',
            'email': 'johnny@example.com',
        }
        response = self.client.post(self.edit_url, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('edit_form', response.context)
        self.assertTrue(response.context['edit_form'].errors)
        
        self.learner_user.refresh_from_db()
        self.assertEqual(self.learner_user.username, 'learner1') # unchanged

    def test_non_admin_cannot_access_edit_endpoint(self):
        self.client.login(username='learner1', password='password123')
        
        # GET
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 403)
        
        # POST
        data = {
            'username': 'hacked_username',
        }
        response = self.client.post(self.edit_url, data)
        self.assertEqual(response.status_code, 403)
        
        self.learner_user.refresh_from_db()
        self.assertEqual(self.learner_user.username, 'learner1') # unchanged

    def test_existing_learner_actions_remain_available_on_learners_page(self):
        self.client.login(username='admin_test', password='password123')
        response = self.client.get(reverse('portal_learners_list'))
        self.assertEqual(response.status_code, 200)
        
        content = response.content.decode('utf-8')
        # Check that Edit Details button is present
        self.assertIn(f'href="{self.edit_url}"', content)
        self.assertIn('Edit Details', content)
        
        # Check that other actions are present
        access_url = reverse('portal_learner_access', args=[self.learner_user.id])
        toggle_url = reverse('portal_learner_toggle_status', args=[self.learner_user.id])
        reset_url = reverse('portal_learner_reset_password', args=[self.learner_user.id])
        
        self.assertIn(f'href="{access_url}"', content)
        self.assertIn(f'action="{toggle_url}"', content)
        self.assertIn(f'data-action-url="{reset_url}"', content)
