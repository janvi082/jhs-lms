from django.test import TestCase, Client
from django.urls import reverse
from django.contrib import messages
from accounts.models import User

class LearnerPasswordResetTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='adminpass', role=User.ROLE_ADMIN, is_staff=True
        )
        self.learner = User.objects.create_user(
            username='learner', password='oldpass123', role=User.ROLE_LEARNER
        )
        self.client = Client()
        self.reset_url = reverse('portal_learner_reset_password', args=[self.learner.id])
        self.list_url = reverse('portal_learners_list')

    def test_admin_can_access_reset_page_get(self):
        self.client.login(username='admin', password='adminpass')
        resp = self.client.get(self.reset_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="new_password"')
        self.assertContains(resp, 'name="confirm_password"')

    def test_non_admin_cannot_access_reset_page(self):
        self.client.login(username='learner', password='oldpass123')
        resp = self.client.get(self.reset_url)
        self.assertNotEqual(resp.status_code, 200)
        self.assertTrue(resp.status_code in (302, 403))

    def test_successful_password_reset(self):
        self.client.login(username='admin', password='adminpass')
        new_pwd = 'StrongPass123!'
        resp = self.client.post(self.reset_url, {
            'new_password': new_pwd,
            'confirm_password': new_pwd,
        }, follow=True)
        self.assertRedirects(resp, self.list_url)
        msgs = list(messages.get_messages(resp.wsgi_request))
        self.assertTrue(any('Password reset successfully' in str(m) for m in msgs))
        self.learner.refresh_from_db()
        self.assertTrue(self.learner.check_password(new_pwd))
        self.assertFalse(self.learner.check_password('oldpass123'))

    def test_password_mismatch_shows_error(self):
        self.client.login(username='admin', password='adminpass')
        resp = self.client.post(self.reset_url, {
            'new_password': 'Pass123!',
            'confirm_password': 'Pass1234!',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Passwords do not match.')

    def test_invalid_password_fails_validation(self):
        self.client.login(username='admin', password='adminpass')
        resp = self.client.post(self.reset_url, {
            'new_password': 'short',
            'confirm_password': 'short',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'This password is too short')
