import json
from django.test import TestCase, Client
from django.urls import reverse
from django.db import IntegrityError, transaction
from accounts.models import User
from content.models import Subject, LearningPhase, PhaseName
from portal.forms import PhaseNameForm, LearningPhaseForm

class PhaseNameCRUDTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_user",
            password="password123",
            role=User.ROLE_ADMIN
        )
        self.learner = User.objects.create_user(
            username="learner_user",
            password="password123",
            role=User.ROLE_LEARNER
        )
        self.subject = Subject.objects.create(name="Finance", order=1, is_active=True)

    def test_admin_create_valid_phase_name(self):
        self.client.login(username="admin_user", password="password123")
        response = self.client.post(reverse('portal_settings_phase_add'), {
            'name': 'Foundation'
        })
        self.assertRedirects(response, reverse('portal_settings'))
        self.assertTrue(PhaseName.objects.filter(name='Foundation').exists())

    def test_case_insensitive_duplicate_rejected_by_form(self):
        PhaseName.objects.create(name='Foundation')
        form = PhaseNameForm(data={'name': 'foundation'})
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)
        self.assertIn('A phase name with this value already exists.', form.errors['name'])

    def test_blank_or_whitespace_name_rejected_by_form(self):
        form = PhaseNameForm(data={'name': '   '})
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)
        self.assertIn('This field is required.', form.errors['name'])

    def test_admin_create_duplicate_shows_error_message(self):
        PhaseName.objects.create(name='Intermediate')
        self.client.login(username="admin_user", password="password123")
        response = self.client.post(reverse('portal_settings_phase_add'), {
            'name': 'intermediate'
        }, follow=True)
        self.assertRedirects(response, reverse('portal_settings'))
        self.assertContains(response, 'A phase name with this value already exists.')
        self.assertEqual(PhaseName.objects.filter(name__iexact='intermediate').count(), 1)

    def test_database_constraint_prevents_case_insensitive_duplicates(self):
        PhaseName.objects.create(name='Advance')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PhaseName.objects.create(name='advance')

    def test_admin_edit_phase_name(self):
        pn = PhaseName.objects.create(name='Foudnation')
        self.client.login(username="admin_user", password="password123")
        response = self.client.post(reverse('portal_settings_phase_edit', args=[pn.id]), {
            'name': 'Foundation'
        })
        self.assertRedirects(response, reverse('portal_settings'))
        pn.refresh_from_db()
        self.assertEqual(pn.name, 'Foundation')

    def test_admin_edit_duplicate_rejected(self):
        PhaseName.objects.create(name='Foundation')
        pn2 = PhaseName.objects.create(name='Advanced')
        self.client.login(username="admin_user", password="password123")
        response = self.client.post(reverse('portal_settings_phase_edit', args=[pn2.id]), {
            'name': 'foundation'
        }, follow=True)
        self.assertRedirects(response, reverse('portal_settings'))
        self.assertContains(response, 'A phase name with this value already exists.')
        pn2.refresh_from_db()
        self.assertEqual(pn2.name, 'Advanced')

    def test_admin_edit_same_name_casing_allowed(self):
        pn = PhaseName.objects.create(name='foundation')
        self.client.login(username="admin_user", password="password123")
        response = self.client.post(reverse('portal_settings_phase_edit', args=[pn.id]), {
            'name': 'Foundation'
        })
        self.assertRedirects(response, reverse('portal_settings'))
        pn.refresh_from_db()
        self.assertEqual(pn.name, 'Foundation')

    def test_admin_delete_phase_name_preserves_learning_phases(self):
        pn = PhaseName.objects.create(name='Foundation')
        lp = LearningPhase.objects.create(
            subject=self.subject,
            name='Foundation',
            start_day=1,
            end_day=10
        )
        self.client.login(username="admin_user", password="password123")
        response = self.client.post(reverse('portal_settings_phase_delete', args=[pn.id]))
        self.assertRedirects(response, reverse('portal_settings'))
        self.assertFalse(PhaseName.objects.filter(id=pn.id).exists())
        lp.refresh_from_db()
        self.assertEqual(lp.name, 'Foundation')

    def test_unauthorized_user_cannot_add_phase_name(self):
        # Unauthenticated
        response = self.client.post(reverse('portal_settings_phase_add'), {'name': 'Security'})
        self.assertRedirects(response, reverse('login'))

        # Learner
        self.client.login(username="learner_user", password="password123")
        response = self.client.post(reverse('portal_settings_phase_add'), {'name': 'Security'})
        self.assertRedirects(response, reverse('learner_dashboard'))
        self.assertFalse(PhaseName.objects.filter(name='Security').exists())

    def test_unauthorized_user_cannot_edit_phase_name(self):
        pn = PhaseName.objects.create(name='Original')
        self.client.login(username="learner_user", password="password123")
        response = self.client.post(reverse('portal_settings_phase_edit', args=[pn.id]), {'name': 'Hacked'})
        self.assertRedirects(response, reverse('learner_dashboard'))
        pn.refresh_from_db()
        self.assertEqual(pn.name, 'Original')

    def test_unauthorized_user_cannot_delete_phase_name(self):
        pn = PhaseName.objects.create(name='ToDelete')
        self.client.login(username="learner_user", password="password123")
        response = self.client.post(reverse('portal_settings_phase_delete', args=[pn.id]))
        self.assertRedirects(response, reverse('learner_dashboard'))
        self.assertTrue(PhaseName.objects.filter(id=pn.id).exists())

    def test_inline_ajax_add_phase_name_success(self):
        self.client.login(username="admin_user", password="password123")
        response = self.client.post(
            reverse('portal_phase_name_api_add'),
            data=json.dumps({'name': 'Specialization'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('name'), 'Specialization')
        self.assertTrue(PhaseName.objects.filter(name='Specialization').exists())

    def test_inline_ajax_add_blank_name_rejected(self):
        self.client.login(username="admin_user", password="password123")
        response = self.client.post(
            reverse('portal_phase_name_api_add'),
            data=json.dumps({'name': '   '}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get('success'))
        self.assertEqual(data.get('error'), 'Phase name cannot be blank.')

    def test_inline_ajax_add_duplicate_rejected(self):
        PhaseName.objects.create(name='Specialization')
        self.client.login(username="admin_user", password="password123")
        response = self.client.post(
            reverse('portal_phase_name_api_add'),
            data=json.dumps({'name': 'specialization'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get('success'))
        self.assertIn('already exists', data.get('error'))

    def test_inline_ajax_add_get_method_rejected(self):
        self.client.login(username="admin_user", password="password123")
        response = self.client.get(reverse('portal_phase_name_api_add'))
        self.assertEqual(response.status_code, 405)

    def test_learning_phase_form_choices_populated_from_phase_names(self):
        PhaseName.objects.create(name='Beta Phase')
        PhaseName.objects.create(name='Alpha Phase')
        form = LearningPhaseForm()
        choices = [choice[0] for choice in form.fields['name'].widget.choices]
        self.assertIn('', choices)
        self.assertIn('Alpha Phase', choices)
        self.assertIn('Beta Phase', choices)
        # Verify alphabetical ordering
        alpha_idx = choices.index('Alpha Phase')
        beta_idx = choices.index('Beta Phase')
        self.assertLess(alpha_idx, beta_idx)
