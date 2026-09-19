from django.test import TestCase
from content.models import SiteConfig, Subject, Topic

class PassingScoreHierarchyTests(TestCase):
    def setUp(self):
        # Set up LMS default
        self.site_config = SiteConfig.get_solo()
        self.site_config.default_passing_score = 75
        self.site_config.save()

        # Create Subject and Topic without overrides
        self.subject = Subject.objects.create(name='Test Subject', slug='test-subject')
        self.topic = Topic.objects.create(subject=self.subject, name='Test Topic')

    def test_default_passing_score(self):
        # Scenario 3: Neither Topic nor Subject override configured
        self.assertIsNone(self.topic.passing_score_override)
        self.assertIsNone(self.subject.passing_score_override)
        self.assertEqual(self.topic.effective_passing_score, 75)

    def test_subject_override(self):
        # Scenario 2: Topic override not configured + Subject override configured
        self.subject.passing_score_override = 80
        self.subject.save()
        
        self.assertIsNone(self.topic.passing_score_override)
        self.assertEqual(self.subject.passing_score_override, 80)
        self.assertEqual(self.topic.effective_passing_score, 80)

    def test_topic_override(self):
        # Scenario 1: Topic override configured
        self.subject.passing_score_override = 80
        self.subject.save()
        
        self.topic.passing_score_override = 90
        self.topic.save()
        
        self.assertEqual(self.subject.passing_score_override, 80)
        self.assertEqual(self.topic.passing_score_override, 90)
        self.assertEqual(self.topic.effective_passing_score, 90)

    def test_topic_override_no_subject(self):
        # Topic override but no subject override
        self.assertIsNone(self.subject.passing_score_override)
        
        self.topic.passing_score_override = 85
        self.topic.save()
        
        self.assertEqual(self.topic.effective_passing_score, 85)
